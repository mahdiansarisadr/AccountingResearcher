"""Build the schema catalog used for table selection (search_schema).

For each table we store: its description, its columns (name/type/desc), a few
representative sample values, a combined text `doc`, an embedding (pgvector),
and a full-text `fts` vector. Together these power hybrid (semantic + keyword)
retrieval.

Descriptions come from test_database_resources/tables.yaml (via the metadata
loader); the DDL comes from test_database_resources/catalog.sql.

Run:  python -m accounting_research.retrieval.catalog   (or: ar-catalog)
"""

from __future__ import annotations

import json
from datetime import datetime

from ..core.db import admin_conn
from ..core.embeddings import embed_documents
from ..core.settings import get_settings
from .metadata import load_provenance_columns, load_tables


def _column_types(cur, table_name: str) -> dict[str, str]:
    cur.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    )
    return {name: dtype for name, dtype in cur.fetchall()}


def _sample_values(cur, table_name: str, columns: list[str]) -> dict[str, list]:
    samples: dict[str, list] = {}
    for col in columns:
        try:
            cur.execute(
                f'SELECT DISTINCT "{col}" FROM {table_name} '
                f'WHERE "{col}" IS NOT NULL LIMIT 5'
            )
            vals = [r[0] for r in cur.fetchall()]
            samples[col] = [str(v) for v in vals]
        except Exception:  # noqa: BLE001 - sampling is best-effort
            samples[col] = []
    return samples


def _build_doc(table_name: str, table_desc: str,
               columns: list[dict], samples: dict[str, list]) -> str:
    lines = [f"Table: {table_name}", f"Description: {table_desc}", "Columns:"]
    for c in columns:
        lines.append(f"  - {c['name']} ({c['type']}): {c['description']}")
    sample_bits = [f"{col}={', '.join(vals)}" for col, vals in samples.items() if vals]
    if sample_bits:
        lines.append("Sample values: " + "; ".join(sample_bits[:8]))
    return "\n".join(lines)


def build() -> None:
    settings = get_settings()
    provenance_cols = load_provenance_columns()
    catalog_ddl = settings.catalog_sql.read_text(encoding="utf-8")

    with admin_conn() as conn, conn.cursor() as cur:
        cur.execute(catalog_ddl.format(dim=settings.embedding_dim))

        docs: list[str] = []
        rows: list[dict] = []

        for table in load_tables():
            types = _column_types(cur, table.name)

            columns = []
            for col, desc in table.columns.items():
                columns.append({"name": col, "type": types.get(col, "?"), "description": desc})
            for col, desc in provenance_cols.items():
                columns.append({"name": col, "type": types.get(col, "?"), "description": desc})

            # Sample only the domain columns (skip provenance/noise).
            domain_cols = [c for c in table.columns if c in types]
            samples = _sample_values(cur, table.name, domain_cols)

            doc = _build_doc(table.name, table.description, columns, samples)
            docs.append(doc)
            rows.append({
                "table_name": table.name,
                "table_description": table.description,
                "columns": columns,
                "sample_values": samples,
                "doc": doc,
            })

        print(f"[{datetime.now():%H:%M:%S}] Embedding {len(docs)} table docs...")
        vectors = embed_documents(docs)
        if vectors and len(vectors[0]) != settings.embedding_dim:
            raise RuntimeError(
                f"AR_EMBEDDING_DIM={settings.embedding_dim} does not match "
                f"{settings.embedding_model} output ({len(vectors[0])}). "
                "Update AR_EMBEDDING_DIM and rerun `make catalog`."
            )

        for row, vec in zip(rows, vectors):
            cur.execute(
                """
                INSERT INTO schema_catalog
                    (table_name, table_description, columns, sample_values, doc, embedding, fts)
                VALUES (%s, %s, %s, %s, %s, %s, to_tsvector('english', %s))
                """,
                (
                    row["table_name"],
                    row["table_description"],
                    json.dumps(row["columns"]),
                    json.dumps(row["sample_values"]),
                    row["doc"],
                    vec,
                    row["doc"],
                ),
            )

        cur.execute("GRANT SELECT ON schema_catalog TO ar_readonly")
        cur.execute("SELECT count(*) FROM schema_catalog")
        n = cur.fetchone()[0]

    print(f"[{datetime.now():%H:%M:%S}] Schema catalog built: {n} tables indexed.")


def main() -> None:
    build()


if __name__ == "__main__":
    main()
