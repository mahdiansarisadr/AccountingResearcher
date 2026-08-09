"""Worker entry point.

Phase 0 scope: prove the process starts, reaches Redis, and shuts down cleanly.
Job consumption (executing agent runs and publishing their events) lands in
Phase 1, where this idle loop is replaced by a real queue consumer.

Run:  ar-worker   (or: python -m worker.main)
"""

from __future__ import annotations

import logging
import signal
import threading
import time

import redis

from . import __version__
from .settings import get_worker_settings

logger = logging.getLogger("worker")

# Set when a shutdown signal arrives; also used as an interruptible sleep.
_shutdown = threading.Event()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _install_signal_handlers() -> None:
    """Translate termination signals into a cooperative shutdown.

    Containers are stopped with SIGTERM; handling it lets an in-flight job
    finish instead of being killed mid-run.
    """

    def handle(signum, _frame) -> None:
        logger.info("received %s, shutting down", signal.Signals(signum).name)
        _shutdown.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)


def connect_to_redis(url: str, timeout: float) -> redis.Redis:
    """Connect to Redis, retrying briefly so startup order doesn't matter."""
    client = redis.from_url(
        url, socket_connect_timeout=timeout, socket_timeout=timeout
    )

    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        try:
            client.ping()
            return client
        except redis.RedisError as exc:
            if time.monotonic() >= deadline or _shutdown.is_set():
                raise
            logger.warning("redis not ready (attempt %d): %s", attempt, exc)
            _shutdown.wait(1.0)


def main() -> None:
    _configure_logging()
    _install_signal_handlers()
    settings = get_worker_settings()

    logger.info("worker %s starting (env=%s)", __version__, settings.environment)

    client = connect_to_redis(settings.redis_url, settings.redis_connect_timeout)
    logger.info("connected to redis at %s", settings.redis_url)
    logger.info("no queue consumer yet (Phase 1); idling")

    try:
        while not _shutdown.is_set():
            _shutdown.wait(settings.heartbeat_interval)
            if not _shutdown.is_set():
                logger.info("idle heartbeat")
    finally:
        client.close()
        logger.info("worker stopped")


if __name__ == "__main__":
    main()
