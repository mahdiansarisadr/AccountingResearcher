"""Postgres persistence for the application schema, shared by the API and the worker.

The counterpart to ``run_bus``: that package carries a run's events while it is
happening, this one records what became of it. Exports are flat for the same
reason — ``app_db.mark_finished(...)`` next to ``run_bus.publish(...)`` reads as
one vocabulary rather than two.
"""

from .base import SCHEMA, Base
from .engine import get_engine, normalize_url, session_scope
from .models import Message, MessageRole, Run, RunStatus, Thread, User, UserRole
from .runs import (
    MAX_ERROR_CHARS,
    create_run,
    get_owned_run,
    get_run,
    mark_finished,
    mark_running,
    count_active_runs,
)
from .threads import (
    DEFAULT_TITLE,
    HISTORY_WINDOW,
    append_message,
    conversation_history,
    create_thread,
    delete_owned_thread,
    get_owned_thread,
    has_active_run,
    list_messages,
    list_threads,
    touch_thread,
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
    "DEFAULT_TITLE",
    "HISTORY_WINDOW",
    "MAX_ERROR_CHARS",
    "SCHEMA",
    "Base",
    "Message",
    "MessageRole",
    "Run",
    "RunStatus",
    "Thread",
    "User",
    "UserRole",
    "append_message",
    "conversation_history",
    "count_active_runs",
    "create_run",
    "create_thread",
    "delete_owned_thread",
    "get_by_email",
    "get_by_id",
    "get_engine",
    "get_owned_run",
    "get_owned_thread",
    "get_run",
    "has_active_run",
    "list_messages",
    "list_threads",
    "list_users",
    "mark_finished",
    "mark_running",
    "normalize_email",
    "normalize_url",
    "record_login",
    "session_scope",
    "touch_thread",
    "update_user",
    "upsert_user",
]
