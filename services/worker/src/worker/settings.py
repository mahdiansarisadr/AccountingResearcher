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

    # Defaults target the host; Compose overrides with the redis service name.
    redis_url: str = "redis://localhost:6379/0"

    environment: str = "development"

    # How long to wait for Redis before giving up on a connection attempt.
    redis_connect_timeout: float = 5.0

    # Seconds between idle-loop heartbeats (placeholder until real job consumption).
    heartbeat_interval: float = 15.0


@lru_cache
def get_worker_settings() -> WorkerSettings:
    return WorkerSettings()
