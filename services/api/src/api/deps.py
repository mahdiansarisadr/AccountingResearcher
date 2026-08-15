"""Shared resources for request handlers.

Exposed as FastAPI dependencies rather than reached for directly, so a test can
swap Redis or the database for a stand-in through ``dependency_overrides``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from functools import lru_cache
from typing import Annotated

import app_db
import run_bus
from fastapi import Depends, HTTPException, Request, status
from redis import Redis
from rq import Queue
from sqlalchemy.orm import Session

from .security import read_session
from .settings import ApiSettings, get_api_settings


@lru_cache
def get_redis() -> Redis:
    """One connection pool for the process.

    redis-py pools connections internally and is thread-safe, so a module-level
    client is the intended usage rather than a per-request connection.
    """
    settings = get_api_settings()
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.probe_timeout,
    )


@lru_cache
def get_queue() -> Queue:
    return Queue(run_bus.QUEUE_NAME, connection=get_redis())


@contextmanager
def db_session() -> Iterator[Session]:
    """A transaction against the application schema.

    For work that must not outlive the handler — a streaming response holds its
    dependencies open until the last byte, which would pin a connection for the
    length of an entire agent run.
    """
    with app_db.session_scope(get_api_settings().database_url) as session:
        yield session


def get_session() -> Iterator[Session]:
    """One transaction per request, committed when the response is finished."""
    with db_session() as session:
        yield session


SessionFactory = Callable[[], AbstractContextManager[Session]]


def get_session_factory() -> SessionFactory:
    """A way to open a transaction, for handlers that must close it themselves.

    Handed out as a factory rather than a session because a generator dependency
    stays open until the response is complete, which a streaming endpoint cannot
    afford. Injected all the same, so tests still have a seam.
    """
    return db_session


RedisDep = Annotated[Redis, Depends(get_redis)]
QueueDep = Annotated[Queue, Depends(get_queue)]
SessionDep = Annotated[Session, Depends(get_session)]
SessionFactoryDep = Annotated[SessionFactory, Depends(get_session_factory)]
SettingsDep = Annotated[ApiSettings, Depends(get_api_settings)]


def current_user(
    request: Request, make_session: SessionFactoryDep, settings: SettingsDep
) -> app_db.User:
    """The signed-in user, or a refusal.

    Deliberately loads the user from the database rather than trusting the
    cookie's contents. The cookie says *who*; the database says whether they are
    still allowed and what they may do, so revoking access takes effect on the
    next request instead of whenever a week-old token expires.

    Uses a transaction of its own, opened and closed here, so that authenticating
    a streaming request does not hold a database connection for the length of an
    agent run.
    """
    unauthenticated = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="not signed in"
    )

    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise unauthenticated

    user_id = read_session(token, settings)
    if user_id is None:
        raise unauthenticated

    with make_session() as session:
        user = app_db.get_by_id(session, user_id)
        if user is None:
            # A valid signature for an account that no longer exists.
            raise unauthenticated
        if not user.is_active:
            # 403, not 401: the credential is good, the account is not. Retrying
            # with a fresh sign-in would not help, and saying so avoids a login
            # loop.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="this account is deactivated"
            )
        request.state.user_id = str(user.id)
        return user


def require_admin(user: Annotated[app_db.User, Depends(current_user)]) -> app_db.User:
    if user.role is not app_db.UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="admin access required"
        )
    return user


CurrentUser = Annotated[app_db.User, Depends(current_user)]
AdminUser = Annotated[app_db.User, Depends(require_admin)]
