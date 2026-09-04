"""The frozen split: same rows for every version, and a test set nobody selects on."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from mleng.agent.tools.train import LOCKED_PREFIX, train_model_impl
from mleng.core import splits
from mleng.core.experiments import list_experiment_runs
from mleng.core.workspace import reset_run_context, save_upload, set_run_context


def _frame(rows: int = 200) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "x": range(rows),
            "z": [(i * 7) % 13 for i in range(rows)],
            "y": [3.0 + i * 2.5 for i in range(rows)],
        }
    )


def _labelled(rows: int = 200) -> pd.DataFrame:
    frame = _frame(rows)
    frame["y"] = ["a" if i % 3 else "b" for i in range(rows)]
    return frame


def test_the_split_is_the_same_every_time() -> None:
    first = splits.make_split(_frame(), "y")
    second = splits.make_split(_frame(), "y")

    assert np.array_equal(first.train, second.train)
    assert np.array_equal(first.valid, second.valid)
    assert np.array_equal(first.test, second.test)


def test_the_three_parts_are_disjoint_and_complete() -> None:
    split = splits.make_split(_frame(), "y")
    combined = np.concatenate([split.train, split.valid, split.test])

    assert len(combined) == 200
    assert len(np.unique(combined)) == 200
    assert len(split.test) == 40
    assert len(split.valid) == 40


def test_a_different_seed_moves_the_rows() -> None:
    default = splits.make_split(_frame(), "y")
    other = splits.make_split(_frame(), "y", seed=7)

    assert not np.array_equal(default.valid, other.valid)


def test_the_fingerprint_notices_changed_data() -> None:
    frame = _frame()
    changed = frame.copy()
    changed.loc[0, "x"] = 9_999

    assert splits.fingerprint(frame) == splits.fingerprint(_frame())
    assert splits.fingerprint(frame) != splits.fingerprint(changed)


def test_a_tiny_table_gives_up_the_test_set_rather_than_pretend() -> None:
    split = splits.make_split(_frame(12), "y")

    assert not split.has_test
    assert len(split.train) + len(split.valid) == 12


def test_classes_are_spread_across_the_parts() -> None:
    frame = _labelled()
    split = splits.make_split(frame, "y")

    for part in (split.train, split.valid, split.test):
        assert frame["y"].iloc[part].nunique() == 2


def test_every_version_is_scored_on_the_same_rows(tmp_path: Path) -> None:
    """Two different scripts, one split — the comparison is the whole point."""
    save_upload("user-a", "t", "data.csv", _frame().to_csv(index=False).encode(), data_dir=tmp_path)
    token = set_run_context("user-a", "t", data_dir=tmp_path)
    try:
        default = json.loads(
            train_model_impl("y", filename="data.csv", model="ridge", task="regression")
        )
        written = json.loads(
            train_model_impl(
                "y",
                filename="data.csv",
                # The same estimator the default path builds, written by hand.
                code=(
                    "from sklearn.linear_model import Ridge\n"
                    "from sklearn.pipeline import Pipeline\n"
                    "from sklearn.preprocessing import StandardScaler\n"
                    "model = Pipeline([('s', StandardScaler()), ('m', Ridge())])\n"
                    "model.fit(features(train), train[target])\n"
                ),
                parent_version=1,
            )
        )
    finally:
        reset_run_context(token)

    assert default["n_train"] == written["n_train"]
    assert default["n_valid"] == written["n_valid"]
    # Same estimator, same rows, so the harness must produce the same number.
    assert default["metrics"]["r2"] == written["metrics"]["r2"]


def test_the_locked_score_is_recorded_but_never_handed_to_the_agent(tmp_path: Path) -> None:
    save_upload("user-a", "t", "data.csv", _frame().to_csv(index=False).encode(), data_dir=tmp_path)
    token = set_run_context("user-a", "t", data_dir=tmp_path)
    try:
        result = json.loads(
            train_model_impl("y", filename="data.csv", model="ridge", task="regression")
        )
    finally:
        reset_run_context(token)

    assert not any(name.startswith(LOCKED_PREFIX) for name in result["metrics"])

    logged = list_experiment_runs("user-a", "t", data_dir=tmp_path)[0].metrics
    assert f"{LOCKED_PREFIX}r2" in logged
    # It must not be mistaken for the metric versions are ranked on.
    assert logged[f"{LOCKED_PREFIX}r2"] != logged["r2"]
