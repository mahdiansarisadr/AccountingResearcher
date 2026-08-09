"""Tool: search_schema — table selection over the schema catalog."""

from __future__ import annotations

from langchain.tools import tool

from ...retrieval.search import search_tables


@tool
def search_schema(query: str) -> str:
    """Find the few database tables most relevant to a question.

    Given a natural-language description of what data you need, returns the
    top candidate tables with their column names, types, and descriptions.
    ALWAYS call this before writing SQL: you do not know the schema up front,
    and there are many tables. Use the returned table and column names exactly.
    """
    matches = search_tables(query)
    if not matches:
        return "No candidate tables found. Ask the user to clarify the request."

    blocks = []
    for m in matches:
        cols = "\n".join(
            f"    - {c['name']} ({c['type']}): {c['description']}" for c in m.columns
        )
        blocks.append(
            f"Table: {m.table_name} (relevance={m.score})\n"
            f"  {m.table_description}\n"
            f"  Columns:\n{cols}"
        )
    return "\n\n".join(blocks)
