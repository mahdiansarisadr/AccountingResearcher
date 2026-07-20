-- Schema catalog table used for table selection (search_schema).
-- {dim} is substituted with AR_EMBEDDING_DIM by the catalog builder before
-- execution, so the vector width matches the configured embedding model.

DROP TABLE IF EXISTS schema_catalog;

CREATE TABLE schema_catalog (
    id                serial PRIMARY KEY,
    table_name        text NOT NULL UNIQUE,
    table_description text NOT NULL,
    columns           jsonb NOT NULL,
    sample_values     jsonb NOT NULL,
    doc               text NOT NULL,
    embedding         vector({dim}) NOT NULL,
    fts               tsvector
);

CREATE INDEX schema_catalog_embedding_idx
    ON schema_catalog USING hnsw (embedding vector_cosine_ops);

CREATE INDEX schema_catalog_fts_idx
    ON schema_catalog USING gin (fts);
