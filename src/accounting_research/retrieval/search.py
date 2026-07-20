"""Table selection via hybrid retrieval over the schema catalog.

Combines semantic similarity (pgvector cosine) and keyword match (Postgres
full-text search) using Reciprocal Rank Fusion (RRF). This is the top accuracy
lever: pick the right few tables so the model can write correct SQL.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from ..core.db import readonly_conn
from ..core.embeddings import embed_query
from ..core.settings import get_settings

# Size of each candidate list before fusion, and the RRF damping constant.
_POOL = 10
_RRF_K = 60


@dataclass
class TableMatch:
    table_name: str
    table_description: str
    columns: list[dict]
    score: float


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in vec) + "]"


def search_tables(query: str, top_k: int | None = None) -> list[TableMatch]:
    settings = get_settings()
    top_k = top_k or settings.schema_top_k

    emb = _vector_literal(embed_query(query))

    with readonly_conn() as conn, conn.cursor() as cur:
        # Semantic ranking (cosine distance via pgvector).
        cur.execute(
            """
            SELECT table_name
            FROM schema_catalog
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (emb, _POOL),
        )
        semantic = [r[0] for r in cur.fetchall()]

        # Keyword ranking (full-text search).
        cur.execute(
            """
            SELECT table_name
            FROM schema_catalog
            WHERE fts @@ plainto_tsquery('english', %s)
            ORDER BY ts_rank(fts, plainto_tsquery('english', %s)) DESC
            LIMIT %s
            """,
            (query, query, _POOL),
        )
        keyword = [r[0] for r in cur.fetchall()]

        # Reciprocal Rank Fusion.
        scores: dict[str, float] = {}
        for ranking in (semantic, keyword):
            for rank, table_name in enumerate(ranking):
                scores[table_name] = scores.get(table_name, 0.0) + 1.0 / (_RRF_K + rank)

        winners = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        if not winners:
            return []

        names = [w[0] for w in winners]
        cur.execute(
            "SELECT table_name, table_description, columns FROM schema_catalog "
            "WHERE table_name = ANY(%s)",
            (names,),
        )
        detail = {
            row[0]: (row[1], row[2] if isinstance(row[2], list) else json.loads(row[2]))
            for row in cur.fetchall()
        }

    results: list[TableMatch] = []
    for name, score in winners:
        desc, columns = detail[name]
        results.append(TableMatch(name, desc, columns, round(score, 5)))
    return results
