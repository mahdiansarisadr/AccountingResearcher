"""Tool: train_model — fit a model on this conversation's upload and log it to MLflow.

Three ways to call it, same tool:

* Omit ``code`` and a capable default pipeline is fit (XGBoost when installed,
  otherwise sklearn histogram gradient boosting). ``model`` can pick a named
  estimator without writing code.
* Pass ``code`` and the agent writes the training script: features, model,
  search, anything the installed libraries can do. ``df``, ``target`` and an
  active MLflow run are injected.
* Pass ``recipe_version`` and a version that already exists runs again,
  unchanged.

Every run executes a recipe version (see :mod:`mleng.core.recipes`). New source
gets the next number and records the ``parent_version`` it was derived from;
identical source re-uses the number it already has. That is what keeps "I tried
something new" distinguishable from "I ran the same thing again".
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import cloudpickle
import mlflow
import numpy as np
import pandas as pd
from langchain.tools import tool
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder, OneHotEncoder, StandardScaler

from ...core import recipes, splits
from ...core.experiments import ERROR_TAG, canonicalize_metrics
from ...core.settings import get_settings
from ...core.workspace import artifact_uri, current, resolve_upload, tracking_uri
from .code_sandbox import TrainingScriptError, TrainingTimeout, run_training_code
from .profile import _load_table

# MLflow's tracking URI is process-global. The worker runs one job at a time,
# but this lock still keeps two overlapping trains from writing into the wrong
# user's store if that ever changes.
_mlflow_lock = threading.Lock()

_STDOUT_CHARS = 8_000
_ERROR_CHARS = 500
_PARAM_CHARS = 480
DEFAULT_SPLIT_SEED = splits.DEFAULT_SEED

# Scores on the locked test rows. Prefixed so they cannot be mistaken for the
# validation metrics the agent selects on, and so ``canonicalize_metrics`` does
# not fold them onto the same axis. They reach the user through the API; they
# are deliberately absent from the leaderboard the agent reads.
LOCKED_PREFIX = "locked_"


def _has_xgboost() -> bool:
    try:
        import xgboost  # noqa: F401

        return True
    except ImportError:
        return False


def _has_lightgbm() -> bool:
    try:
        import lightgbm  # noqa: F401

        return True
    except ImportError:
        return False


def _infer_task(series: pd.Series) -> str:
    if not pd.api.types.is_numeric_dtype(series):
        return "classification"
    values = series.dropna()
    # Counting distinct values misreads a small sample of a continuous target.
    # Fractional values are the giveaway: nobody labels classes 3.5 and 8.5.
    if pd.api.types.is_float_dtype(values) and not (values == values.round()).all():
        return "regression"
    return "regression" if values.nunique() > 10 else "classification"


def _preprocessor(frame: pd.DataFrame) -> ColumnTransformer:
    numeric = [c for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])]
    categorical = [c for c in frame.columns if c not in numeric]
    transformers = []
    if numeric:
        transformers.append(
            (
                "num",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric,
            )
        )
    if categorical:
        transformers.append(
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        (
                            "encode",
                            OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                        ),
                    ]
                ),
                categorical,
            )
        )
    return ColumnTransformer(transformers)


def _named_estimator(task: str, model: str):
    """Build a named estimator. ``auto`` picks XGBoost, then hist GB."""
    name = (model or "auto").strip().lower() or "auto"
    if name == "auto":
        name = "xgboost" if _has_xgboost() else "hist_gb"

    if name == "xgboost":
        if not _has_xgboost():
            raise ValueError("xgboost is not installed")
        from xgboost import XGBClassifier, XGBRegressor

        if task == "classification":
            return (
                XGBClassifier(
                    n_estimators=200,
                    max_depth=4,
                    n_jobs=1,
                    random_state=42,
                    verbosity=0,
                ),
                name,
            )
        return (
            XGBRegressor(
                n_estimators=200,
                max_depth=4,
                n_jobs=1,
                random_state=42,
                verbosity=0,
            ),
            name,
        )

    if name == "lightgbm":
        if not _has_lightgbm():
            raise ValueError("lightgbm is not installed")
        from lightgbm import LGBMClassifier, LGBMRegressor

        if task == "classification":
            return (
                LGBMClassifier(
                    n_estimators=200,
                    max_depth=4,
                    n_jobs=1,
                    random_state=42,
                    verbosity=-1,
                ),
                name,
            )
        return (
            LGBMRegressor(
                n_estimators=200,
                max_depth=4,
                n_jobs=1,
                random_state=42,
                verbosity=-1,
            ),
            name,
        )

    if task == "classification":
        classifiers = {
            "hist_gb": lambda: HistGradientBoostingClassifier(max_depth=6, random_state=42),
            "random_forest": lambda: RandomForestClassifier(
                n_estimators=200, random_state=42, n_jobs=1
            ),
            "logistic": lambda: LogisticRegression(max_iter=500),
        }
        factory = classifiers.get(name)
        if factory is None:
            raise ValueError(
                f"unknown classification model {model!r}. "
                "Use auto, xgboost, lightgbm, hist_gb, random_forest, logistic, "
                "or pass code= to train anything else."
            )
        return factory(), name

    regressors = {
        "hist_gb": lambda: HistGradientBoostingRegressor(max_depth=6, random_state=42),
        "random_forest": lambda: RandomForestRegressor(
            n_estimators=200, random_state=42, n_jobs=1
        ),
        "ridge": lambda: Ridge(),
    }
    factory = regressors.get(name)
    if factory is None:
        raise ValueError(
            f"unknown regression model {model!r}. "
            "Use auto, xgboost, lightgbm, hist_gb, random_forest, ridge, "
            "or pass code= to train anything else."
        )
    return factory(), name


def _encode_classification_target(y: pd.Series) -> tuple[pd.Series, LabelEncoder]:
    encoder = LabelEncoder()
    encoded = pd.Series(encoder.fit_transform(y.astype(str)), index=y.index)
    return encoded, encoder


def _score(task: str, y_true, y_pred, proba=None) -> dict[str, float]:
    """The only place a score is computed.

    Scoring belongs to the harness, not to the script being scored. When each
    run named its own metrics, two runs could report numbers that were never on
    the same axis, and comparing them was meaningless.
    """
    truth = np.asarray(y_true)
    preds = np.asarray(y_pred)
    if len(preds) != len(truth):
        raise ValueError(
            f"predictions have length {len(preds)} but there are {len(truth)} rows to score"
        )

    if task == "regression":
        preds = preds.astype(float)
        return {
            "r2": float(r2_score(truth, preds)),
            "mae": float(mean_absolute_error(truth, preds)),
            "rmse": float(mean_squared_error(truth, preds) ** 0.5),
        }

    # A script may return labels in whatever form it trained on; compare as text
    # so "1" and 1 are not counted as a miss.
    if truth.dtype != preds.dtype:
        truth = truth.astype(str)
        preds = preds.astype(str)
    metrics = {
        "accuracy": float(accuracy_score(truth, preds)),
        "f1_weighted": float(f1_score(truth, preds, average="weighted")),
    }
    if proba is not None:
        array = np.asarray(proba)
        if array.ndim == 2 and array.shape[1] == 2 and len(np.unique(truth)) == 2:
            try:
                metrics["roc_auc"] = float(roc_auc_score(truth, array[:, 1]))
            except ValueError:
                pass
    return metrics


def _locked(metrics: dict[str, float]) -> dict[str, float]:
    return {f"{LOCKED_PREFIX}{name}": value for name, value in metrics.items()}


def _try_log_model(log_model, estimator: Any, **extra: Any) -> None:
    try:
        log_model(estimator, name="model", **extra)
    except TypeError:
        log_model(estimator, artifact_path="model", **extra)


def _log_fitted(estimator: Any) -> None:
    module = type(estimator).__module__
    if module.startswith("xgboost"):
        _try_log_model(mlflow.xgboost.log_model, estimator)
        return
    if module.startswith("lightgbm"):
        _try_log_model(mlflow.lightgbm.log_model, estimator)
        return
    _try_log_model(
        mlflow.sklearn.log_model, estimator, serialization_format="cloudpickle"
    )


@contextmanager
def _mlflow_run(ctx, run_name: str) -> Iterator[tuple[Any, str]]:
    uri = tracking_uri(ctx.user_id, data_dir=ctx.data_dir)
    with _mlflow_lock:
        mlflow.set_tracking_uri(uri)
        if mlflow.get_experiment_by_name(ctx.thread_id) is None:
            mlflow.create_experiment(
                ctx.thread_id,
                artifact_location=artifact_uri(ctx.user_id, data_dir=ctx.data_dir),
            )
        mlflow.set_experiment(ctx.thread_id)
        with mlflow.start_run(run_name=run_name) as run:
            yield run, uri


@contextmanager
def _record_failure() -> Iterator[None]:
    """Put the reason a run died on the run itself.

    Tool results never reach the next turn, so without this the agent rewrites
    the same broken transform and watches it break the same way.
    """
    try:
        yield
    except BaseException as exc:
        # A script error already names the exception the script raised; naming
        # the wrapper too would read "RuntimeError: ValueError: ...".
        detail = str(exc) if isinstance(exc, TrainingScriptError) else f"{type(exc).__name__}: {exc}"
        mlflow.set_tag(ERROR_TAG, detail[:_ERROR_CHARS])
        raise


def _run_name(version: int, model: str, hypothesis: str | None) -> str:
    base = (model or "model").strip() or "model"
    extra = " ".join((hypothesis or "").split())
    name = f"v{version} {base}"
    return (f"{name} — {extra}" if extra else name)[:80]


def _estimator_params(estimator: Any) -> str:
    """The estimator's resolved hyperparameters, so a run says what it actually ran."""
    try:
        params = estimator.get_params()
    except (AttributeError, TypeError):
        return ""
    readable = {key: str(value) for key, value in sorted(params.items())}
    return json.dumps(readable, sort_keys=True)[:_PARAM_CHARS]


