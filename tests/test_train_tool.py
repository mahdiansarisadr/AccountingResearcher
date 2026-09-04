"""Train a model on an isolated user workspace without calling the LLM."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from mleng.agent.tools.code_sandbox import assert_training_code_allowed
from mleng.agent.tools.train import train_model_impl
from mleng.core.workspace import reset_run_context, save_upload, set_run_context


CSV = """sepal_length,sepal_width,species
5.1,3.5,setosa
4.9,3.0,setosa
6.2,3.4,virginica
5.9,3.0,virginica
5.5,2.3,versicolor
6.5,2.8,virginica
4.6,3.1,setosa
5.7,2.8,versicolor
6.3,3.3,virginica
5.0,3.6,setosa
"""

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


def _train(tmp_path: Path, csv: str, target: str, filename: str = "data.csv", **kwargs):
    save_upload("user-a", "thread-1", filename, csv.encode(), data_dir=tmp_path)
    token = set_run_context("user-a", "thread-1", data_dir=tmp_path)
    try:
        raw = train_model_impl(target, filename=filename, **kwargs)
    finally:
        reset_run_context(token)
    if raw.startswith("ERROR:"):
        return raw
    return json.loads(raw)


def test_train_model_logs_to_that_users_mlflow(tmp_path: Path) -> None:
    save_upload("user-a", "thread-1", "iris.csv", CSV.encode(), data_dir=tmp_path)
    token = set_run_context("user-a", "thread-1", data_dir=tmp_path)
    try:
        result = json.loads(train_model_impl("species", filename="iris.csv"))
    finally:
        reset_run_context(token)

    assert result["target"] == "species"
    assert result["task"] == "classification"
    assert result["mode"] == "default"
    assert "accuracy" in result["metrics"]
    assert "user-a" in result["tracking_uri"]
    assert "user-b" not in result["tracking_uri"]
    assert (tmp_path / "users" / "user-a" / "mlflow.db").is_file()
    assert not (tmp_path / "users" / "user-b" / "mlflow.db").exists()


def test_two_users_train_into_separate_stores(tmp_path: Path) -> None:
    save_upload("user-a", "t", "iris.csv", CSV.encode(), data_dir=tmp_path)
    save_upload("user-b", "t", "iris.csv", CSV.encode(), data_dir=tmp_path)

    token = set_run_context("user-a", "t", data_dir=tmp_path)
    try:
        a = json.loads(train_model_impl("species", filename="iris.csv"))
    finally:
        reset_run_context(token)

    token = set_run_context("user-b", "t", data_dir=tmp_path)
    try:
        b = json.loads(train_model_impl("species", filename="iris.csv"))
    finally:
        reset_run_context(token)

    assert a["tracking_uri"] != b["tracking_uri"]
    assert "user-a" in a["tracking_uri"]
    assert "user-b" in b["tracking_uri"]
    assert (tmp_path / "users" / "user-a" / "mlflow.db").is_file()
    assert (tmp_path / "users" / "user-b" / "mlflow.db").is_file()


def test_named_ridge_regression(tmp_path: Path) -> None:
    result = _train(tmp_path, REGRESSION_CSV, "y", model="ridge", task="regression")
    assert result["model"] == "ridge"
    assert result["task"] == "regression"
    assert "r2" in result["metrics"]
    assert "mae" in result["metrics"]
    assert "rmse" in result["metrics"]


def test_unknown_target_is_an_error(tmp_path: Path) -> None:
    raw = _train(tmp_path, CSV, "missing")
    assert isinstance(raw, str) and raw.startswith("ERROR:")
    assert "missing" in raw


def test_code_trains_whatever_the_agent_writes(tmp_path: Path) -> None:
    code = """
from sklearn.linear_model import Ridge

model = Ridge()
model.fit(features(train), train[target])
print("trained ridge")
"""
    result = _train(tmp_path, REGRESSION_CSV, "y", code=code, hypothesis="code ridge")
    assert result["mode"] == "code"
    assert result["logged_model"] is True
    # The harness scored it, so the metric names are the same as any other run.
    assert "r2" in result["metrics"]
    assert "mae" in result["metrics"]
    assert "trained ridge" in result["stdout"]
    assert "user-a" in result["tracking_uri"]
    assert (tmp_path / "users" / "user-a" / "mlflow.db").is_file()


def test_a_script_that_only_transforms_training_rows_fails_loudly(tmp_path: Path) -> None:
    """Feature engineering outside the model cannot be reproduced at scoring time."""
    code = """
from sklearn.linear_model import Ridge

frame = features(train).copy()
frame["ratio"] = frame["x"] / (frame["z"] + 1)
model = Ridge()
model.fit(frame, train[target])
"""
    raw = _train(tmp_path, REGRESSION_CSV, "y", code=code)

    assert isinstance(raw, str) and raw.startswith("ERROR:")


def test_code_cannot_import_os() -> None:
    with pytest.raises(ValueError, match="os"):
        assert_training_code_allowed("import os\nos.system('echo hi')")


def test_code_cannot_call_eval() -> None:
    with pytest.raises(ValueError, match="eval"):
        assert_training_code_allowed("eval('1+1')")


def test_code_rejects_blocked_import_at_train_time(tmp_path: Path) -> None:
    raw = _train(tmp_path, REGRESSION_CSV, "y", code="import subprocess\n")
    assert isinstance(raw, str) and raw.startswith("ERROR:")
    assert "subprocess" in raw


@pytest.mark.skipif(
    importlib.util.find_spec("xgboost") is None,
    reason="xgboost is not installed",
)
def test_code_can_import_xgboost(tmp_path: Path) -> None:
    code = """
import xgboost as xgb

model = xgb.XGBRegressor(n_estimators=20, max_depth=2, n_jobs=1, verbosity=0)
model.fit(features(train), train[target])
"""
    result = _train(tmp_path, REGRESSION_CSV, "y", code=code)
    assert result["mode"] == "code"
    assert result["logged_model"] is True
    assert "r2" in result["metrics"]
