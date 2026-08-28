"""Train a model on an isolated user workspace without calling the LLM."""

from __future__ import annotations

import json
from pathlib import Path

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


def test_train_model_logs_to_that_users_mlflow(tmp_path: Path) -> None:
    save_upload("user-a", "thread-1", "iris.csv", CSV.encode(), data_dir=tmp_path)
    token = set_run_context("user-a", "thread-1", data_dir=tmp_path)
    try:
        result = json.loads(train_model_impl("species", filename="iris.csv"))
    finally:
        reset_run_context(token)

    assert result["target"] == "species"
    assert result["task"] == "classification"
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
