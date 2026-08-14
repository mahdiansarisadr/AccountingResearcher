"""Postgres persistence for the application schema, shared by the API and the worker.

The counterpart to ``run_bus``: that package carries a run's events while it is
happening, this one records what became of it. Exports are flat for the same
reason — ``app_db.mark_finished(...)`` next to ``run_bus.publish(...)`` reads as
one vocabulary rather than two.
"""

from .base import SCHEMA, Base
from .engine import get_engine, normalize_url, session_scope
from .models import Run, RunStatus, User, UserRole
from .runs import (
    MAX_ERROR_CHARS,
    create_run,
    get_owned_run,
    get_run,
    mark_finished,
    mark_running,
)
from .users import (
    get_by_email,
    get_by_id,
    list_users,
    normalize_email,
    record_login,
    update_user,
    upsert_user,
)

__all__ = [
    "MAX_ERROR_CHARS",
    "SCHEMA",
    "Base",
    "Run",
    "RunStatus",
    "User",
    "UserRole",
    "create_run",
    "get_by_email",
    "get_by_id",
    "get_engine",
    "get_owned_run",
    "get_run",
    "list_users",
    "mark_finished",
    "mark_running",
    "normalize_email",
    "normalize_url",
    "record_login",
    "session_scope",
    "update_user",
    "upsert_user",
]
