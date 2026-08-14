"""Reading and writing user records.

Policy lives in the API — who is allowed in, what a rejection looks like. This
module only records the decision, so the rules stay in one readable place instead
of half here and half there.

Every function takes a :class:`~sqlalchemy.orm.Session` and never commits, so a
caller can compose several writes into one transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import User, UserRole


def normalize_email(email: str) -> str:
    """Lowercase and trim an address.

    Google treats the local part as case-insensitive, so storing what was typed
    would let one person become two rows.
    """
    return email.strip().lower()


def get_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == normalize_email(email)))


def get_by_id(session: Session, user_id: uuid.UUID) -> User | None:
    return session.get(User, user_id)


def upsert_user(
    session: Session,
    *,
    email: str,
    name: str | None = None,
    avatar_url: str | None = None,
    initial_admin: bool = False,
) -> User:
    """Create the user on first sign-in, or refresh what Google now says.

    Profile fields are Google's to change, so they are overwritten — but only
    when supplied, so a claim missing from one response cannot blank a value
    that a previous one provided.
    """
    email = normalize_email(email)
    user = get_by_email(session, email)

    if user is None:
        user = User(
            email=email,
            name=name,
            avatar_url=avatar_url,
            role=UserRole.ADMIN if initial_admin else UserRole.MEMBER,
        )
        session.add(user)
    else:
        if name:
            user.name = name
        if avatar_url:
            user.avatar_url = avatar_url

    if initial_admin:
        # The configured first admin is restored on every sign-in. It is
        # configuration rather than data, and it is what stops an instance from
        # being locked out of its own user management.
        user.role = UserRole.ADMIN
        user.is_active = True

    session.flush()
    session.refresh(user)
    return user


def record_login(session: Session, user: User) -> None:
    """Stamp a successful sign-in. Called only once access has been granted."""
    # func.now() rather than a Python timestamp, so every row in this schema is
    # dated by the same clock.
    user.last_login_at = func.now()
    session.flush()
    session.refresh(user)


def list_users(session: Session) -> list[User]:
    """Every user, ordered by email so the admin list does not shuffle."""
    return list(session.scalars(select(User).order_by(User.email)).all())


def update_user(
    session: Session,
    user_id: uuid.UUID,
    *,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> User | None:
    """Change a user's role or access. Returns None if there is no such user.

    Both fields are optional and only applied when given, so a request that sets
    one cannot silently reset the other.
    """
    user = get_by_id(session, user_id)
    if user is None:
        return None

    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active

    session.flush()
    return user
