"""Tables in the application schema.

``users``, ``threads``, ``messages`` and ``runs``. Application state lives here;
the accounting data the agent queries lives in ``public``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def _enum_column(enum: type[StrEnum], name: str) -> Enum:
    """A string-valued enum column.

    native_enum=False stores the value as VARCHAR rather than a Postgres ENUM
    type. Adding a member to a CHECK constraint is an ordinary migration;
    ``ALTER TYPE ... ADD VALUE`` cannot run inside a transaction, so it is not.

    create_constraint stays off because each table declares its constraint
    explicitly instead — one attached to the type is invisible to Alembic's
    autogenerate, which then reports it as dropped on every run.
    """
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=False,
        length=16,
        # Without this SQLAlchemy persists member *names* ("QUEUED").
        values_callable=lambda members: [member.value for member in members],
    )


def _check_values(column: str, enum: type[StrEnum], name: str) -> CheckConstraint:
    """The database-level backstop for anything writing outside the model."""
    allowed = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({allowed})", name=name)


class UserRole(StrEnum):
    """What a signed-in person may do.

    Two roles on purpose: this is an internal single-team tool, and a permission
    matrix nobody needs is a permission matrix nobody maintains.
    """

    ADMIN = "admin"
    MEMBER = "member"


class MessageRole(StrEnum):
    """Who produced a message.

    ``tool`` is stored so a later UI can replay what the agent did; the history
    handed back to the model is only ``user`` and ``assistant``, which is the
    shape ``run_agent`` already accepts.
    """

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class User(Base):
    """Someone who has signed in with a company Google account.

    Rows are created on first sign-in rather than invited ahead of time: the
    domain check already decides who is allowed, so a separate invitation step
    would only be a second list to keep in step with the first.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    # The identity Google asserts, lowercased on the way in. Unique because it is
    # how a returning user is recognised.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    name: Mapped[str | None] = mapped_column(String(200), default=None)
    avatar_url: Mapped[str | None] = mapped_column(Text, default=None)

    role: Mapped[UserRole] = mapped_column(
        _enum_column(UserRole, "user_role"), nullable=False, default=UserRole.MEMBER
    )

    # Deactivation rather than deletion: a thread refers to its owner, and history
    # that loses its author is worse than an account that cannot sign in. Checked
    # on every request, so revoking access takes effect at once.
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    threads: Mapped[list[Thread]] = relationship(back_populates="user")
    runs: Mapped[list[Run]] = relationship(back_populates="user")

    __table_args__ = (_check_values("role", UserRole, "user_role"),)

    def __repr__(self) -> str:
        return f"<User {self.email} {self.role}>"


class Thread(Base):
    """One conversation.

    The durable unit the UI lists and reopens. A run is one turn inside it;
    messages are what remain after the run's event log has expired.
    """

    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Bumped when a message is added, so the sidebar can sort by recent activity
    # rather than by when the conversation was opened.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="threads")
    messages: Mapped[list[Message]] = relationship(
        back_populates="thread", cascade="all, delete-orphan", order_by="Message.seq"
    )
    runs: Mapped[list[Run]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Thread {self.id} {self.title!r}>"


class Message(Base):
    """One turn in a conversation, kept after the run that produced it is gone."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)

    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Monotonic within a thread. created_at is the transaction's clock in
    # Postgres, so two messages written in one transaction would otherwise be
    # unordered; UUID ids are not sequential either.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)

    role: Mapped[MessageRole] = mapped_column(
        _enum_column(MessageRole, "message_role"), nullable=False
    )

    # The text a later turn, or a UI, actually reads. Structured extras (citations,
    # SQL, confidence) sit in payload so they can be shown without being stuffed
    # into this string.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    thread: Mapped[Thread] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint("thread_id", "seq"),
        _check_values("role", MessageRole, "message_role"),
    )

    def __repr__(self) -> str:
        return f"<Message {self.id} {self.role}>"


class RunStatus(StrEnum):
    """The lifecycle of a run, from accepted to settled.

    A ``StrEnum`` so the value is already the wire format used by the run event
    contract and the JSON API — no translation table to keep in step.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """True once the run has settled and its status can no longer change."""
        return self in _TERMINAL


_TERMINAL = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


class Run(Base):
    """One agent execution.

    The durable record of a run. Redis holds the event log so a browser can
    replay or resume a stream, but that expires after an hour; this row is what
    remains, and it is what makes run status answerable after a restart.

    There is no column for the question asked: that lives on ``messages``. A run
    points at the thread it belongs to, and at the user, so a lookup does not
    have to join to enforce isolation.
    """

    __tablename__ = "runs"

    # Generated by the API rather than the database: the caller is handed the id
    # in the response and needs it before the row is written.
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # CASCADE: deleting a conversation deletes the executions inside it. The
    # event log in Redis expires on its own.
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=False, index=True
    )

    status: Mapped[RunStatus] = mapped_column(
        _enum_column(RunStatus, "run_status"), nullable=False, index=True
    )

    # Why a run failed, in the words of whatever failed. Null on every other
    # outcome, including cancellation.
    error: Mapped[str | None] = mapped_column(Text, default=None)

    # Server-side defaults throughout: the API and the worker are separate
    # processes (separate containers in Compose), so taking every timestamp from
    # the database is the only way they cannot disagree by clock skew.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Set when a worker picks the run up, so queue wait is created_at -> started_at
    # and execution time is started_at -> finished_at.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    user: Mapped[User] = relationship(back_populates="runs")
    thread: Mapped[Thread] = relationship(back_populates="runs")

    __table_args__ = (_check_values("status", RunStatus, "run_status"),)

    def __repr__(self) -> str:
        return f"<Run {self.id} {self.status}>"
