"""Alembic environment.

The database URL comes from the API's own settings rather than alembic.ini, so
migrations and the running service can never drift onto different databases and
no credentials are committed.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from api.settings import get_api_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No ORM models yet, so autogenerate has nothing to diff against and migrations
# are hand-written. Point this at a declarative Base's metadata to enable
# `alembic revision --autogenerate`.
target_metadata = None


def _database_url() -> str:
    """Return the app database URL in the form SQLAlchemy needs.

    A bare ``postgresql://`` URL makes SQLAlchemy reach for psycopg2, which we
    do not install — this project uses psycopg 3. Naming the driver explicitly
    keeps one URL in .env usable by both psycopg and SQLAlchemy.
    """
    url = get_api_settings().database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    Useful when a DBA has to review or apply the change by hand, which is the
    normal path in firms that do not grant DDL rights to application accounts.
    """
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection."""
    # NullPool: a migration run is short-lived, so pooling buys nothing and a
    # lingering connection can block later DDL.
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Detect column type changes, which Alembic ignores by default.
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
