"""Agent tools, exported as a single list for create_agent."""

from __future__ import annotations

from .schema_search import search_schema
from .sql_query import run_sql_query

TOOLS = [search_schema, run_sql_query]

__all__ = ["TOOLS", "search_schema", "run_sql_query"]
