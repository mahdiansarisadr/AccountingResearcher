"""The trajectory report: did the search actually move, and by more than noise?"""

from __future__ import annotations

from pathlib import Path

from mleng.agent.tools.progress import report_progress_impl
from mleng.agent.tools.train import train_model_impl
from mleng.core.progress import format_progress, summarise_progress
from mleng.core.workspace import reset_run_context, save_upload, set_run_context

REGRESSION_CSV = """x,z,y
1,2,3.0
2,1,3.5
3,4,8.0
4,3,8.5
5,6,13.0
6,5,13.5
7,8,18.0
8,7,18.5
9,10,23.0
10,9,23.5
11,12,28.0
12,11,28.5
13,14,33.0
14,13,33.5
15,16,38.0
16,15,38.5
"""


def _seed(tmp_path: Path) -> None:
    save_upload("user-p", "thread-p", "data.csv", REGRESSION_CSV.encode(), data_dir=tmp_path)


def test_empty_thread_reports_nothing_to_show(tmp_path: Path) -> None:
    progress = summarise_progress("nobody", "no-thread", data_dir=tmp_path)
    assert progress.steps == ()
    assert progress.best is None
    assert progress.improved is False
    assert "No training runs" in format_progress(progress)


def test_trajectory_is_chronological_with_a_monotonic_best(tmp_path: Path) -> None:
    _seed(tmp_path)
    token = set_run_context("user-p", "thread-p", data_dir=tmp_path)
    try:
        for model in ("ridge", "random_forest", "hist_gb"):
            assert not train_model_impl(
                "y", filename="data.csv", model=model, task="regression"
            ).startswith("ERROR:")
    finally:
        reset_run_context(token)

    progress = summarise_progress("user-p", "thread-p", data_dir=tmp_path)

    assert progress.runs == 3
    assert progress.versions == 3
    assert [step.order for step in progress.steps] == [1, 2, 3]
    assert progress.metric == "r2"

    curve = [step.best_so_far for step in progress.steps]
    assert all(a <= b for a, b in zip(curve, curve[1:])), "best-so-far must never fall"
    assert progress.first == progress.steps[0].value
    assert progress.best == curve[-1]
    assert progress.best_version is not None
    assert progress.steps[0].improved, "the first score always sets the best"


def test_rerunning_a_version_measures_the_noise_floor(tmp_path: Path) -> None:
    _seed(tmp_path)
    token = set_run_context("user-p", "thread-p", data_dir=tmp_path)
    try:
        train_model_impl("y", filename="data.csv", model="ridge", task="regression")
        train_model_impl(
            "y", filename="data.csv", model="ridge", task="regression", recipe_version=1
        )
    finally:
        reset_run_context(token)

    progress = summarise_progress("user-p", "thread-p", data_dir=tmp_path)

    assert progress.versions == 1
    assert progress.runs == 2
    # A deterministic model re-run on the same split lands in the same place, so
    # the floor is measured and flat rather than unknown.
    assert progress.noise == 0.0
    assert progress.total_gain == 0.0
    assert progress.improved is False
    assert "not larger than that" in format_progress(progress)


def test_unmeasured_noise_is_called_out_rather_than_assumed_zero(tmp_path: Path) -> None:
    _seed(tmp_path)
    token = set_run_context("user-p", "thread-p", data_dir=tmp_path)
    try:
        train_model_impl("y", filename="data.csv", model="ridge", task="regression")
    finally:
        reset_run_context(token)

    progress = summarise_progress("user-p", "thread-p", data_dir=tmp_path)

    assert progress.noise is None
    assert progress.improved is False, "one run is not evidence of improvement"
    assert "noise floor is unmeasured" in format_progress(progress)


def test_failed_runs_stay_in_the_trajectory_with_their_reason(tmp_path: Path) -> None:
    _seed(tmp_path)
    token = set_run_context("user-p", "thread-p", data_dir=tmp_path)
    try:
        train_model_impl("y", filename="data.csv", model="ridge", task="regression")
        train_model_impl(
            "y",
            filename="data.csv",
            code="raise ValueError('deliberate')",
            task="regression",
        )
    finally:
        reset_run_context(token)

    progress = summarise_progress("user-p", "thread-p", data_dir=tmp_path)
    failed = [step for step in progress.steps if step.failed]

    assert len(failed) == 1
    assert "deliberate" in (failed[0].error or "")
    assert failed[0].value is None
    assert progress.failed == 1
    # A failure must not erase the best score reached before it.
    assert failed[0].best_so_far == progress.best

    text = format_progress(progress)
    assert "failed" in text
    assert "deliberate" in text


def test_tool_renders_the_report_for_the_current_context(tmp_path: Path) -> None:
    _seed(tmp_path)
    token = set_run_context("user-p", "thread-p", data_dir=tmp_path)
    try:
        train_model_impl("y", filename="data.csv", model="ridge", task="regression")
        text = report_progress_impl()
    finally:
        reset_run_context(token)

    assert "Search trajectory" in text
    assert "r2" in text
