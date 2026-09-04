"""This conversation's MLflow experiment: list it, format it, keep metrics comparable.

The worker builds a new agent every turn, so LangGraph memory cannot carry
scores forward. The durable log is the per-user MLflow store, experiment name
= thread id. This module is how the prompt, the API, and the UI all read the
same history.

Runs are grouped by the recipe version they executed (see :mod:`.recipes`), so
the prompt shows a tree of *ideas* with their scores rather than a flat list of
executions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from mlflow.tracking import MlflowClient

from . import recipes
from .workspace import tracking_uri

# Holdout names the prompt and the UI compare. Aliases are folded into these
# so a code-mode run that logged ``r2_test`` still sits on the same axis as a
# default run that logged ``r2``.
_METRIC_ALIASES = {
    "r2_score": "r2",
    "r2_test": "r2",
    "test_r2": "r2",
    "mae_test": "mae",
    "test_mae": "mae",
    "rmse_test": "rmse",
    "test_rmse": "rmse",
    "accuracy_test": "accuracy",
    "f1": "f1_weighted",
    "f1_test": "f1_weighted",
    "f1_weighted_test": "f1_weighted",
}

_PROMPT_ROWS = 12

# Why a run failed, kept on the run so the next attempt does not rediscover it.
ERROR_TAG = "mleng.error"
_ERROR_CHARS = 300


@dataclass(frozen=True)
class ExperimentRun:
    run_id: str
    name: str
    status: str
    started_at: datetime | None
    metrics: dict[str, float]
    params: dict[str, str]
    model: str | None
    task: str | None
    hypothesis: str | None
    primary_metric: str | None
    primary_value: float | None
    recipe_version: int | None = None
    recipe_parent: int | None = None
    recipe_kind: str | None = None
    reused: bool = False
    split_seed: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class VersionSummary:
    """Every run of one recipe version, collapsed into what the prompt needs."""

    version: int
    parent: int | None
    kind: str
    note: str | None
    runs: int
    best_metric: str | None
    best_value: float | None
    scores: tuple[float, ...] = ()
    failed: int = 0
    last_error: str | None = None

    @property
    def spread(self) -> float | None:
        """Range across runs of the same version — the noise floor for comparisons."""
        if len(self.scores) < 2:
            return None
        return max(self.scores) - min(self.scores)


def canonicalize_metrics(raw: Mapping[str, Any]) -> dict[str, float]:
    """Finite numbers only, with holdout aliases folded onto r2/mae/rmse/accuracy."""
    finite: dict[str, float] = {}
    for key, value in raw.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        finite[str(key)] = number
    out = dict(finite)
    for alias, canonical in _METRIC_ALIASES.items():
        if canonical not in out and alias in finite:
            out[canonical] = finite[alias]
    return out


def primary_score(metrics: Mapping[str, float]) -> tuple[str | None, float | None]:
    """The one number used to pick a 'best' run.

    Accuracy for classification, r2 for regression. Missing both, nothing is
    comparable and the leaderboard will not crown a winner.
    """
    if "accuracy" in metrics:
        return "accuracy", float(metrics["accuracy"])
    if "r2" in metrics:
        return "r2", float(metrics["r2"])
    return None, None


def list_experiment_runs(
    user_id: str,
    thread_id: str,
    *,
    data_dir: Path | None = None,
    limit: int = 40,
) -> list[ExperimentRun]:
    """Runs on this thread's experiment, newest first. Empty if none exist."""
    uri = tracking_uri(user_id, data_dir=data_dir)
    client = MlflowClient(tracking_uri=uri)
    experiment = client.get_experiment_by_name(str(thread_id))
    if experiment is None:
        return []
    found = client.search_runs(
        [experiment.experiment_id],
        order_by=["start_time DESC"],
        max_results=limit,
    )
    rows: list[ExperimentRun] = []
    for run in found:
        params = dict(run.data.params or {})
        tags = dict(run.data.tags or {})
        metrics = canonicalize_metrics(run.data.metrics or {})
        metric_name, metric_value = primary_score(metrics)
        started = None
        if run.info.start_time:
            started = datetime.fromtimestamp(
                run.info.start_time / 1000, tz=timezone.utc
            )
        rows.append(
            ExperimentRun(
                run_id=run.info.run_id,
                name=(run.info.run_name or "").strip() or run.info.run_id[:8],
                status=(run.info.status or "").lower(),
                started_at=started,
                metrics=metrics,
                params=params,
                model=params.get("model") or tags.get("mleng.model") or None,
                task=params.get("task") or tags.get("mleng.task") or None,
                hypothesis=(
                    (tags.get("mleng.hypothesis") or params.get("hypothesis") or "").strip()
                    or None
                ),
                primary_metric=metric_name,
                primary_value=metric_value,
                recipe_version=_as_int(tags.get(recipes.VERSION_TAG)),
                recipe_parent=_as_int(tags.get(recipes.PARENT_TAG)),
                recipe_kind=(tags.get(recipes.KIND_TAG) or "").strip() or None,
                reused=(tags.get(recipes.REUSED_TAG) or "").strip().lower() == "true",
                split_seed=params.get("split_seed"),
                error=(tags.get(ERROR_TAG) or "").strip() or None,
            )
        )
    return rows


