"""The shape of a search, from the first run to the last.

The version tree says what was tried. This says whether any of it worked: the
score in the order it actually happened, the best at each point, and how much
of the total gain sits above the noise the search measured on itself.

A search that improves r2 from 0.05 to 0.24 has done something. A search that
improves it from 0.240 to 0.244, when re-running one version twice moves it by
0.01, has done nothing and should say so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .experiments import (
    ExperimentRun,
    VersionSummary,
    list_experiment_runs,
    summarise_versions,
)

# A long session can log a lot of runs; the trajectory wants all of them.
_SCAN_LIMIT = 500
_ROWS_IN_TEXT = 30


@dataclass(frozen=True)
class Step:
    """One run, in the order it happened."""

    order: int
    version: int | None
    run_id: str
    at: datetime | None
    value: float | None
    best_so_far: float | None
    improved: bool
    gain: float | None
    note: str | None
    failed: bool
    error: str | None
    seconds: float | None


@dataclass(frozen=True)
class Progress:
    metric: str | None
    steps: tuple[Step, ...]
    first: float | None
    best: float | None
    best_version: int | None
    total_gain: float | None
    versions: int
    runs: int
    failed: int
    noise: float | None
    seconds: float

    @property
    def improved(self) -> bool:
        """Did the search beat where it started by more than its own noise?"""
        if self.total_gain is None:
            return False
        if self.noise is None:
            return self.total_gain > 0
        return self.total_gain > self.noise

    @property
    def curve(self) -> tuple[float, ...]:
        """Best-so-far at each scored step, for plotting."""
        return tuple(
            step.best_so_far for step in self.steps if step.best_so_far is not None
        )


def summarise_progress(
    user_id: str,
    thread_id: str,
    *,
    data_dir: Path | None = None,
) -> Progress:
    """Walk this conversation's runs oldest first and track the best so far."""
    rows = list_experiment_runs(user_id, thread_id, data_dir=data_dir, limit=_SCAN_LIMIT)
    ordered = sorted(rows, key=_when)
    metric = next((row.primary_metric for row in ordered if row.primary_metric), None)

    steps: list[Step] = []
    best: float | None = None
    first: float | None = None
    for position, row in enumerate(ordered, start=1):
        value = row.primary_value if row.primary_metric == metric else None
        gain = None
        improved = False
        if value is not None:
            if first is None:
                first = value
            if best is None or value > best:
                gain = None if best is None else value - best
                best = value
                improved = True
        steps.append(
            Step(
                order=position,
                version=row.recipe_version,
                run_id=row.run_id,
                at=row.started_at,
                value=value,
                best_so_far=best,
                improved=improved,
                gain=gain,
                note=row.hypothesis,
                failed=row.status == "failed",
                error=row.error,
                seconds=row.metrics.get("train_seconds"),
            )
        )

    versions = summarise_versions(rows)
    best_version = None
    if best is not None:
        best_version = next(
            (
                summary.version
                for summary in versions.values()
                if summary.best_value == best
            ),
            None,
        )

    return Progress(
        metric=metric,
        steps=tuple(steps),
        first=first,
        best=best,
        best_version=best_version,
        total_gain=None if (first is None or best is None) else best - first,
        versions=len(versions),
        runs=len(rows),
        failed=sum(1 for row in rows if row.status == "failed"),
        noise=_noise(versions),
        seconds=sum(row.metrics.get("train_seconds", 0.0) for row in rows),
    )


def _when(row: ExperimentRun) -> float:
    return row.started_at.timestamp() if row.started_at else 0.0


def _noise(versions: dict[int, VersionSummary]) -> float | None:
    """The largest gap seen between two runs of the *same* version.

    Running one script twice should give one answer. However far apart those
    answers land is the resolution of every comparison in the search.
    """
    spreads = [
        summary.spread for summary in versions.values() if summary.spread is not None
    ]
    return max(spreads) if spreads else None


def format_progress(progress: Progress) -> str:
    """The trajectory as text, for the agent's closing summary."""
    if not progress.steps:
        return "No training runs on this conversation, so there is no progress to report."

    metric = progress.metric or "score"
    lines = [
        f"Search trajectory ({progress.runs} runs across {progress.versions} versions, "
        f"{progress.failed} failed, {_duration(progress.seconds)} of training):",
        "",
    ]
    shown = progress.steps[-_ROWS_IN_TEXT:]
    if len(shown) < len(progress.steps):
        lines.append(f"... {len(progress.steps) - len(shown)} earlier runs omitted ...")
    for step in shown:
        label = f"v{step.version}" if step.version is not None else step.run_id[:8]
        if step.failed:
            body = f"failed — {(step.error or 'no reason recorded')[:120]}"
        elif step.value is None:
            body = "no comparable score"
        else:
            body = f"{metric}={_fmt(step.value)}"
            if step.improved:
                body += (
                    f"  <- new best (+{_fmt(step.gain)})"
                    if step.gain is not None
                    else "  <- first score"
                )
        lines.append(f"{step.order:>3}. {label:<5} {body}")
        if step.note and step.improved:
            lines.append(f"       {step.note}")

    lines.append("")
    if progress.first is not None and progress.best is not None:
        best_at = next(
            (step.order for step in progress.steps if step.improved and step.value == progress.best),
            None,
        )
        lines.append(
            f"Started at {metric}={_fmt(progress.first)}, best {_fmt(progress.best)} "
            f"(v{progress.best_version}, run {best_at} of {len(progress.steps)}) "
            f"— a gain of {_fmt(progress.total_gain)}."
        )
        if best_at == 1 and len(progress.steps) > 1:
            lines.append(
                "The very first thing tried is still the best; nothing since has beaten it."
            )
    if progress.noise is not None:
        lines.append(
            f"Re-running one version unchanged moved the score by up to "
            f"{_fmt(progress.noise)}, so that is the resolution of these comparisons."
        )
        lines.append(
            "The total gain is larger than that, so the improvement is real."
            if progress.improved
            else "The total gain is not larger than that. Report this as no real "
            "improvement rather than as a win."
        )
    else:
        lines.append(
            "No version was run twice, so the noise floor is unmeasured and no gain "
            "here can be called significant. Say so."
        )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.4g}"


def _duration(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"
