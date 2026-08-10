"""Shared resources for request handlers."""

from __future__ import annotations

from functools import lru_cache

import run_bus
from redis import Redis
from rq import Queue

from .settings import get_api_settings


@lru_cache
def get_redis() -> Redis:
    """One connection pool for the process.

    redis-py pools connections internally and is thread-safe, so a module-level
    client is the intended usage rather than a per-request connection.
    """
    settings = get_api_settings()
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.probe_timeout,
    )


@lru_cache
def get_queue() -> Queue:
    return Queue(run_bus.QUEUE_NAME, connection=get_redis())
