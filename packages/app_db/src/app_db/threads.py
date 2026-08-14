"""Reading and writing conversations.

A thread is the durable unit a user reopens; messages are what they see; the
history handed to the agent is derived from those messages rather than trusted
from the client. That is the whole of per-user isolation for a conversation:
every query here is scoped to an owner, or is reached only through a thread
already so scoped.

Every function takes a :class:`~sqlalchemy.orm.Session` and never commits, so a
caller can compose several writes into one transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Message, MessageRole, Run, RunStatus, Thread

DEFAULT_TITLE = "New conversation"
TITLE_MAX_CHARS = 200
# How many user/assistant turns the agent is given. Everything is kept; only the
# window sent to the model is capped, so a long conversation cannot grow the
# prompt without bound.
HISTORY_WINDOW = 40


def create_thread(
    session: Session, user_id: uuid.UUID, title: str | None = None
) -> Thread:
    """Open an empty conversation belonging to this user."""
    thread = Thread(user_id=user_id, title=_normalise_title(title))
    session.add(thread)
    session.flush()
    session.refresh(thread)
    return thread


def get_owned_thread(
    session: Session, thread_id: uuid.UUID, owner_id: uuid.UUID
) -> Thread | None:
    """Fetch a thread only if it belongs to this user.

    Ownership is part of the query rather than a check on the result, so a thread
    someone else owns is indistinguishable from one that does not exist.
    """
    return session.scalar(
        select(Thread).where(Thread.id == thread_id, Thread.user_id == owner_id)
    )


def list_threads(session: Session, owner_id: uuid.UUID) -> list[Thread]:
    """This user's conversations, most recently active first."""
    return list(
        session.scalars(
            select(Thread)
            .where(Thread.user_id == owner_id)
            .order_by(Thread.updated_at.desc(), Thread.created_at.desc())
        ).all()
    )


def delete_owned_thread(
    session: Session, thread_id: uuid.UUID, owner_id: uuid.UUID
) -> bool:
    """Delete a conversation and everything in it. Returns whether it existed."""
    thread = get_owned_thread(session, thread_id, owner_id)
    if thread is None:
        return False
    session.delete(thread)
    session.flush()
    return True


def has_active_run(session: Session, thread_id: uuid.UUID) -> bool:
    """True if a run on this thread has not yet settled.

    Used to refuse a second question while the first is still being answered, so
    two jobs cannot interleave history on the same conversation.
    """
    return (
        session.scalar(
            select(Run.id).where(
                Run.thread_id == thread_id,
                Run.status.in_((RunStatus.QUEUED, RunStatus.RUNNING)),
            )
        )
        is not None
    )


def touch_thread(session: Session, thread_id: uuid.UUID) -> None:
    """Mark a thread as recently active. Dated by the database, like everything else."""
    thread = session.get(Thread, thread_id)
    if thread is None:
        return
    thread.updated_at = func.now()
    session.flush()
    session.refresh(thread)


def append_message(
    session: Session,
    thread_id: uuid.UUID,
    role: MessageRole,
    content: str,
    payload: dict | None = None,
    *,
    title_from: str | None = None,
) -> Message:
    """Add a message and bump the thread's activity.

    ``title_from`` sets the title from the first user question, but only when the
    thread still has the default — a title the user chose is left alone.
    """
    next_seq = session.scalar(
        select(func.coalesce(func.max(Message.seq), 0) + 1).where(
            Message.thread_id == thread_id
        )
    )
    message = Message(
        thread_id=thread_id,
        seq=next_seq or 1,
        role=role,
        content=content,
        payload=payload,
    )
    session.add(message)

    thread = session.get(Thread, thread_id)
    if thread is not None:
        thread.updated_at = func.now()
        if title_from and thread.title == DEFAULT_TITLE:
            thread.title = _normalise_title(title_from)

    session.flush()
    session.refresh(message)
    return message


def list_messages(session: Session, thread_id: uuid.UUID) -> list[Message]:
    """Every message in a thread, oldest first."""
    return list(
        session.scalars(
            select(Message)
            .where(Message.thread_id == thread_id)
            .order_by(Message.seq)
        ).all()
    )


def conversation_history(session: Session, thread_id: uuid.UUID) -> list[dict[str, str]]:
    """Prior user/assistant turns, as ``run_agent`` expects them.

    Tool messages are skipped: the model is given the dialogue, not a replay of
    its own function calls. The window is the tail, so a long thread still fits
    in a prompt.
    """
    messages = [
        message
        for message in list_messages(session, thread_id)
        if message.role in (MessageRole.USER, MessageRole.ASSISTANT)
    ]
    return [
        {"role": message.role.value, "content": message.content}
        for message in messages[-HISTORY_WINDOW:]
    ]


def _normalise_title(title: str | None) -> str:
    compact = " ".join((title or "").split())
    if not compact:
        return DEFAULT_TITLE
    return compact[:TITLE_MAX_CHARS]