def _stamp_run(
    *,
    task: str | None,
    mode: str,
    model: str | None,
    hypothesis: str | None,
) -> None:
    mlflow.set_tags(
        {
            "mleng.mode": mode,
            "mleng.task": task or "",
            "mleng.model": model or "",
            "mleng.hypothesis": (hypothesis or "")[:200],
        }
    )


def _load_frame(target: str, filename: str | None) -> tuple[pd.DataFrame, Any]:
    ctx = current()
    path = resolve_upload(ctx.user_id, ctx.thread_id, filename, data_dir=ctx.data_dir)
    frame = _load_table(path)
    if target not in frame.columns:
        raise ValueError(
            f"target column {target!r} is not in this file. "
            f"Columns: {list(map(str, frame.columns))}"
        )
    if frame.drop(columns=[target]).empty:
        raise ValueError("the file has no feature columns besides the target.")
    if frame[target].nunique(dropna=True) < 2:
        raise ValueError("the target has fewer than two distinct values.")
    return frame, path


def _default_train(
    frame: pd.DataFrame,
    path,
    target: str,
    task_name: str,
    model: str | None,
    hypothesis: str | None,
    ctx,
    *,
    parent_version: int | None = None,
    split_seed: int = DEFAULT_SPLIT_SEED,
) -> dict[str, Any]:
    y = frame[target]
    x = frame.drop(columns=[target])
    encoder = None
    if task_name == "classification":
        y, encoder = _encode_classification_target(y)

    try:
        estimator, model_name = _named_estimator(task_name, model or "auto")
    except ValueError as exc:
        return {"error": str(exc)}

    pipe = Pipeline([("prep", _preprocessor(x)), ("model", estimator)])
    spec = recipes.default_spec(
        task=task_name,
        model=model_name,
        target=target,
        estimator_params=_estimator_params(estimator),
    )
    allocation = recipes.allocate(
        ctx.user_id,
        ctx.thread_id,
        kind=recipes.DEFAULT,
        spec=spec,
        parent=parent_version,
        data_dir=ctx.data_dir,
    )

    split = splits.make_split(frame, target, seed=split_seed)
    x_train, y_train = x.iloc[split.train], y.iloc[split.train]
    x_valid, y_valid = x.iloc[split.valid], y.iloc[split.valid]

    metrics: dict[str, float]
    with _mlflow_run(ctx, _run_name(allocation.version, model_name, hypothesis)) as (run, uri):
        recipes.stamp(allocation)
        mlflow.log_params(
            {
                "file": path.name,
                "mode": "default",
                "n_rows": str(len(frame)),
                "n_features": str(x.shape[1]),
                **split.as_params(),
                **spec,
            }
        )
        if hypothesis:
            mlflow.log_param("hypothesis", hypothesis[:200])
        if encoder is not None:
            mlflow.log_param("n_classes", str(len(encoder.classes_)))
        _stamp_run(
            task=task_name, mode="default", model=model_name, hypothesis=hypothesis
        )
        started = time.monotonic()
        with _record_failure():
            pipe.fit(x_train, y_train)
            metrics = canonicalize_metrics(
                _score(task_name, y_valid, pipe.predict(x_valid), _proba(pipe, x_valid))
            )
            if split.has_test:
                locked = _score(
                    task_name,
                    y.iloc[split.test],
                    pipe.predict(x.iloc[split.test]),
                    _proba(pipe, x.iloc[split.test]),
                )
                metrics.update(_locked(locked))
        seconds = time.monotonic() - started
        mlflow.log_metric("train_seconds", seconds)
        if metrics:
            mlflow.log_metrics(metrics)
        _log_fitted(pipe)
        run_id = run.info.run_id
        experiment_id = run.info.experiment_id

    return {
        "file": path.name,
        "target": target,
        "task": task_name,
        "model": model_name,
        "mode": "default",
        "recipe_version": allocation.version,
        "recipe_parent": allocation.parent,
        "reused_recipe": allocation.reused,
        "split_seed": split_seed,
        "n_train": int(len(split.train)),
        "n_valid": int(len(split.valid)),
        "seconds": round(seconds, 2),
        "metrics": _visible(metrics),
        "mlflow_run_id": run_id,
        "mlflow_experiment_id": experiment_id,
        "tracking_uri": uri,
    }


