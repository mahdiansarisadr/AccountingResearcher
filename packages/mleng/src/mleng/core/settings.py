"""Central configuration, loaded from the environment (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from .workspace import default_data_dir

load_dotenv()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    llm_model: str
    data_dir: Path
    enable_mlflow_mcp: bool
    # Every version gets the same wall clock, which is what makes a cheap model
    # and an expensive search comparable instead of just differently patient.
    train_budget_seconds: float
    # Sized for a session that iterates on its own, not for one chat answer.
    # These are the fuse, not the stopping rule: the agent decides when it is
    # done, and these stop a runaway.
    max_model_calls: int
    max_tool_calls: int


def _require(name: str, value: str | None) -> str:
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value


def _number(name: str, fallback: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return fallback
    try:
        return float(raw)
    except ValueError:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from None


@lru_cache
def get_settings() -> Settings:
    mcp = os.getenv("MLENG_ENABLE_MLFLOW_MCP", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }
    return Settings(
        anthropic_api_key=_require("ANTHROPIC_API_KEY", os.getenv("ANTHROPIC_API_KEY")),
        llm_model=os.getenv("MLENG_LLM_MODEL", "claude-sonnet-4-5"),
        data_dir=default_data_dir(),
        enable_mlflow_mcp=mcp,
        train_budget_seconds=_number("MLENG_TRAIN_BUDGET_SECONDS", 60.0),
        max_model_calls=int(_number("MLENG_MAX_MODEL_CALLS", 150)),
        max_tool_calls=int(_number("MLENG_MAX_TOOL_CALLS", 200)),
    )
