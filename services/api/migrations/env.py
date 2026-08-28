"""Alembic environment.

The database URL comes from the API's own settings rather than alembic.ini, so
migrations and the running service can never drift onto different databases and
no credentials are committed.
"""

from __future__ import annotations

from logging.config import fileConfig

import app_db
from alembic import context
from sqlalchemy import create_engine, pool, text

# Imported for its side effect of registering the tables on Base.metadata.
from app_db import models  # noqa: F401

from api.settings import get_api_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = app_db.Base.metadata


def _database_url() -> str:
    return app_db.normalize_url(get_api_settings().database_url)


def _include_name(name: str | None, type_: str, _parent_names: dict) -> bool:
    """Confine autogenerate to the ``app`` schema so unrelated tables in
    ``public`` are never proposed for drop.
    """
    if type_ == "schema":
        return name == app_db.SCHEMA
    return True


# Shared by both modes: which schema owns the table Alembic stamps, and the
# filter that keeps autogenerate away from anything outside ``app``.
_CONTEXT_OPTIONS = {
    "target_metadata": target_metadata,
    "version_table_schema": app_db.SCHEMA,
    "include_schemas": True,
    "include_name": _include_name,
    # Detect column type changes, which Alembic ignores by default.
    "compare_type": True,
}


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it.

    Useful when a DBA has to review or apply the change by hand, which is the
    normal path in firms that do not grant DDL rights to application accounts.

    The emitted script assumes the ``app`` schema already exists: offline mode
    cannot query for it, and Alembic writes its version table before the first
    migration runs.
    """
    context.configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_CONTEXT_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live connection."""
    # NullPool: a migration run is short-lived, so pooling buys nothing and a
    # lingering connection can block later DDL.
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # Alembic creates its version table inside the application schema, and it
        # does so before the first migration has had a chance to create that
        # schema. Ensuring it here breaks the circle.
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{app_db.SCHEMA}"'))
        connection.commit()

        context.configure(connection=connection, **_CONTEXT_OPTIONS)

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
