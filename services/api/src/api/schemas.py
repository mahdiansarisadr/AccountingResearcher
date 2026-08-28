"""Response shapes shared by more than one router."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import app_db
from pydantic import BaseModel


class UserResponse(BaseModel):
    """A user as returned by /me and by the admin endpoints.

    One shape for both: an admin listing users should see exactly what those
    users see about themselves, and a second near-identical model is a second
    thing to keep in step.
    """

    id: UUID
    email: str
    name: str | None = None
    avatar_url: str | None = None
    role: app_db.UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None

    @classmethod
    def of(cls, user: app_db.User) -> UserResponse:
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            role=user.role,
            is_active=user.is_active,
            created_at=user.created_at,
            last_login_at=user.last_login_at,
        )


class ThreadResponse(BaseModel):
    """A conversation as listed and as returned on create."""

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, thread: app_db.Thread) -> ThreadResponse:
        return cls(
            id=thread.id,
            title=thread.title,
            created_at=thread.created_at,
            updated_at=thread.updated_at,
        )


class ThreadFileResponse(BaseModel):
    """A dataset uploaded onto a conversation."""

    name: str
    size: int
    modified_at: datetime


class MessageResponse(BaseModel):
    """One persisted turn. ``payload`` carries structured extras when present."""

    id: UUID
    role: app_db.MessageRole
    content: str
    payload: dict | None = None
    created_at: datetime

    @classmethod
    def of(cls, message: app_db.Message) -> MessageResponse:
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            payload=message.payload,
            created_at=message.created_at,
        )


class RunResponse(BaseModel):
    """A run as recorded. The same shape wherever a run is returned."""

    run_id: UUID
    thread_id: UUID
    status: app_db.RunStatus
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def of(cls, run: app_db.Run) -> RunResponse:
        return cls(
            run_id=run.id,
            thread_id=run.thread_id,
            status=run.status,
            error=run.error,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )
