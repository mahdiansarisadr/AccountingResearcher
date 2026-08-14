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
