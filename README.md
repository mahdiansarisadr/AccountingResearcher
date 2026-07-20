# Accounting Research Assistant — Phase 1

A text-to-SQL AI agent that answers accounting questions over a seeded Postgres
store. It selects the relevant tables via a pgvector-backed **schema catalog**,
runs **read-only** SQL, and returns **cited, confidence-scored** answers —
abstaining rather than guessing. See `docs/PRD.md` (what/why) and `docs/TDD.md`
(how).

This phase runs entirely locally: Postgres + pgvector in Docker, synthetic
accounting data, a CLI chat interface, and LangSmith tracing.

## Prerequisites

- Docker (for Postgres + pgvector)
- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- An OpenAI API key and (optional) LangSmith key

## Setup

```bash
# 1. Configure secrets (already copied for local dev)
cp .env.example .env   # then fill in OPENAI_API_KEY / LANGSMITH_API_KEY

# 2. Install dependencies
uv sync

# 3. Start Postgres + pgvector
docker compose up -d

# 4. Seed synthetic accounting data (creates ~10 tables with provenance)
uv run ar-seed

# 5. Build the schema catalog (embeddings + full-text index for table selection)
uv run ar-catalog

# 6. Chat
uv run ar-chat
```

## Usage

```bash
uv run ar-chat                 # interactive chat REPL
uv run ar-chat --ask "..."     # one-shot question
uv run ar-chat --smoke         # run the 3 core question types end-to-end
```

## The three core question types (Phase 1 exit criteria)

1. **Multi-year aggregation** — e.g. "What is the total travel cost for the
   Finance team over the last 3 years?"
2. **Trend** — e.g. "What is the monthly spending trend since the start of 2026?"
3. **Status / exception** — e.g. "Which audit cases have not been audited yet?"

## Layout

```
docker-compose.yml               Postgres + pgvector
test_database_resources/         Demo/test database bootstrap (seed-time only)
  init.sql                       Extension + read-only agent role
  schema.sql                     Seed table DDL
  catalog.sql                    schema_catalog DDL
  tables.yaml                    Authored table/column descriptions (feeds the catalog)
src/accounting_research/
  core/
    settings.py                  Settings from .env
    db.py                        Admin + read-only connections
    embeddings.py                Hosted OpenAI embeddings
  retrieval/
    metadata.py                  Loader for tables.yaml
    catalog.py                   Build schema catalog (ar-catalog)
    search.py                    Hybrid table selection (pgvector + FTS + RRF)
  agent/
    schemas.py                   Structured output (answer/confidence/citations)
    builder.py                   create_agent assembly + middleware
    prompts/system.md            System prompt content (behavioral contract)
    prompts/__init__.py          Prompt loader
    tools/schema_search.py       search_schema tool
    tools/sql_query.py           run_sql_query tool (read-only)
  ingestion/
    seed.py                      Synthetic data loader (ar-seed)
  interfaces/
    cli.py                       Chat REPL (ar-chat)
  eval/                          Evaluation harness (Phase 3, stub)
```
