"""Declarative base for the application schema.

Application state lives in its own ``app`` schema, separate from ``public``.
Keeping users, threads, messages and runs out of ``public`` means they are never
the default search path for ad-hoc connections, and Alembic can confine
autogenerate to this schema alone.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

SCHEMA = "app"

# Postgres invents names for unnamed constraints and indexes, and those names
# differ between databases — so a migration that drops one by name works on the
# machine it was written on and fails elsewhere. Naming them from the model makes
# them predictable and lets Alembic autogenerate a downgrade that works.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)
