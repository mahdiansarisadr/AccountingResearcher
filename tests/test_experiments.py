"""Leaderboard helpers: comparable metrics and the prompt table."""

from __future__ import annotations

from pathlib import Path

from mleng.core.experiments import (
    canonicalize_metrics,
    format_leaderboard,
    list_experiment_runs,
    primary_score,
)
from mleng.agent.tools.train import train_model_impl
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
"""


def test_canonicalize_folds_aliases_and_drops_infinity() -> None:
    out = canonicalize_metrics(
        {"r2_test": 0.4, "mae_test": 12.0, "mape": float("inf"), "noise": "x"}
    )
    assert out["r2"] == 0.4
    assert out["mae"] == 12.0
    assert "mape" not in out
    assert "noise" not in out


def test_primary_score_prefers_accuracy_then_r2() -> None:
    assert primary_score({"accuracy": 0.9, "r2": 0.1}) == ("accuracy", 0.9)
    assert primary_score({"r2": 0.42, "mae": 10.0}) == ("r2", 0.42)
    assert primary_score({"mae": 10.0}) == (None, None)


def test_leaderboard_names_the_best_holdout(tmp_path: Path) -> None:
    save_upload("user-a", "thread-1", "data.csv", REGRESSION_CSV.encode(), data_dir=tmp_path)
    token = set_run_context("user-a", "thread-1", data_dir=tmp_path)
    try:
        raw = train_model_impl(
            "y",
            filename="data.csv",
            model="ridge",
            task="regression",
            hypothesis="plain ridge",
        )
    finally:
        reset_run_context(token)
    assert not raw.startswith("ERROR:")

    rows = list_experiment_runs("user-a", "thread-1", data_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0].model == "ridge"
    assert rows[0].primary_metric == "r2"
    assert rows[0].hypothesis == "plain ridge"

    text = format_leaderboard("user-a", "thread-1", data_dir=tmp_path)
    assert "Best holdout so far" in text
    assert "plain ridge" in text
    assert "validation rows" in text


def test_empty_leaderboard_says_so(tmp_path: Path) -> None:
    text = format_leaderboard("nobody", "no-thread", data_dir=tmp_path)
    assert "No training runs" in text