def _proba(estimator, frame):
    if not hasattr(estimator, "predict_proba"):
        return None
    try:
        return estimator.predict_proba(frame)
    except Exception:  # noqa: BLE001 - probabilities are a bonus, not a contract
        return None


def _visible(metrics: dict[str, float]) -> dict[str, float]:
    """What the agent is allowed to see: validation only.

    The locked test rows are scored on every run so the user gets an honest
    number at the end, but they must play no part in choosing between versions.
    Handing them back here is how they would.
    """
    return {
        name: value
        for name, value in metrics.items()
        if not name.startswith(LOCKED_PREFIX)
    }


def _code_train(
    frame: pd.DataFrame,
    path,
    target: str,
    code: str,
    hypothesis: str | None,
    ctx,
    *,
    task: str | None = None,
    parent_version: int | None = None,
    split_seed: int = DEFAULT_SPLIT_SEED,
) -> dict[str, Any]:
    source = recipes.normalise_source(code)
    allocation = recipes.allocate(
        ctx.user_id,
        ctx.thread_id,
        kind=recipes.CODE,
        source=source,
        spec={"target": target},
        parent=parent_version,
        data_dir=ctx.data_dir,
    )
    task_name = (task or _infer_task(frame[target])).strip().lower()
    split = splits.make_split(frame, target, seed=split_seed)
    budget = get_settings().train_budget_seconds

    label = _run_name(allocation.version, "code", hypothesis)
    with _mlflow_run(ctx, label) as (run, uri):
        recipes.stamp(allocation, source=source)
        mlflow.log_params(
            {
                "file": path.name,
                "target": target,
                "mode": "code",
                "task": task_name,
                "n_rows": str(len(frame)),
                "n_features": str(int(frame.shape[1] - 1)),
                "budget_seconds": str(budget),
                **split.as_params(),
            }
        )
        if hypothesis:
            mlflow.log_param("hypothesis", hypothesis[:200])
        _stamp_run(task=task_name, mode="code", model="code", hypothesis=hypothesis)

        with _record_failure():
            result = run_training_code(
                source,
                train=frame.iloc[split.train],
                valid=frame.iloc[split.valid],
                test=frame.iloc[split.test].drop(columns=[target])
                if split.has_test
                else None,
                target=target,
                split_seed=split_seed,
                budget_seconds=budget,
            )
            if result.error:
                raise TrainingScriptError(result.error)
            metrics = canonicalize_metrics(
                _score(
                    task_name,
                    frame[target].iloc[split.valid],
                    result.valid_pred,
                    result.valid_proba,
                )
            )
            if split.has_test and result.test_pred is not None:
                metrics.update(
                    _locked(
                        _score(task_name, frame[target].iloc[split.test], result.test_pred)
                    )
                )

        mlflow.log_metric("train_seconds", result.seconds)
        if result.params:
            mlflow.log_params({f"code.{k}": v for k, v in result.params.items()})
        if metrics:
            mlflow.log_metrics(metrics)

        stdout = result.stdout
        logged_model = False
        if result.model is not None:
            try:
                _log_fitted(cloudpickle.loads(result.model))
                logged_model = True
            except Exception as exc:  # noqa: BLE001 - reported, run still useful
                stdout = (stdout + f"\n[could not log model: {exc}]").strip()

        run_id = run.info.run_id
        experiment_id = run.info.experiment_id

    return {
        "file": path.name,
        "target": target,
        "task": task_name,
        "mode": "code",
        "recipe_version": allocation.version,
        "recipe_parent": allocation.parent,
        "reused_recipe": allocation.reused,
        "split_seed": split_seed,
        "n_train": int(len(split.train)),
        "n_valid": int(len(split.valid)),
        "seconds": round(result.seconds, 2),
        "budget_seconds": budget,
        "metrics": _visible(metrics),
        "estimator": result.model_repr,
        "logged_model": logged_model,
        "stdout": stdout[-_STDOUT_CHARS:],
        "mlflow_run_id": run_id,
        "mlflow_experiment_id": experiment_id,
        "tracking_uri": uri,
    }


