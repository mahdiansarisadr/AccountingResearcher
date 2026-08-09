"""Central configuration, loaded from the environment (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_DB_RESOURCES_DIRNAME = "test_database_resources"


def _default_db_resources_dir() -> Path:
    """Locate test_database_resources/ by walking up from the CWD and this file.

    Robust to where the package lives in the repo (e.g. under packages/…) and to
    where the command is launched from. Overridable via AR_DB_RESOURCES_DIR.
    """
    for base in (Path.cwd(), Path(__file__).resolve()):
        for parent in (base, *base.parents):
            candidate = parent / _DB_RESOURCES_DIRNAME
            if candidate.is_dir():
                return candidate
    return Path.cwd() / _DB_RESOURCES_DIRNAME


@dataclass(frozen=True)
class Settings:
    # LLM / embeddings
    openai_api_key: str
    llm_model: str
    embedding_model: str
    embedding_dim: int

    # Postgres
    database_url: str           # admin: seeding + catalog build
    readonly_database_url: str  # agent: run_sql_query (read-only role)

    # Retrieval / limits
    schema_top_k: int
    max_result_rows: int

    # Filesystem: demo/test database bootstrap material (.sql, tables.yaml)
    db_resources_dir: Path

    @property
    def schema_sql(self) -> Path:
        return self.db_resources_dir / "schema.sql"

    @property
    def catalog_sql(self) -> Path:
        return self.db_resources_dir / "catalog.sql"

    @property
    def tables_yaml(self) -> Path:
        return self.db_resources_dir / "tables.yaml"


def _require(name: str, value: str | None) -> str:
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value


@lru_cache
def get_settings() -> Settings:
    return Settings(
        openai_api_key=_require("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY")),
        llm_model=os.getenv("AR_LLM_MODEL", "gpt-4o"),
        embedding_model=os.getenv("AR_EMBEDDING_MODEL", "text-embedding-3-small"),
        embedding_dim=int(os.getenv("AR_EMBEDDING_DIM", "1536")),
        database_url=os.getenv(
            "AR_DATABASE_URL",
            "postgresql://ar_admin:ar_admin@localhost:5433/accounting",
        ),
        readonly_database_url=os.getenv(
            "AR_READONLY_DATABASE_URL",
            "postgresql://ar_readonly:ar_readonly@localhost:5433/accounting",
        ),
        schema_top_k=int(os.getenv("AR_SCHEMA_TOP_K", "5")),
        max_result_rows=int(os.getenv("AR_MAX_RESULT_ROWS", "200")),
        db_resources_dir=(
            Path(os.environ["AR_DB_RESOURCES_DIR"])
            if os.getenv("AR_DB_RESOURCES_DIR")
            else _default_db_resources_dir()
        ),
    )