def summarise_versions(rows: list[ExperimentRun]) -> dict[int, VersionSummary]:
    """Collapse runs onto the version each one executed."""
    grouped: dict[int, list[ExperimentRun]] = {}
    for row in rows:
        if row.recipe_version is not None:
            grouped.setdefault(row.recipe_version, []).append(row)

    summaries: dict[int, VersionSummary] = {}
    for version, runs in sorted(grouped.items()):
        # ``rows`` is newest first, so the first entry describes the version as
        # it was last run, and the last entry is where its lineage was set.
        newest, oldest = runs[0], runs[-1]
        scored = [
            run
            for run in runs
            if run.status == "finished" and run.primary_value is not None
        ]
        best = max(scored, key=lambda run: run.primary_value or float("-inf"), default=None)
        failures = [run for run in runs if run.status == "failed"]
        summaries[version] = VersionSummary(
            version=version,
            parent=next(
                (run.recipe_parent for run in reversed(runs) if run.recipe_parent is not None),
                None,
            ),
            kind=newest.recipe_kind or oldest.recipe_kind or recipes.CODE,
            note=next((run.hypothesis for run in runs if run.hypothesis), None),
            runs=len(runs),
            best_metric=best.primary_metric if best else None,
            best_value=best.primary_value if best else None,
            scores=tuple(
                run.primary_value for run in scored if run.primary_value is not None
            ),
            failed=len(failures),
            last_error=next((run.error for run in failures if run.error), None),
        )
    return summaries


def format_leaderboard(
    user_id: str,
    thread_id: str,
    *,
    data_dir: Path | None = None,
) -> str:
    """The system prompt's view: the recipe version tree and what each one scored."""
    rows = list_experiment_runs(user_id, thread_id, data_dir=data_dir)
    if not rows:
        return (
            "No training runs on this conversation yet. The first train_model "
            "call creates recipe v1. Log the holdout metrics the harness expects "
            "(r2/mae/rmse or accuracy/f1_weighted) so later turns can compare."
        )

    versions = summarise_versions(rows)
    best = _best_version(versions)

    lines = [
        "Recipe versions on this conversation. A version is one training script; "
        "runs execute it. Newest source is always readable with get_recipe."
    ]
    if best is not None:
        lines.append(
            f"Best holdout so far: v{best.version} "
            f"{best.best_metric}={_fmt(best.best_value)}."
        )
    if versions:
        lines.append("")
        lines.extend(_tree_lines(versions, best))

    loose = [row for row in rows if row.recipe_version is None]
    if loose:
        lines.append("")
        lines.append("Runs from before versioning (no recipe to build on):")
        for row in loose[:_PROMPT_ROWS]:
            score = (
                f"{row.primary_metric}={_fmt(row.primary_value)}"
                if row.primary_metric and row.primary_value is not None
                else "no comparable holdout metric"
            )
            lines.append(f"- {row.run_id[:8]} {row.status} {row.model or '—'} {score}")

    lines.append("")
    lines.append(
        "Scores above are on the validation rows, which every version shares. "
        "Build on this: to change the approach, write the whole script and pass "
        "parent_version = the version you started from. Identical source re-uses "
        "its version rather than forking a new one. A gain smaller than the "
        "spread already seen across runs of one version is noise, not progress."
    )
    return "\n".join(lines)


def _best_version(versions: Mapping[int, VersionSummary]) -> VersionSummary | None:
    scored = [row for row in versions.values() if row.best_value is not None]
    if not scored:
        return None
    return max(scored, key=lambda row: row.best_value or float("-inf"))


def _tree_lines(
    versions: Mapping[int, VersionSummary], best: VersionSummary | None
) -> list[str]:
    """Depth-first render, so a version sits under the one it was derived from."""
    children: dict[int | None, list[int]] = {}
    for version, row in versions.items():
        parent = row.parent if row.parent in versions else None
        children.setdefault(parent, []).append(version)

    lines: list[str] = []
    rendered: set[int] = set()

    def emit(version: int, depth: int) -> None:
        if version in rendered:
            return
        row = versions[version]
        rendered.add(version)
        indent = "  " * depth
        marker = "  <- best" if best is not None and best.version == version else ""
        lines.append(f"{indent}v{version} [{row.kind}] {_score_text(row)}{marker}")
        detail = _detail_text(row)
        if detail:
            lines.append(f"{indent}   {detail}")
        for child in sorted(children.get(version, ())):
            emit(child, depth + 1)

    for root in sorted(children.get(None, ())):
        emit(root, 0)
    # A cycle in the parent tags would otherwise drop versions silently.
    for version in sorted(set(versions) - rendered):
        emit(version, 0)
    return lines


def _score_text(row: VersionSummary) -> str:
    if row.best_value is None:
        return f"no comparable holdout metric ({_runs_text(row)})"
    text = f"{row.best_metric}={_fmt(row.best_value)} ({_runs_text(row)}"
    spread = row.spread
    if spread is not None:
        text += f", spread {_fmt(spread)}"
    return text + ")"


def _runs_text(row: VersionSummary) -> str:
    text = f"{row.runs} run" + ("s" if row.runs != 1 else "")
    if row.failed:
        text += f", {row.failed} failed"
    return text


def _detail_text(row: VersionSummary) -> str:
    parts = []
    if row.note:
        parts.append(row.note)
    if row.last_error:
        parts.append(f"FAILED: {row.last_error[:_ERROR_CHARS]}")
    return " — ".join(parts)


def _as_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.4g}"
