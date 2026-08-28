"""Create the application schema and the runs table.

The first application table. Until now a run existed only in Redis, which
expires an hour after the run ends; this makes the outcome of a run durable and
answerable after a restart.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

SCHEMA = "app"

# Spelled out rather than derived from app_db.RunStatus: a migration is a record
# of what the schema looked like at this point, and must not shift when the enum
# gains a member later.
STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


def upgrade() -> None:
    # Idempotent, and also created by migrations/env.py so Alembic's own version
    # table has somewhere to live. Repeated here so the offline SQL is complete.
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # Named for the enum, not the constraint: Alembic applies the metadata's
        # naming convention, which renders this as ck_runs_run_status.
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{status}'" for status in STATUSES) + ")",
            name="run_status",
        ),
        schema=SCHEMA,
    )
    # Supports "what is still in flight", the one query this table is scanned for.
    op.create_index("ix_runs_status", "runs", ["status"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_runs_status", table_name="runs", schema=SCHEMA)
    op.drop_table("runs", schema=SCHEMA)
    # The schema itself stays: Alembic's version table lives in it.
