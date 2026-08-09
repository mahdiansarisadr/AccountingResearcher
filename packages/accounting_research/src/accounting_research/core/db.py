"""Postgres connection helpers (admin + read-only)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector

from .settings import get_settings


@contextmanager
def admin_conn() -> Iterator[psycopg.Connection]:
    """Read/write connection used for seeding and building the catalog."""
    settings = get_settings()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        register_vector(conn)
        yield conn


@contextmanager
def readonly_conn() -> Iterator[psycopg.Connection]:
    """Read-only connection used by the agent's run_sql_query tool.

    The role itself has only SELECT privileges; on top of that we force a
    read-only transaction as defense-in-depth.
    """
    settings = get_settings()
    conn = psycopg.connect(settings.readonly_database_url)
    try:
        conn.read_only = True
        yield conn
    finally:
        conn.close()
