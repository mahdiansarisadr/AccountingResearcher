"""Run agent-written training code in a child process, under a time budget.

Two layers, and they do different jobs.

The AST guard is a guardrail, not a security boundary: it stops a confused model
from ``import os`` or ``eval``-ing its way off the intended path. Imports that
sklearn and friends make *internally* are unaffected, since only the submitted
source is inspected.

The child process is what makes the budget real. Every version gets the same
wall clock, so a ridge and a 200-trial search are compared on equal compute
rather than on how long the agent was willing to wait — and a script that hangs
or segfaults costs one run instead of the worker.
"""

from __future__ import annotations

import ast
import os
import pickle
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cloudpickle
import pandas as pd

from ...core.sandbox_child import INPUT_FILE, OUTPUT_FILE

# Roots the submitted source may import. Transitive imports from those
# libraries (joblib, os, ctypes, …) still load normally.
_ALLOWED_IMPORT_ROOTS = frozenset(
    {
        "sklearn",
        "xgboost",
        "lightgbm",
        "optuna",
        "pandas",
        "numpy",
        "scipy",
        "math",
        "statistics",
        "collections",
        "itertools",
        "functools",
        "re",
        "json",
        "copy",
        "warnings",
        "typing",
        "dataclasses",
        "numbers",
        "decimal",
        "fractions",
        "operator",
        "string",
        "textwrap",
        "heapq",
        "bisect",
        "random",
        "time",
        "datetime",
        "calendar",
        "abc",
        "enum",
        "contextlib",
        "pprint",
        "reprlib",
        "types",
        "weakref",
    }
)

_BLOCKED_CALLS = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "input", "breakpoint"}
)

_MAX_CODE_CHARS = 80_000
# Killing the process group is not instant; give it a moment before SIGKILL.
_GRACE_SECONDS = 5


class TrainingTimeout(RuntimeError):
    """The script used its whole compute budget without finishing."""


class TrainingScriptError(RuntimeError):
    """The script raised. Carries the child's message verbatim, already prefixed
    with the original exception type, so nothing re-wraps it into
    ``RuntimeError: ValueError: ...``."""


@dataclass
class SandboxResult:
    """What the child got done before it stopped."""

    stdout: str = ""
    params: dict[str, str] = field(default_factory=dict)
    valid_pred: Any = None
    valid_proba: Any = None
    test_pred: Any = None
    model: Any = None
    model_repr: str = ""
    error: str | None = None
    traceback: str | None = None
    seconds: float = 0.0


def assert_training_code_allowed(source: str) -> None:
    """Reject source that reaches outside the training libraries."""
    if len(source) > _MAX_CODE_CHARS:
        raise ValueError(
            f"code is too long ({len(source)} chars; max {_MAX_CODE_CHARS})"
        )
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ValueError(f"code is not valid Python: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_import_root(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValueError("relative imports are not allowed")
            if node.module:
                _check_import_root(node.module)
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name in _BLOCKED_CALLS:
                raise ValueError(f"{name}() is not allowed in train_model code")


def _check_import_root(module: str) -> None:
    root = module.split(".")[0]
    if root not in _ALLOWED_IMPORT_ROOTS:
        raise ValueError(
            f"import of {module!r} is not allowed in train_model code. "
            "Use sklearn, xgboost, lightgbm, optuna, pandas, numpy or scipy."
        )


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def run_training_code(
    source: str,
    *,
    train: pd.DataFrame,
    valid: pd.DataFrame,
    test: pd.DataFrame | None,
    target: str,
    split_seed: int,
    budget_seconds: float,
) -> SandboxResult:
    """Execute ``source`` in a child process and bring back its predictions.

    Raises :class:`TrainingTimeout` when the budget runs out; every other
    failure comes back inside the result so the run can still be logged with a
    reason attached.
    """
    assert_training_code_allowed(source)

    workdir = Path(tempfile.mkdtemp(prefix="mleng-train-"))
    try:
        (workdir / INPUT_FILE).write_bytes(
            cloudpickle.dumps(
                {
                    "source": source,
                    "train": train,
                    "valid": valid,
                    "test": test,
                    "target": target,
                    "split_seed": split_seed,
                }
            )
        )
        elapsed = _spawn(workdir, budget_seconds)
        output = workdir / OUTPUT_FILE
        if not output.is_file():
            raise RuntimeError(
                "the training process died without reporting anything — most "
                "likely it ran out of memory or crashed inside a native library"
            )
        payload = pickle.loads(output.read_bytes())
        return SandboxResult(seconds=elapsed, **payload)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _spawn(workdir: Path, budget_seconds: float) -> float:
    """Run the child, and make sure nothing survives the budget."""
    import time

    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-m", "mleng.core.sandbox_child", str(workdir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        # Its own session, so a runaway that spawned threads or children can be
        # killed as a group rather than left behind holding a core.
        start_new_session=True,
    )
    try:
        _, stderr = process.communicate(timeout=budget_seconds)
    except subprocess.TimeoutExpired:
        _terminate(process)
        raise TrainingTimeout(
            f"training used its whole {budget_seconds:.0f}s compute budget without "
            "finishing. Every version gets the same budget, so make this one "
            "cheaper — fewer trees, fewer search trials, or a smaller model."
        ) from None
    if process.returncode != 0 and not (workdir / OUTPUT_FILE).is_file():
        detail = (stderr or b"").decode("utf-8", "replace").strip()[-2_000:]
        raise RuntimeError(f"the training process exited with {process.returncode}: {detail}")
    return time.monotonic() - started


def _terminate(process: subprocess.Popen) -> None:
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue
