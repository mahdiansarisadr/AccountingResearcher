"""Worker configuration, read from the environment and validated at startup."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    # extra="ignore" so the shared .env can also hold the agent's AR_* keys.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults target the host; Compose overrides with the service names.
    redis_url: str = "redis://localhost:6379/0"
    # The application schema, where the worker records what became of each run.
    # The same DATABASE_URL the API and Alembic use.
    database_url: str = "postgresql://ar_admin:ar_admin@localhost:5433/accounting"

    environment: str = "development"

    # How long to wait for Redis before giving up on a connection attempt.
    redis_connect_timeout: float = 5.0

    # Optional. Empty means error tracking is off.
    sentry_dsn: str = ""


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
