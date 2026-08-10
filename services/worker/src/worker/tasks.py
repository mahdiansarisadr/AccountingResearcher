"""The job RQ executes: run the agent and publish what it emits.

This module is the seam between the queue and the agent. It contains no agent
logic — that lives in accounting_research.agent.runner — and no HTTP concerns.
"""

from __future__ import annotations

import logging
from typing import Any

import run_bus
from accounting_research.agent.runner import run_agent
from redis import Redis

from .settings import get_worker_settings

logger = logging.getLogger("worker.tasks")

# Built once per worker process and reused across jobs. Construction is cheap but
# not free, and a run has a latency budget to respect.
_agent: Any | None = None


def _get_agent() -> Any:
    global _agent
    if _agent is None:
        from accounting_research.agent.builder import build_agent

        _agent = build_agent()
    return _agent


def execute_run(
    run_id: str,
    message: str,
    history: list[dict[str, Any]] | None = None,
) -> str:
    """Execute one agent run, publishing every event to the run's stream.

    Returns the final status. Exceptions are deliberately not allowed to escape
    as the only signal: a client streaming this run must receive a terminating
    event, so failures are published before being re-raised for the queue's own
    bookkeeping.
    """
    settings = get_worker_settings()
    redis = Redis.from_url(settings.redis_url)

    logger.info("run %s starting", run_id)
    run_bus.set_status(redis, run_id, "running")

    def cancelled() -> bool:
        return run_bus.cancel_requested(redis, run_id)

    status = "failed"
    try:
        for event in run_agent(
            message,
            history=history,
            run_id=run_id,
            agent=_get_agent(),
            is_cancelled=cancelled,
        ):
            # Record the outcome *before* publishing the terminal event. A client
            # that sees "done" may immediately ask for the status, and would
            # otherwise be told the run is still running.
            if event.type == "done":
                status = event.status
                run_bus.set_status(redis, run_id, status)
            run_bus.publish(redis, run_id, event)
        logger.info("run %s finished: %s", run_id, status)
        return status
    except Exception as exc:
        # run_agent converts most failures into events itself; this covers the
        # rest (Redis errors, a broken agent build) so the stream still ends.
        logger.exception("run %s crashed", run_id)
        from accounting_research.agent.events import Done, Error

        run_bus.publish(redis, run_id, Error(message=f"{type(exc).__name__}: {exc}"))
        run_bus.publish(redis, run_id, Done(run_id=run_id, status="failed"))
        raise
    finally:
        run_bus.set_status(redis, run_id, status)
        redis.close()
