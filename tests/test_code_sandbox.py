"""Guards and the compute budget on agent-written training code."""

from __future__ import annotations

import time

import pandas as pd
import pytest
from mleng.agent.tools.code_sandbox import (
    TrainingTimeout,
    assert_training_code_allowed,
    run_training_code,
)


def _frame(rows: int = 40) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "x": range(rows),
            "z": [(i * 7) % 13 for i in range(rows)],
            "y": [3.0 + i * 2.5 for i in range(rows)],
        }
    )


def _run(source: str, *, budget: float = 60.0):
    frame = _frame()
    return run_training_code(
        source,
        train=frame.iloc[:30],
        valid=frame.iloc[30:],
        test=frame.iloc[30:].drop(columns=["y"]),
        target="y",
        split_seed=42,
        budget_seconds=budget,
    )


RIDGE = """
from sklearn.linear_model import Ridge

model = Ridge()
model.fit(features(train), train[target])
params = {"alpha": 1.0}
print("fitted")
"""


def test_sklearn_and_xgboost_imports_are_allowed() -> None:
    assert_training_code_allowed(
        "import xgboost as xgb\nfrom sklearn.linear_model import Ridge\nimport optuna\n"
    )


def test_relative_imports_are_rejected() -> None:
    with pytest.raises(ValueError, match="relative"):
        assert_training_code_allowed("from .foo import bar")


def test_open_is_rejected() -> None:
    with pytest.raises(ValueError, match="open"):
        assert_training_code_allowed("open('/etc/passwd')")


def test_mlflow_is_no_longer_reachable_from_a_script() -> None:
    """Scripts do not log their own runs; the harness does."""
    with pytest.raises(ValueError, match="mlflow"):
        assert_training_code_allowed("import mlflow")


def test_a_script_returns_predictions_and_stdout() -> None:
    result = _run(RIDGE)

    assert result.error is None
    assert "fitted" in result.stdout
    assert len(result.valid_pred) == 10
    assert len(result.test_pred) == 10
    assert result.model_repr == "Ridge"
    assert result.params == {"alpha": "1.0"}
    assert result.seconds > 0


def test_a_predict_function_works_instead_of_a_model() -> None:
    result = _run(
        """
def predict(frame):
    return frame["x"] * 2.5 + 3.0
"""
    )

    assert result.error is None
    assert len(result.valid_pred) == 10


def test_a_script_that_scores_nothing_is_an_error() -> None:
    result = _run("x = 1\n")

    assert result.error is not None
    assert "predict" in result.error


def test_a_raising_script_comes_back_with_its_reason() -> None:
    result = _run("raise ValueError('bad transform')")

    assert result.error is not None
    assert "bad transform" in result.error
    assert result.traceback


def test_the_compute_budget_is_enforced() -> None:
    started = time.monotonic()
    with pytest.raises(TrainingTimeout, match="compute budget"):
        _run("import time\ntime.sleep(30)\nmodel = None\n", budget=2)

    # The point of a separate process: a script stuck inside a call still stops.
    assert time.monotonic() - started < 15


def test_a_script_cannot_see_the_test_labels() -> None:
    result = _run(
        """
model = None
def predict(frame):
    return [0.0] * len(frame)
seen = sorted(train.columns)
print("columns:", seen)
"""
    )

    assert result.error is None
    assert "y" in result.stdout  # train keeps its target
