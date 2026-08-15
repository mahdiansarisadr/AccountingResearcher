"""Reading and writing run records.

The transitions live here rather than in the API and the worker because both
processes drive the same lifecycle, and the guards below are what keep it
single-directional however they interleave.

Every function takes a :class:`~sqlalchemy.orm.Session` and never commits, so a
caller can compose several writes into one transaction. Use
:func:`app_db.session_scope` for the commit boundary.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import Run, RunStatus

# An exception message can be arbitrarily long (a driver error may carry a whole
# query). Keep the head, which is where the type and cause are.
MAX_ERROR_CHARS = 2_000


def create_run(
    session: Session, run_id: uuid.UUID, user_id: uuid.UUID, thread_id: uuid.UUID
) -> Run:
    """Record a newly accepted run, queued and not yet started.

    ``user_id`` is denormalised from the thread so a run lookup can enforce
    ownership without a join. Both are set here, together, and never updated.
    """
    run = Run(
        id=run_id, user_id=user_id, thread_id=thread_id, status=RunStatus.QUEUED
    )
    session.add(run)
    # Flush to emit the INSERT, then refresh to load created_at, which the
    # database generates. Doing it now means the caller can read the row after
    # the session has closed.
    session.flush()
    session.refresh(run)
    return run


def get_run(session: Session, run_id: uuid.UUID) -> Run | None:
    """Fetch a run regardless of who owns it.

    For system paths only — the worker, migrations, tests. Anything answering a
    request must use :func:`get_owned_run`, or one user's id becomes enough to
    read another user's run.
    """
    return session.get(Run, run_id)


def get_owned_run(session: Session, run_id: uuid.UUID, owner_id: uuid.UUID) -> Run | None:
    """Fetch a run only if it belongs to this user.

    Ownership is part of the query rather than a check on the result, so a run
    someone else owns is indistinguishable from one that does not exist and no
    caller can forget to compare.
    """
    return session.scalar(select(Run).where(Run.id == run_id, Run.user_id == owner_id))


def mark_running(session: Session, run_id: uuid.UUID) -> bool:
    """Move a queued run to running. Returns whether it moved.

    Conditional on the run still being queued, which makes a redelivered job
    harmless: a second attempt cannot rewind ``started_at`` or resurrect a run
    that was already cancelled while it sat in the queue.
    """
    result = session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == RunStatus.QUEUED)
        .values(status=RunStatus.RUNNING, started_at=func.now())
    )
    return result.rowcount == 1


def mark_finished(
    session: Session,
    run_id: uuid.UUID,
    status: RunStatus,
    error: str | None = None,
) -> bool:
    """Settle a run. Returns whether this call was the one that settled it.

    Conditional on the run not having settled already, so the first terminal
    outcome wins. That is what stops a crash handler from overwriting a
    deliberate ``cancelled`` with ``failed`` on the way out.
    """
    if not status.is_terminal:
        raise ValueError(f"{status} is not a terminal status")

    result = session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)))
        .values(
            status=status,
            error=error[:MAX_ERROR_CHARS] if error else None,
            finished_at=func.now(),
        )
    )
    return result.rowcount == 1


def count_active_runs(session: Session, user_id: uuid.UUID) -> int:
    """How many of this user's runs have not yet settled.

    The per-thread check in ``has_active_run`` stops two answers interleaving on
    one conversation. This one is the cost cap: one account, every thread.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(Run)
            .where(
                Run.user_id == user_id,
                Run.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)),
            )
        )
        or 0
    )
