"""Loader for the authored table/column descriptions (tables.yaml).

This is the thin *code* layer over the *content* in
`test_database_resources/tables.yaml`. The catalog builder uses it to know what
to embed and index; the descriptions themselves live in the YAML, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from ..core.settings import get_settings


@dataclass(frozen=True)
class TableMeta:
    name: str
    description: str
    columns: dict[str, str] = field(default_factory=dict)  # column -> description


@lru_cache
def _load_raw() -> dict:
    path = get_settings().tables_yaml
    if not path.exists():
        raise FileNotFoundError(
            f"Table metadata file not found: {path}. "
            "Expected it under test_database_resources/tables.yaml."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_provenance_columns() -> dict[str, str]:
    """Column -> description for the provenance columns shared by all tables."""
    return dict(_load_raw().get("provenance_columns", {}))


def load_tables() -> list[TableMeta]:
    """All authored tables with their descriptions and column descriptions."""
    tables = _load_raw().get("tables", {})
    return [
        TableMeta(
            name=name,
            description=spec.get("description", ""),
            columns=dict(spec.get("columns", {})),
        )
        for name, spec in tables.items()
    ]
