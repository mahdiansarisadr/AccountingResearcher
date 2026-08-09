"""Tool: run_sql_query — read-only SQL execution with guardrails."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal

from langchain.tools import tool

from ...core.db import readonly_conn
from ...core.settings import get_settings

# Only single read-only statements are allowed.
_ALLOWED_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|merge|"
    r"call|do|vacuum|reindex|refresh|lock|listen|notify|comment|into)\b",
    re.IGNORECASE,
)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


@tool
def run_sql_query(sql: str) -> str:
    """Execute a single read-only SQL SELECT against the accounting database.

    Returns rows as JSON: {"columns": [...], "rows": [[...]], "row_count": n,
    "truncated": bool}. Only SELECT/WITH statements are allowed. To make answers
    traceable, SELECT the provenance columns (source_file, locator) alongside the
    values you report so you can cite them. All arithmetic/aggregation must be
    done here in SQL, never estimated.
    """
    settings = get_settings()
    cleaned = sql.strip().rstrip(";").strip()

    if not _ALLOWED_START.match(cleaned):
        return "ERROR: only SELECT/WITH read-only queries are allowed."
    if ";" in cleaned:
        return "ERROR: multiple statements are not allowed; submit a single SELECT."
    if _FORBIDDEN.search(cleaned):
        return "ERROR: query contains a forbidden (non-read-only) keyword."

    try:
        with readonly_conn() as conn, conn.cursor() as cur:
            cur.execute(cleaned)
            if cur.description is None:
                return "ERROR: query returned no result set."
            columns = [d.name for d in cur.description]
            rows = cur.fetchmany(settings.max_result_rows + 1)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model to self-correct
        return f"ERROR executing query: {exc}"

    truncated = len(rows) > settings.max_result_rows
    rows = rows[: settings.max_result_rows]
    payload = {
        "columns": columns,
        "rows": [list(r) for r in rows],
        "row_count": len(rows),
        "truncated": truncated,
    }
    return json.dumps(payload, default=_json_default)
