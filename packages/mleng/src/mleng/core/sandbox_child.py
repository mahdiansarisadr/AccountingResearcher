"""The other side of the sandbox: run one training script and hand back results.

Executed as ``python -m mleng.core.sandbox_child <workdir>``, never imported by
the parent. Living in its own process is what makes a wall-clock budget
enforceable — you cannot interrupt a thread stuck inside ``fit``, but you can
kill a process — and it means a segfault in a native library costs one run
instead of the worker.

The script gets ``train`` and ``valid`` and must leave behind something that can
predict. It never sees the test rows or the validation labels' verdict: scoring
happens in the parent, so no script can invent its own ruler.
"""

from __future__ import annotations

import io
import pickle
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import cloudpickle

INPUT_FILE = "input.pkl"
OUTPUT_FILE = "output.pkl"

_STDOUT_CHARS = 20_000


def _modules() -> dict[str, Any]:
    import numpy as np
    import pandas as pd
    from sklearn.model_selection import train_test_split

    injected: dict[str, Any] = {
        "pd": pd,
        "np": np,
        "train_test_split": train_test_split,
    }
    for name in ("sklearn", "xgboost", "lightgbm", "optuna"):
        try:
            injected[name] = __import__(name)
        except ImportError:
            continue
    return injected


def _namespace(payload: dict[str, Any]) -> dict[str, Any]:
    import builtins

    target = payload["target"]
    train = payload["train"]

    def features(frame):
        """Drop the target if it is present, so the same call works everywhere."""
        return frame.drop(columns=[target]) if target in frame.columns else frame

    namespace: dict[str, Any] = {
        "__name__": "train_model_code",
        "__builtins__": builtins,
        "train": train,
        "valid": payload["valid"],
        "target": target,
        "split_seed": payload["split_seed"],
        "features": features,
        "params": {},
    }
    namespace.update(_modules())
    return namespace


def _resolve_predict(namespace: dict[str, Any]):
    predict = namespace.get("predict")
    if callable(predict):
        return predict
    model = namespace.get("model")
    if model is not None and hasattr(model, "predict"):
        return model.predict
    raise ValueError(
        "the script must assign `model` (anything with .predict) or a `predict` "
        "function. Whatever you assign has to accept a raw feature frame, so put "
        "feature engineering inside a Pipeline or inside `predict`."
    )


def _probabilities(namespace: dict[str, Any], frame):
    model = namespace.get("model")
    if model is None or not hasattr(model, "predict_proba"):
        return None
    try:
        return model.predict_proba(frame)
    except Exception:  # noqa: BLE001 - probabilities are a bonus, not a contract
        return None


def main(workdir: Path) -> int:
    payload = pickle.loads((workdir / INPUT_FILE).read_bytes())
    result: dict[str, Any] = {
        "stdout": "",
        "params": {},
        "valid_pred": None,
        "valid_proba": None,
        "test_pred": None,
        "model": None,
        "model_repr": "",
        "error": None,
    }

    buffer = io.StringIO()
    try:
        # The parent already checked this. Checking again means the guard holds
        # even if something else ever learns to start this process.
        from mleng.agent.tools.code_sandbox import assert_training_code_allowed

        assert_training_code_allowed(payload["source"])
        namespace = _namespace(payload)
        with redirect_stdout(buffer), redirect_stderr(buffer):
            exec(compile(payload["source"], "<train_model>", "exec"), namespace, namespace)  # noqa: S102
            predict = _resolve_predict(namespace)
            valid_features = namespace["features"](payload["valid"])
            result["valid_pred"] = predict(valid_features)
            result["valid_proba"] = _probabilities(namespace, valid_features)
            if payload.get("test") is not None:
                result["test_pred"] = predict(namespace["features"](payload["test"]))

        declared = namespace.get("params")
        if isinstance(declared, dict):
            result["params"] = {str(k): str(v)[:250] for k, v in list(declared.items())[:50]}
        model = namespace.get("model")
        if model is not None:
            result["model_repr"] = type(model).__name__
            try:
                result["model"] = cloudpickle.dumps(model)
            except Exception as exc:  # noqa: BLE001 - reported, metrics still stand
                result["model_repr"] = f"{type(model).__name__} (not serialisable: {exc})"
    except BaseException as exc:  # noqa: BLE001 - the parent turns this into a failed run
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()[-4_000:]

    result["stdout"] = buffer.getvalue()[-_STDOUT_CHARS:]
    (workdir / OUTPUT_FILE).write_bytes(cloudpickle.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