def train_model_impl(
    target: str,
    filename: str | None = None,
    task: str | None = None,
    model: str | None = None,
    code: str | None = None,
    hypothesis: str | None = None,
    parent_version: int | None = None,
    recipe_version: int | None = None,
    split_seed: int = DEFAULT_SPLIT_SEED,
) -> str:
    ctx = current()
    source = (code or "").strip()
    note = (hypothesis or "").strip() or None

    if recipe_version is not None:
        if source:
            return (
                "ERROR: pass code= to write a new version or recipe_version= to run an "
                "existing one, not both. To change v"
                f"{recipe_version}, pass code= with parent_version={recipe_version}."
            )
        if parent_version is not None:
            return (
                "ERROR: a re-run does not set lineage. Drop parent_version, or drop "
                "recipe_version and pass code= for a new version."
            )
        try:
            replay = recipes.load(
                ctx.user_id, ctx.thread_id, recipe_version, data_dir=ctx.data_dir
            )
        except recipes.RecipeError as exc:
            return f"ERROR: {exc}"
        if replay.kind == recipes.CODE:
            if not replay.source:
                return (
                    f"ERROR: v{recipe_version} has no stored source; it predates "
                    "recipe versioning. Write the script out in full instead."
                )
            source = replay.source
        target = replay.spec.get("target") or target
        task = replay.spec.get("task") or task
        model = replay.spec.get("model") or model

    try:
        frame, path = _load_frame(target, filename)
    except (ValueError, FileNotFoundError) as exc:
        return f"ERROR: {exc}"

    if source:
        try:
            result = _code_train(
                frame,
                path,
                target,
                source,
                note,
                ctx,
                task=task,
                parent_version=parent_version,
                split_seed=split_seed,
            )
        except (TrainingTimeout, ValueError, RuntimeError, recipes.RecipeError) as exc:
            # The run is already recorded as failed with this reason attached;
            # the agent gets it as text so it can fix the script and try again.
            return f"ERROR: {exc}"
        return json.dumps(result)

    inferred = _infer_task(frame[target])
    task_name = (task or inferred).strip().lower()
    if task_name not in {"classification", "regression"}:
        return "ERROR: task must be 'classification' or 'regression'."

    try:
        result = _default_train(
            frame,
            path,
            target,
            task_name,
            model,
            note,
            ctx,
            parent_version=parent_version,
            split_seed=split_seed,
        )
    except recipes.RecipeError as exc:
        return f"ERROR: {exc}"
    if "error" in result:
        return f"ERROR: {result['error']}"
    return json.dumps(result)


