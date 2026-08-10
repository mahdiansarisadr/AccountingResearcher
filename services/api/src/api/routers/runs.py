"""Starting, streaming, and cancelling agent runs.

No authentication and no persistence yet: a run is identified by a generated id
and its events live in Redis. Threads and per-user scoping arrive in later
phases, at which point these routes move under /threads/{id}.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Literal
from uuid import uuid4

import run_bus
from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..deps import get_queue, get_redis
from ..settings import get_api_settings
from ..sse import SSE_HEADERS, comment, format_event

logger = logging.getLogger("api.runs")

router = APIRouter(prefix="/runs", tags=["runs"])


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class StartRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    # The caller owns the conversation until threads are persisted.
    history: list[Turn] = Field(default_factory=list)


class StartRunResponse(BaseModel):
    run_id: str
    status: str


class RunStatusResponse(BaseModel):
    run_id: str
    status: str | None


@router.post("", response_model=StartRunResponse, status_code=status.HTTP_202_ACCEPTED)
def start_run(request: StartRunRequest) -> StartRunResponse:
    """Queue a run and return immediately.

    202 rather than 200: the work has been accepted, not completed. The client
    then opens the stream to watch it happen.
    """
    settings = get_api_settings()
    redis = get_redis()
    run_id = str(uuid4())

    run_bus.set_status(redis, run_id, "queued")
    get_queue().enqueue(
        run_bus.JOB_FUNCTION,
        run_id,
        request.message,
        [turn.model_dump() for turn in request.history],
        job_timeout=settings.run_timeout_seconds,
    )

    logger.info("queued run %s", run_id)
    return StartRunResponse(run_id=run_id, status="queued")


@router.get("/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: str) -> RunStatusResponse:
    current = run_bus.get_status(get_redis(), run_id)
    if current is None:
        raise HTTPException(status_code=404, detail="unknown run")
    return RunStatusResponse(run_id=run_id, status=current)


@router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_run(run_id: str) -> dict[str, str]:
    """Ask a run to stop.

    Cooperative by design: the job checks between events and stops at the next
    boundary, so it can report `cancelled` and release its connections instead of
    being killed mid-query.
    """
    redis = get_redis()
    if run_bus.get_status(redis, run_id) is None:
        raise HTTPException(status_code=404, detail="unknown run")
    run_bus.request_cancel(redis, run_id)
    logger.info("cancel requested for run %s", run_id)
    return {"run_id": run_id, "status": "cancelling"}


def _event_stream(run_id: str, last_event_id: str | None) -> Iterator[str]:
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
    yield comment(f"stream open for run {run_id}")

    saw_done = False
    try:
        for stream_id, event in run_bus.read_events(
            redis,
            run_id,
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
    run_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> Response:
    """Stream a run's events as they happen."""
    if run_bus.get_status(get_redis(), run_id) is None:
        raise HTTPException(status_code=404, detail="unknown run")

    return StreamingResponse(
        _event_stream(run_id, last_event_id),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
