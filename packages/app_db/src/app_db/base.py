"""Declarative base for the application schema.

Application state lives in its own ``app`` schema, separate from the ``public``
schema holding the demo accounting data. That separation is a security boundary,
not tidiness:

- ``ar-seed`` finishes with ``GRANT SELECT ON ALL TABLES IN SCHEMA public TO
  ar_readonly``. Application tables in ``public`` would be handed to the agent's
  read-only role on every seed — which, once users exist, means the agent's SQL
  tool could read email addresses.
- ``ar_readonly`` is never granted ``USAGE`` on ``app``, so Postgres refuses
  those queries outright rather than relying on the agent behaving.

The table-selection catalog only describes ``public``, so application tables are
also never offered to the model as query targets.
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