@tool
def train_model(
    target: str,
    filename: str | None = None,
    task: str | None = None,
    model: str | None = None,
    code: str | None = None,
    hypothesis: str | None = None,
    parent_version: int | None = None,
    recipe_version: int | None = None,
    split_seed: int = DEFAULT_SPLIT_SEED,
) -> str:
    """Train a model on the dataset uploaded to this conversation and log it to MLflow.

    Every run executes a numbered recipe version. Read the version tree in the
    system prompt first, then pick one of three moves:

    * Build on existing work: pass the whole script as ``code`` plus
      ``parent_version`` = the version you started from. Call ``get_recipe`` to
      read that version's source and edit it, rather than writing from memory.
    * Re-run something unchanged: pass ``recipe_version`` on its own. Use a new
      ``split_seed`` to find out whether a difference between two versions is
      real or just noise.
    * Start from a strong default: omit ``code`` entirely.

    Never refuse a modeling request because a library is missing from a short
    list. If the user wants XGBoost, LightGBM, custom features, or hyperparameter
    search, pass Python as ``code``.

    Args:
        target: Column to predict. Use the exact name from profile_dataset.
        filename: Which uploaded file to use. Omit to use the latest upload.
        task: "classification" or "regression". Omit to infer from the target.
            Ignored when ``code`` is set.
        model: Named default estimator when ``code`` is omitted: "auto" (XGBoost
            if available, else hist_gb), "xgboost", "lightgbm", "hist_gb",
            "random_forest", "logistic" (classification), "ridge" (regression).
        code: The complete Python script, not a fragment or a diff. It runs in
            a separate process under a fixed compute budget, and it does not
            score itself — the harness does, on rows the script never sees.

            Injected names: ``train`` and ``valid`` (DataFrames including the
            target column), ``target``, ``split_seed``, ``features(frame)``
            (drops the target), ``pd``, ``np``, ``sklearn``, ``xgboost``,
            ``lightgbm``, ``optuna``, ``train_test_split``.

            Fit on ``train``. Use ``valid`` for early stopping or model
            selection if you want it. Then assign either ``model`` (anything
            with ``.predict``) or a ``predict(frame)`` function. Whatever you
            assign is called with a *raw* feature frame, so feature engineering
            has to live inside a Pipeline or inside ``predict`` — a transform
            applied only to ``train`` will not be reproduced at scoring time.
            Optionally assign a ``params`` dict of anything worth recording,
            such as the best trial of a search. Do not compute metrics; do not
            import os or subprocess; do not open files.
        hypothesis: One sentence — what this version changes versus its parent.
        parent_version: The version this code was derived from. Set it whenever
            you are editing earlier work, so the history stays a tree.
        recipe_version: Run this existing version again, unchanged. Cannot be
            combined with ``code`` or ``parent_version``.
        split_seed: Seed for the train/valid/test cut. Leave at 42 to stay
            comparable; vary it only to measure how noisy a version's score is.

    Returns validation metrics, the recipe version this run executed, whether
    that version already existed, how long training took against its budget,
    the MLflow run id, and (in code mode) captured stdout.
    """
    try:
        return train_model_impl(
            target,
            filename=filename,
            task=task,
            model=model,
            code=code,
            hypothesis=hypothesis,
            parent_version=parent_version,
            recipe_version=recipe_version,
            split_seed=split_seed,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        return f"ERROR: {exc}"
