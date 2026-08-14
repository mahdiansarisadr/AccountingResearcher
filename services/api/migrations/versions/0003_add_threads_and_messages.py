"""Add threads and messages, and put every run in a thread.

Conversations arrive with this revision. A run with no thread has nowhere to put
its question or its answer, so ``runs.thread_id`` is mandatory: a nullable thread
would be a filter every future query has to remember.

Existing runs are deleted rather than backfilled. They predate threads, so there
is no conversation to attach them to, and keeping them as orphans would recreate
the unowned-run problem this column exists to close.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

SCHEMA = "app"
ROLES = ("user", "assistant", "tool")


def upgrade() -> None:
    op.create_table(
        "threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_threads_user_id", "threads", ["user_id"], schema=SCHEMA)

    op.create_table(
        "messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("thread_id", "seq"),
        sa.CheckConstraint(
            "role IN (" + ", ".join(f"'{role}'" for role in ROLES) + ")",
            name="message_role",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"], [f"{SCHEMA}.threads.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )
    op.create_index("ix_messages_thread_id", "messages", ["thread_id"], schema=SCHEMA)

    # See the note above: these rows predate the existence of conversations.
    op.execute(f'DELETE FROM "{SCHEMA}".runs')

    op.add_column("runs", sa.Column("thread_id", sa.Uuid(), nullable=False), schema=SCHEMA)
    op.create_index("ix_runs_thread_id", "runs", ["thread_id"], schema=SCHEMA)
    op.create_foreign_key(
        "fk_runs_thread_id_threads",
        "runs",
        "threads",
        ["thread_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_runs_thread_id_threads", "runs", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_index("ix_runs_thread_id", table_name="runs", schema=SCHEMA)
    op.drop_column("runs", "thread_id", schema=SCHEMA)
    op.drop_index("ix_messages_thread_id", table_name="messages", schema=SCHEMA)
    op.drop_table("messages", schema=SCHEMA)
    op.drop_index("ix_threads_user_id", table_name="threads", schema=SCHEMA)
    op.drop_table("threads", schema=SCHEMA)
