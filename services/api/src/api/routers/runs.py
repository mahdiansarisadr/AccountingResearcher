"""Reporting, streaming, and cancelling agent runs.

Postgres is the record of a run; Redis carries it while it happens. A run's
status, error and timings are read from the ``app.runs`` table, so they survive a
restart and outlive the hour that its event log stays in Redis.

Starting a run lives under ``POST /threads/{id}/runs``: a run belongs to a
conversation, and that conversation is what supplies its history. These routes
are the ones that address a run by id — ask about it, watch it, stop it — and
every lookup is scoped to the signed-in user.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from uuid import UUID

import app_db
import run_bus
from accounting_research.agent.events import Done
from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..deps import (
    CurrentUser,
    RedisDep,
    SessionDep,
    SessionFactoryDep,
    get_redis,
)
from ..schemas import RunResponse
from ..settings import get_api_settings
from ..sse import SSE_HEADERS, comment, format_event

logger = logging.getLogger("api.runs")

router = APIRouter(prefix="/runs", tags=["runs"])


def _load(session: Session, run_id: UUID, owner: app_db.User) -> app_db.Run:
    """This user's run, or a 404.

    Someone else's run is reported as missing rather than forbidden: confirming
    that an id exists but belongs to another person is itself a disclosure.
    """
    run = app_db.get_owned_run(session, run_id, owner.id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown run")
    return run


@router.get("/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, user: CurrentUser, session: SessionDep) -> RunResponse:
    return RunResponse.of(_load(session, run_id, user))


@router.post(
    "/{run_id}/cancel", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED
)
def cancel_run(
    run_id: UUID, user: CurrentUser, session: SessionDep, redis: RedisDep
) -> RunResponse:
    """Ask a run to stop.

    Cooperative by design: the job checks between events and stops at the next
    boundary, so it can report ``cancelled`` and release its connections instead
    of being killed mid-query. The run in the response is therefore still
    running — 202 says the request was accepted, not that it has taken effect.
    """
    run = _load(session, run_id, user)
    if run.status.is_terminal:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"run already {run.status.value}",
        )

    run_bus.request_cancel(redis, str(run_id))
    logger.info("cancel requested for run %s", run_id)
    return RunResponse.of(run)


def _event_stream(run_id: UUID, last_event_id: str | None) -> Iterator[str]:
    """Yield SSE records for a run until it finishes.

    A plain (non-async) generator on purpose: Starlette runs it in a worker
    thread, so the blocking Redis read does not stall the event loop. That costs
    one thread per open stream, which is the known ceiling on concurrent viewers
    and the reason to move to redis.asyncio if this ever serves a crowd.
    """
    redis = get_redis()
    settings = get_api_settings()

    # Browsers resend the last id they saw after a dropped connection, so a
    # reconnect resumes instead of replaying the whole run.
    cursor = last_event_id or run_bus.START
    yield comment(f"stream open for run {str(run_id)}")

    saw_done = False
    try:
        for stream_id, event in run_bus.read_events(
            redis,
            str(run_id),
            last_id=cursor,
            idle_timeout_s=settings.stream_idle_timeout_seconds,
        ):
            yield format_event(event.type, event.model_dump_json(), event_id=stream_id)
            if event.type == "done":
                saw_done = True
    except GeneratorExit:
        # The client went away. Nothing to clean up: the run keeps going in the
        # worker and its events stay in Redis for a later reconnect.
        logger.info("client disconnected from run %s", run_id)
        raise

    if not saw_done:
        # The run produced nothing for the idle timeout. Told as a transport
        # error so the client stops waiting; the run itself may still complete.
        logger.warning("stream for run %s timed out", run_id)
        yield format_event(
            "error",
            '{"type":"error","message":"stream timed out waiting for events"}',
        )


@router.get("/{run_id}/stream")
def stream_run(
    run_id: UUID,
    user: CurrentUser,
    make_session: SessionFactoryDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> Response:
    """Stream a run's events as they happen."""
    # A transaction opened and closed here rather than the request-scoped
    # session: FastAPI keeps a dependency open until the response is complete,
    # and for a stream that is the whole run — a database connection held for
    # minutes to answer one question asked up front.
    with make_session() as session:
        run = app_db.get_owned_run(session, run_id, user.id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="unknown run"
            )
        settled = run.status if run.status.is_terminal else None

    # A run whose events have aged out of Redis has nothing left to stream. One
    # done event rebuilt from the record beats holding the connection open until
    # the idle timeout only to conclude the same thing.
    if settled is not None and not run_bus.has_events(get_redis(), str(run_id)):
        replayed = Done(run_id=str(run_id), status=settled.value)
        return StreamingResponse(
            iter([format_event(replayed.type, replayed.model_dump_json())]),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return StreamingResponse(
        _event_stream(run_id, last_event_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
