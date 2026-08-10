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

### Running everything in containers

The default `make up` starts Postgres and Redis only, leaving you to run the API
and worker on the host. To run the whole stack in Docker instead:

```bash
make build       # build the api and worker images
make stack       # start postgres, redis, api, worker
make stack-logs  # follow api + worker output
make stack-down  # stop it all
```

`api` and `worker` live behind a Compose `apps` profile, which is why they are
excluded from plain `docker compose up`. Both mount their source read-only from
the host, so edits reload live without a rebuild; rebuild only when dependencies
change. Inside Compose the services reach each other by service name
(`postgres:5432`, `redis:6379`), which is why `docker-compose.yml` overrides the
localhost URLs that `.env` uses for host-based runs.

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
docker-compose.yml               Postgres + pgvector, Redis, api + worker (apps profile)
.dockerignore                    Build-context exclusions (shared by both images)
test_database_resources/         Demo/test database bootstrap (seed-time only)
  init.sql                       Extension + read-only agent role
  schema.sql                     Seed table DDL
  catalog.sql                    schema_catalog DDL
  tables.yaml                    Authored table/column descriptions (feeds the catalog)
services/api/                    HTTP API (FastAPI)
  Dockerfile                     Built from the repo root; installs --package api
  src/api/main.py                App factory
  src/api/settings.py            API settings from .env
  src/api/checks.py              Postgres + Redis readiness probes
  src/api/routers/health.py      /health (liveness), /ready (readiness)
services/worker/                 Background worker
  Dockerfile                     Built from the repo root; installs --package worker
  src/worker/main.py             Startup retry, idle loop, graceful shutdown
  src/worker/settings.py         Worker settings from .env
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
