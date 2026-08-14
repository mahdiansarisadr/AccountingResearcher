"""The job RQ executes: run the agent, publish what it emits, record how it ended.

This module is the seam between the queue and the agent. It contains no agent
logic — that lives in accounting_research.agent.runner — and no HTTP concerns.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import app_db
import run_bus
from accounting_research.agent.events import Done, Error
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
    """Execute one agent run, publishing every event and recording the outcome.

    Returns the final status. Exceptions are deliberately not allowed to be the
    only signal: a client streaming this run must receive a terminating event, so
    failures are published and recorded before being re-raised for the queue's
    own bookkeeping.
    """
    settings = get_worker_settings()
    redis = Redis.from_url(settings.redis_url)
    run_uuid = UUID(run_id)
    settled = False

    def settle(
        status: app_db.RunStatus,
        error: str | None = None,
        answer: Any | None = None,
    ) -> None:
        nonlocal settled
        with app_db.session_scope(settings.database_url) as session:
            moved = app_db.mark_finished(session, run_uuid, status, error)
            # The assistant turn is written by whoever actually settled the run,
            # so a redelivered job cannot append the same answer twice.
            if (
                moved
                and status is app_db.RunStatus.SUCCEEDED
                and answer is not None
            ):
                run = app_db.get_run(session, run_uuid)
                if run is not None:
                    app_db.append_message(
                        session,
                        run.thread_id,
                        app_db.MessageRole.ASSISTANT,
                        answer.answer,
                        payload=answer.model_dump(),
                    )
        settled = True

    logger.info("run %s starting", run_id)
    with app_db.session_scope(settings.database_url) as session:
        if not app_db.mark_running(session, run_uuid):
            # The API commits the row before enqueuing, so this means the record
            # is gone or already settled. Stream the run anyway — a client is
            # probably waiting on it — but say so, because it should not happen.
            logger.warning("run %s has no queued record", run_id)

    try:
        # Cancelled while it sat in the queue. Nothing to execute, but a client
        # streaming this run still needs an event that ends the stream.
        if run_bus.cancel_requested(redis, run_id):
            logger.info("run %s cancelled before it started", run_id)
            settle(app_db.RunStatus.CANCELLED)
            run_bus.publish(redis, run_id, Done(run_id=run_id, status="cancelled"))
            return app_db.RunStatus.CANCELLED.value

        outcome = app_db.RunStatus.FAILED
        error: str | None = None
        answer: Any | None = None

        for event in run_agent(
            message,
            history=history,
            run_id=run_id,
            agent=_get_agent(),
            is_cancelled=lambda: run_bus.cancel_requested(redis, run_id),
        ):
            if event.type == "answer":
                answer = event.answer
            if event.type == "error":
                # The runner reports failure as an error event followed by done.
                # Keeping the message is what lets the record say why a run
                # failed rather than only that it did.
                error = event.message
            if event.type == "done":
                outcome = app_db.RunStatus(event.status)
                # Record the outcome *before* publishing the terminal event. A
                # client that sees "done" may immediately ask about the run, and
                # would otherwise be told it is still running.
                settle(
                    outcome,
                    error if outcome is app_db.RunStatus.FAILED else None,
                    answer if outcome is app_db.RunStatus.SUCCEEDED else None,
                )
            run_bus.publish(redis, run_id, event)

        if not settled:
            # run_agent guarantees exactly one done event. If that ever stops
            # being true, record a failure rather than leave a row running for
            # good.
            logger.error("run %s produced no terminal event", run_id)
            settle(app_db.RunStatus.FAILED, "run ended without a terminal event")

        logger.info("run %s finished: %s", run_id, outcome.value)
        return outcome.value

    except Exception as exc:
        logger.exception("run %s crashed", run_id)
        detail = f"{type(exc).__name__}: {exc}"
        # Publish before recording here, the opposite of the happy path: the
        # thing that just failed may well be the database, and a client waiting
        # on this stream has to be released either way.
        run_bus.publish(redis, run_id, Error(message=detail))
        run_bus.publish(redis, run_id, Done(run_id=run_id, status="failed"))
        settle(app_db.RunStatus.FAILED, detail)
        raise
    finally:
        redis.close()
