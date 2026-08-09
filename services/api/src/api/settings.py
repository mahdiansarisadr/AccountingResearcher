"""API configuration, read from the environment and validated at startup.

Uses pydantic-settings so a missing or malformed value fails fast on boot
rather than surfacing as a confusing runtime error later. Field names map to
env vars case-insensitively (``database_url`` <- ``DATABASE_URL``).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    # extra="ignore" so the shared .env can also hold the agent's AR_* keys.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults target local development (host ports from docker-compose).
    # In Compose, these are overridden with service hostnames (postgres, redis).
    database_url: str = "postgresql://ar_admin:ar_admin@localhost:5433/accounting"
    redis_url: str = "redis://localhost:6379/0"

    environment: str = "development"

    # Seconds a readiness probe waits on a dependency before calling it down.
    probe_timeout: float = 2.0


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
