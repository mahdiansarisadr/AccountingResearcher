"""Engines and transactions.

Callers name the database by URL rather than reading configuration here: the API
and the worker each already validate their own settings, and a library that
reaches for the environment behind their back is untestable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def normalize_url(url: str) -> str:
    """Return ``url`` with the driver SQLAlchemy should use spelled out.

    A bare ``postgresql://`` URL makes SQLAlchemy reach for psycopg2, which this
    project does not install — it uses psycopg 3. Naming the driver here keeps a
    single ``DATABASE_URL`` in .env usable by psycopg, SQLAlchemy and Alembic
    alike.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@lru_cache
def get_engine(url: str) -> Engine:
    """One pooled engine per URL per process."""
    return create_engine(
        normalize_url(url),
        # A connection idle in the pool can be closed from the other side — by a
        # Postgres restart, or an idle timeout in a proxy. Without pre_ping that
        # surfaces as a failed request on an otherwise healthy service.
        pool_pre_ping=True,
        # Modest: the API's concurrency is bounded by its thread pool and the
        # worker executes one run at a time.
        pool_size=5,
        max_overflow=5,
    )


@lru_cache
def _session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(
        bind=get_engine(url),
        # Leaves loaded attributes readable after commit. Otherwise returning an
        # ORM object from a closed session raises on first attribute access.
        expire_on_commit=False,
    )


@contextmanager
def session_scope(url: str) -> Iterator[Session]:
    """One transaction: commit on a clean exit, roll back on an exception."""
    session = _session_factory(url)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
