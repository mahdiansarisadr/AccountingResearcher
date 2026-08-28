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


def _require(name: str, value: str | None) -> str:
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value


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
    )
