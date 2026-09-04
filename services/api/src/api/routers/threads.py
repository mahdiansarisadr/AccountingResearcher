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
from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from mleng.core.experiments import list_experiment_runs
from mleng.core.progress import summarise_progress
from mleng.core.workspace import (
    delete_thread_uploads,
    list_uploads,
    save_upload,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import CurrentUser, QueueDep, RedisDep, SessionDep, SettingsDep
from ..schemas import (
    ExperimentRunResponse,
    MessageResponse,
    ProgressResponse,
    ProgressStepResponse,
    RunResponse,
    ThreadFileResponse,
    ThreadResponse,
)

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
    delete_thread_uploads(str(user.id), str(thread.id))
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


@router.get("/{thread_id}/files", response_model=list[ThreadFileResponse])
def list_thread_files(
    thread_id: UUID, user: CurrentUser, session: SessionDep
) -> list[ThreadFileResponse]:
    _load_thread(session, thread_id, user)
    return [
        ThreadFileResponse(name=item.name, size=item.size, modified_at=item.modified_at)
        for item in list_uploads(str(user.id), str(thread_id))
    ]


@router.get("/{thread_id}/experiments", response_model=list[ExperimentRunResponse])
def list_thread_experiments(
    thread_id: UUID, user: CurrentUser, session: SessionDep
) -> list[ExperimentRunResponse]:
    """Training runs logged to this conversation's MLflow experiment."""
    _load_thread(session, thread_id, user)
    rows = list_experiment_runs(str(user.id), str(thread_id))
    return [
        ExperimentRunResponse(
            run_id=row.run_id,
            name=row.name,
            status=row.status,
            started_at=row.started_at,
            model=row.model,
            task=row.task,
            hypothesis=row.hypothesis or None,
            primary_metric=row.primary_metric,
            primary_value=row.primary_value,
            metrics=row.metrics,
            recipe_version=row.recipe_version,
            recipe_parent=row.recipe_parent,
            recipe_kind=row.recipe_kind,
            reused=row.reused,
            split_seed=row.split_seed,
            error=row.error,
        )
        for row in rows
    ]


@router.get("/{thread_id}/progress", response_model=ProgressResponse)
def thread_progress(
    thread_id: UUID, user: CurrentUser, session: SessionDep
) -> ProgressResponse:
    """How the score moved across this conversation's whole search."""
    _load_thread(session, thread_id, user)
    progress = summarise_progress(str(user.id), str(thread_id))
    return ProgressResponse(
        metric=progress.metric,
        steps=[
            ProgressStepResponse(
                order=step.order,
                version=step.version,
                run_id=step.run_id,
                at=step.at,
                value=step.value,
                best_so_far=step.best_so_far,
                improved=step.improved,
                gain=step.gain,
                note=step.note,
                failed=step.failed,
                error=step.error,
            )
            for step in progress.steps
        ],
        first=progress.first,
        best=progress.best,
        best_version=progress.best_version,
        total_gain=progress.total_gain,
        versions=progress.versions,
        runs=progress.runs,
        failed=progress.failed,
        noise=progress.noise,
        seconds=progress.seconds,
        improved=progress.improved,
    )


@router.post(
    "/{thread_id}/files",
    response_model=ThreadFileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_thread_file(
    thread_id: UUID,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    file: UploadFile = File(...),
) -> ThreadFileResponse:
    _load_thread(session, thread_id, user)
    data = await file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413, detail="file too large (max 25 MB)"
        )
    try:
        stored = save_upload(str(user.id), str(thread_id), file.filename or "", data)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    logger.info("uploaded %s on thread %s", stored.name, thread_id)
    return ThreadFileResponse(
        name=stored.name, size=stored.size, modified_at=stored.modified_at
    )


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
