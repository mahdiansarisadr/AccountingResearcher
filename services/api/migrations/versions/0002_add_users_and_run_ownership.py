"""Add users, and give every run an owner.

Sign-in arrives with this revision, so the table of people comes with it. The
same change makes ``runs.user_id`` mandatory: a run nobody owns could be read by
anyone who knew its id, and a nullable owner is a filter every future query has
to remember.

Existing runs are deleted rather than backfilled. There is no account to
attribute them to — no user existed before this revision, so no run in the table
can belong to anyone. Making the column nullable to preserve a handful of
pre-authentication rows would trade a permanent ambiguity for throwaway history.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SCHEMA = "app"

# Spelled out rather than derived from app_db.UserRole: a migration records what
# the schema looked like here, and must not shift when the enum gains a member.
ROLES = ("admin", "member")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        # 320 is the longest address SMTP allows: 64 local + @ + 255 domain.
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # Unique because the address is how a returning user is recognised.
        sa.UniqueConstraint("email"),
        sa.CheckConstraint(
            "role IN (" + ", ".join(f"'{role}'" for role in ROLES) + ")",
            name="user_role",
        ),
        schema=SCHEMA,
    )

    # See the note above: these rows predate the existence of accounts.
    op.execute(f'DELETE FROM "{SCHEMA}".runs')

    op.add_column("runs", sa.Column("user_id", sa.Uuid(), nullable=False), schema=SCHEMA)
    # Supports "this user's runs", which is every read of this table from now on.
    op.create_index("ix_runs_user_id", "runs", ["user_id"], schema=SCHEMA)
    # RESTRICT, not CASCADE: deleting an account should not quietly erase the
    # record of what it did. Accounts are deactivated instead.
    op.create_foreign_key(
        "fk_runs_user_id_users",
        "runs",
        "users",
        ["user_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_runs_user_id_users", "runs", schema=SCHEMA, type_="foreignkey")
    op.drop_index("ix_runs_user_id", table_name="runs", schema=SCHEMA)
    op.drop_column("runs", "user_id", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
