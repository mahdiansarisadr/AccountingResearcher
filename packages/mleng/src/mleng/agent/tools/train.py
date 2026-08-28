"""Tool: train_model — fit a sklearn pipeline and log it to this user's MLflow."""

from __future__ import annotations

import json
import threading

import mlflow
import pandas as pd
from langchain.tools import tool
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ...core.workspace import artifact_uri, current, resolve_upload, tracking_uri
from .profile import _load_table

# MLflow's tracking URI is process-global. The worker runs one job at a time,
# but this lock still keeps two overlapping trains from writing into the wrong
# user's store if that ever changes.
_mlflow_lock = threading.Lock()

_CLASSIFIERS = {
    "logistic": lambda: LogisticRegression(max_iter=500),
    "random_forest": lambda: RandomForestClassifier(n_estimators=100, random_state=42),
}
_REGRESSORS = {
    "ridge": lambda: Ridge(),
    "random_forest": lambda: RandomForestRegressor(n_estimators=100, random_state=42),
}


def _infer_task(series: pd.Series) -> str:
    if pd.api.types.is_numeric_dtype(series) and series.nunique(dropna=True) > 10:
        return "regression"
    return "classification"


def _estimator(task: str, model: str):
    if task == "classification":
        factory = _CLASSIFIERS.get(model) or _CLASSIFIERS["logistic"]
        return factory(), "logistic" if model not in _CLASSIFIERS else model
    factory = _REGRESSORS.get(model) or _REGRESSORS["ridge"]
    return factory(), "ridge" if model not in _REGRESSORS else model


def _preprocessor(frame: pd.DataFrame) -> ColumnTransformer:
    numeric = [c for c in frame.columns if pd.api.types.is_numeric_dtype(frame[c])]
    categorical = [c for c in frame.columns if c not in numeric]
    return ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="median")),
                        ("scale", StandardScaler()),
                    ]
                ),
                numeric,
            ),
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
            ),
        ]
    )


def train_model_impl(
    target: str,
    filename: str | None = None,
    task: str | None = None,
    model: str | None = None,
) -> str:
    ctx = current()
    path = resolve_upload(ctx.user_id, ctx.thread_id, filename, data_dir=ctx.data_dir)
    frame = _load_table(path)
    if target not in frame.columns:
        return (
            "ERROR: target column "
            f"{target!r} is not in this file. Columns: {list(map(str, frame.columns))}"
        )

    y = frame[target]
    x = frame.drop(columns=[target])
    if x.empty:
        return "ERROR: the file has no feature columns besides the target."
    if y.nunique(dropna=True) < 2:
        return "ERROR: the target has fewer than two distinct values."

    inferred = _infer_task(y)
    task_name = (task or inferred).strip().lower()
    if task_name not in {"classification", "regression"}:
        return "ERROR: task must be 'classification' or 'regression'."

    estimator, model_name = _estimator(task_name, (model or "").strip().lower())
    pipe = Pipeline(
        [("prep", _preprocessor(x)), ("model", estimator)]
    )

    split_kwargs: dict = {"test_size": 0.2, "random_state": 42}
    try:
        if task_name == "classification":
            x_train, x_test, y_train, y_test = train_test_split(
                x, y, stratify=y, **split_kwargs
            )
        else:
            x_train, x_test, y_train, y_test = train_test_split(x, y, **split_kwargs)
    except ValueError:
        x_train, x_test, y_train, y_test = train_test_split(x, y, **split_kwargs)

    uri = tracking_uri(ctx.user_id, data_dir=ctx.data_dir)
    with _mlflow_lock:
        mlflow.set_tracking_uri(uri)
        if mlflow.get_experiment_by_name(ctx.thread_id) is None:
            mlflow.create_experiment(
                ctx.thread_id,
                artifact_location=artifact_uri(ctx.user_id, data_dir=ctx.data_dir),
            )
        mlflow.set_experiment(ctx.thread_id)

        with mlflow.start_run(run_name=path.name) as run:
            pipe.fit(x_train, y_train)
            preds = pipe.predict(x_test)
            metrics: dict[str, float] = {}
            if task_name == "classification":
                metrics["accuracy"] = float(accuracy_score(y_test, preds))
                metrics["f1_weighted"] = float(
                    f1_score(y_test, preds, average="weighted")
                )
                if hasattr(pipe, "predict_proba") and y_test.nunique() == 2:
                    proba = pipe.predict_proba(x_test)[:, 1]
                    metrics["roc_auc"] = float(roc_auc_score(y_test, proba))
            else:
                metrics["r2"] = float(r2_score(y_test, preds))
                metrics["mae"] = float(mean_absolute_error(y_test, preds))

            mlflow.log_params(
                {
                    "file": path.name,
                    "target": target,
                    "task": task_name,
                    "model": model_name,
                    "n_rows": len(frame),
                    "n_features": x.shape[1],
                }
            )
            mlflow.log_metrics(metrics)
            try:
                mlflow.sklearn.log_model(
                    pipe,
                    name="model",
                    serialization_format="cloudpickle",
                )
            except TypeError:
                mlflow.sklearn.log_model(
                    pipe,
                    artifact_path="model",
                    serialization_format="cloudpickle",
                )
            run_id = run.info.run_id
            experiment_id = run.info.experiment_id

    return json.dumps(
        {
            "file": path.name,
            "target": target,
            "task": task_name,
            "model": model_name,
            "n_train": int(len(x_train)),
            "n_test": int(len(x_test)),
            "metrics": metrics,
            "mlflow_run_id": run_id,
            "mlflow_experiment_id": experiment_id,
            "tracking_uri": uri,
        }
    )


@tool
def train_model(
    target: str,
    filename: str | None = None,
    task: str | None = None,
    model: str | None = None,
) -> str:
    """Train a model on the dataset uploaded to this conversation and log it to MLflow.

    Args:
        target: Column to predict. Use the exact name from profile_dataset.
        filename: Which uploaded file to use. Omit to use the latest upload.
        task: "classification" or "regression". Omit to infer from the target.
        model: "logistic" or "random_forest" for classification; "ridge" or
            "random_forest" for regression. Omit for a simple default.

    Returns metrics on a held-out 20% split and the MLflow run id. After this
    succeeds, use the MLflow tools to inspect the run if the user asks.
    """
    try:
        return train_model_impl(target, filename=filename, task=task, model=model)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        return f"ERROR: {exc}"
