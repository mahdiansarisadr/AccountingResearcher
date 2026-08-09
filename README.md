# Accounting Research Assistant — Phase 1

A text-to-SQL AI agent that answers accounting questions over a seeded Postgres
store. It selects the relevant tables via a pgvector-backed **schema catalog**,
runs **read-only** SQL, and returns **cited, confidence-scored** answers —
abstaining rather than guessing. See `docs/PRD.md` (what/why) and `docs/TDD.md`
(how).

This phase runs entirely locally: Postgres + pgvector in Docker, synthetic
accounting data, a CLI chat interface, and LangSmith tracing.

## Prerequisites

- Docker (for Postgres + pgvector and Redis)
- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- An OpenAI API key and (optional) LangSmith key

## Setup

All commands go through `make`, which pins the virtualenv location (see
[Environment notes](#environment-notes)). Run `make help` for the full list.

```bash
# 1. Configure secrets (already copied for local dev)
cp .env.example .env   # then fill in OPENAI_API_KEY / LANGSMITH_API_KEY

# 2. Install every workspace member into the shared virtualenv
make sync

# 3. Start Postgres + pgvector and Redis
make up

# 4. Seed synthetic accounting data (creates ~10 tables with provenance)
make seed

# 5. Build the schema catalog (embeddings + full-text index for table selection)
make catalog

# 6. Chat
make chat
```

## Usage

```bash
make chat        # interactive chat REPL
make api         # HTTP API on :8000 with reload
make worker      # background worker
make health      # curl /health and /ready
```

For flags the Makefile does not wrap, call the console scripts directly:

```bash
uv run --no-sync ar-chat --ask "..."     # one-shot question
uv run --no-sync ar-chat --smoke         # run the 3 core question types end-to-end
```

## Environment notes

The virtualenv lives at `~/.venvs/accounting-research`, **outside** this
checkout, and `make` points `uv` at it via `UV_PROJECT_ENVIRONMENT`.

This is not cosmetic. This repo sits under `~/Desktop`, which macOS syncs to
iCloud. The sync daemon sets the `UF_HIDDEN` file flag on `.pth` files, and
CPython's `site.py` silently skips hidden `.pth` files — so editable installs
stop resolving and imports fail with `ModuleNotFoundError`. The daemon also
leaves `foo 2.py` conflict copies inside `site-packages`. Keeping the
environment out of the synced tree avoids both. `make doctor` asserts every
member is importable, and `make sync` runs it automatically.

Re-run `make sync` after changing dependencies or adding a workspace member.
Long-running processes use `uv run --no-sync` so an implicit re-sync cannot swap
packages out from under a live server.

## The three core question types (Phase 1 exit criteria)

1. **Multi-year aggregation** — e.g. "What is the total travel cost for the
   Finance team over the last 3 years?"
2. **Trend** — e.g. "What is the monthly spending trend since the start of 2026?"
3. **Status / exception** — e.g. "Which audit cases have not been audited yet?"

## Layout

A `uv` workspace: one lockfile and one virtualenv shared by several members.
`packages/` holds importable libraries, `services/` holds deployable processes.

```
Makefile                         Developer entry points (make help)
pyproject.toml                   Workspace root (members + shared dev deps)
docker-compose.yml               Postgres + pgvector, Redis
test_database_resources/         Demo/test database bootstrap (seed-time only)
  init.sql                       Extension + read-only agent role
  schema.sql                     Seed table DDL
  catalog.sql                    schema_catalog DDL
  tables.yaml                    Authored table/column descriptions (feeds the catalog)
services/api/src/api/            HTTP API (FastAPI)
  main.py                        App factory
  settings.py                    API settings from .env
  checks.py                      Postgres + Redis readiness probes
  routers/health.py              /health (liveness), /ready (readiness)
services/worker/src/worker/      Background worker
  main.py                        Startup retry, idle loop, graceful shutdown
  settings.py                    Worker settings from .env
packages/accounting_research/src/accounting_research/
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
