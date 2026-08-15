"""Conversations: list them, open one, read its history, ask the next question.

A thread is scoped to its owner in every query, so another user's id is of no
use. History is loaded from persisted messages rather than accepted from the
client — that is what makes a reload show the same conversation, and what stops
a caller from forging prior turns.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

import app_db
import run_bus
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import CurrentUser, QueueDep, RedisDep, SessionDep, SettingsDep
from ..schemas import MessageResponse, RunResponse, ThreadResponse

logger = logging.getLogger("api.threads")

router = APIRouter(prefix="/threads", tags=["threads"])


class CreateThreadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class StartRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)


def _load_thread(
    session: Session, thread_id: UUID, owner: app_db.User
) -> app_db.Thread:
    thread = app_db.get_owned_thread(session, thread_id, owner.id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown thread")
    return thread


@router.get("", response_model=list[ThreadResponse])
def list_threads(user: CurrentUser, session: SessionDep) -> list[ThreadResponse]:
    return [ThreadResponse.of(thread) for thread in app_db.list_threads(session, user.id)]


@router.post("", response_model=ThreadResponse, status_code=status.HTTP_201_CREATED)
def create_thread(
    user: CurrentUser,
    session: SessionDep,
    request: CreateThreadRequest = CreateThreadRequest(),
) -> ThreadResponse:
    return ThreadResponse.of(app_db.create_thread(session, user.id, request.title))


@router.get("/{thread_id}", response_model=ThreadResponse)
def get_thread(thread_id: UUID, user: CurrentUser, session: SessionDep) -> ThreadResponse:
    return ThreadResponse.of(_load_thread(session, thread_id, user))


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_thread(
    thread_id: UUID, user: CurrentUser, session: SessionDep, redis: RedisDep
) -> Response:
    """Remove a conversation and everything in it.

    In-flight runs are asked to stop first, so a worker that has already picked
    one up still exits at the next boundary rather than writing into a thread
    that no longer exists.
    """
    thread = _load_thread(session, thread_id, user)
    for run in thread.runs:
        if not run.status.is_terminal:
            run_bus.request_cancel(redis, str(run.id))
    app_db.delete_owned_thread(session, thread.id, user.id)
    logger.info("deleted thread %s", thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{thread_id}/messages", response_model=list[MessageResponse])
def list_messages(
    thread_id: UUID, user: CurrentUser, session: SessionDep
) -> list[MessageResponse]:
    _load_thread(session, thread_id, user)
    return [
        MessageResponse.of(message) for message in app_db.list_messages(session, thread_id)
    ]


@router.post(
    "/{thread_id}/runs", response_model=RunResponse, status_code=status.HTTP_202_ACCEPTED
)
def start_run(
    thread_id: UUID,
    request: StartRunRequest,
    user: CurrentUser,
    session: SessionDep,
    queue: QueueDep,
    settings: SettingsDep,
) -> RunResponse:
    """Queue a run on this thread and return immediately.

    202 rather than 200: the work has been accepted, not completed. The client
    then opens the stream to watch it happen.
    """
    thread = _load_thread(session, thread_id, user)
    if app_db.has_active_run(session, thread.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this thread already has a run in progress",
        )
    if app_db.count_active_runs(session, user.id) >= settings.max_concurrent_runs_per_user:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many runs in progress",
        )

    # History is whatever this thread already contains, taken before the new
    # question is appended so the agent is not handed the same turn twice.
    history = app_db.conversation_history(session, thread.id)
    app_db.append_message(
        session,
        thread.id,
        app_db.MessageRole.USER,
        request.message,
        title_from=request.message,
    )

    run_id = uuid4()
    run = app_db.create_run(session, run_id, user.id, thread.id)

    # Commit before enqueuing, not after. A worker can claim the job within
    # milliseconds and its first act is to look the run up by id, so the row has
    # to be visible to other transactions before the job exists.
    session.commit()

    try:
        queue.enqueue(
            run_bus.JOB_FUNCTION,
            str(run_id),
            request.message,
            history,
            job_timeout=settings.run_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - reported to the caller as 503
        # The row is committed but nothing will ever execute it. Settle it here
        # rather than leave a run stuck at "queued" for good. The user message
        # stays: they asked, and the record of that is more useful than pretending
        # they did not.
        logger.exception("could not queue run %s", run_id)
        app_db.mark_finished(
            session, run_id, app_db.RunStatus.FAILED, f"could not queue run: {exc}"
        )
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="could not queue run"
        ) from exc

    logger.info("queued run %s on thread %s", run_id, thread_id)
    return RunResponse.of(run)
