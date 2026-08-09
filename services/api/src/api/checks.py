"""Dependency probes backing the readiness endpoint.

Each probe is deliberately cheap and time-bounded: readiness is polled
frequently by orchestrators, so it must never hang on a sick dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
import redis

from .settings import get_api_settings


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    detail: str


def check_postgres() -> ProbeResult:
    settings = get_api_settings()
    try:
        with psycopg.connect(
            settings.database_url, connect_timeout=int(settings.probe_timeout)
        ) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return ProbeResult(True, "connected")
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        return ProbeResult(False, f"{type(exc).__name__}: {exc}".strip())


def check_redis() -> ProbeResult:
    settings = get_api_settings()
    client = None
    try:
        client = redis.from_url(
            settings.redis_url,
            socket_connect_timeout=settings.probe_timeout,
            socket_timeout=settings.probe_timeout,
        )
        client.ping()
        return ProbeResult(True, "connected")
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        return ProbeResult(False, f"{type(exc).__name__}: {exc}".strip())
    finally:
        if client is not None:
            client.close()
