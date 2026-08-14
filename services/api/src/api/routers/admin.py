"""User management, for admins.

Accounts appear by themselves — anyone on the company domain who signs in gets
one, as a member. What an admin does here is the part that cannot be automated:
promote someone, or take access away.
"""

from __future__ import annotations

import logging
from uuid import UUID

import app_db
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, model_validator

from ..deps import AdminUser, SessionDep
from ..schemas import UserResponse

logger = logging.getLogger("api.admin")

router = APIRouter(prefix="/admin", tags=["admin"])


class UpdateUserRequest(BaseModel):
    """Both fields optional, and only what is present is applied.

    So a request changing a role cannot silently reset access as a side effect of
    leaving the other field out.
    """

    role: app_db.UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _require_something_to_do(self) -> UpdateUserRequest:
        if self.role is None and self.is_active is None:
            raise ValueError("provide role, is_active, or both")
        return self


@router.get("/users", response_model=list[UserResponse])
def list_users(_admin: AdminUser, session: SessionDep) -> list[UserResponse]:
    return [UserResponse.of(user) for user in app_db.list_users(session)]


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: UUID, payload: UpdateUserRequest, admin: AdminUser, session: SessionDep
) -> UserResponse:
    """Set a user's role, their access, or both."""
    if user_id == admin.id:
        # Demoting or deactivating yourself is how an instance ends up with no
        # working administrator. Another admin can still do it, which keeps the
        # rule from making anyone permanent.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="an admin cannot change their own role or access",
        )

    user = app_db.update_user(
        session, user_id, role=payload.role, is_active=payload.is_active
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown user")

    logger.info(
        "%s updated %s: role=%s active=%s",
        admin.email,
        user.email,
        user.role.value,
        user.is_active,
    )
    return UserResponse.of(user)
