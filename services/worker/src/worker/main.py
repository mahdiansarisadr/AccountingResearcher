"""Worker entry point: consume queued runs and execute them.

Run:  ar-worker   (or: python -m worker.main)
"""

from __future__ import annotations

import logging
import signal
import threading
import time

import redis
import run_bus
from rq import Queue, SimpleWorker

from . import __version__
from .logconfig import configure_logging
from .settings import get_worker_settings

logger = logging.getLogger("worker")

# Set when a shutdown signal arrives during startup; also an interruptible sleep.
# Once RQ takes over it installs its own handlers for warm shutdown.
_shutdown = threading.Event()


def _configure_logging() -> None:
    configure_logging(get_worker_settings().environment)


def _init_sentry() -> None:
    settings = get_worker_settings()
    if not settings.sentry_dsn:
        return
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=__version__,
        send_default_pii=False,
    )


def _install_signal_handlers() -> None:
    """Make the Redis connect loop interruptible.

    Containers are stopped with SIGTERM. RQ replaces these handlers when it
    starts working, so an in-flight job finishes before the worker exits.
    """

    def handle(signum, _frame) -> None:
        logger.info("received %s, shutting down", signal.Signals(signum).name)
        _shutdown.set()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)


def connect_to_redis(url: str, timeout: float) -> redis.Redis:
    """Connect to Redis, retrying briefly so startup order doesn't matter."""
    client = redis.from_url(url, socket_connect_timeout=timeout, socket_timeout=timeout)

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
    _init_sentry()
    _install_signal_handlers()
    settings = get_worker_settings()

    logger.info("worker %s starting (env=%s)", __version__, settings.environment)

    connection = connect_to_redis(settings.redis_url, settings.redis_connect_timeout)
    logger.info("connected to redis at %s", settings.redis_url)

    queue = Queue(run_bus.QUEUE_NAME, connection=connection)

    # SimpleWorker executes jobs in this process instead of forking one child per
    # job. That keeps the built agent warm across runs, which matters for the
    # latency budget, and avoids the fork restrictions that make forking workers
    # unreliable on macOS. The cost is no per-job process isolation; revisit if a
    # run is ever able to corrupt process state.
    worker = SimpleWorker([queue], connection=connection)
    logger.info("consuming queue %r", run_bus.QUEUE_NAME)

    try:
        worker.work()
    finally:
        connection.close()
        logger.info("worker stopped")


if __name__ == "__main__":
    main()
