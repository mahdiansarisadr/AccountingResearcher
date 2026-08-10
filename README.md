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

## Running the agent over HTTP

Start a run, then stream it. Starting returns immediately with a `run_id`
because a run takes seconds, and holding an HTTP request open that long is
fragile — a refresh or a proxy timeout would lose the work.

```bash
# 1. Queue a run (202 Accepted)
curl -X POST localhost:8000/runs -H 'content-type: application/json' \
     -d '{"message":"Which audit cases have not been audited yet?"}'
# -> {"run_id":"5e7b5c1a-...","status":"queued"}

# 2. Watch it happen
curl -N localhost:8000/runs/5e7b5c1a-.../stream

# 3. Ask about it, or stop it
curl localhost:8000/runs/5e7b5c1a-...
curl -X POST localhost:8000/runs/5e7b5c1a-.../cancel
```

The stream is Server-Sent Events. Every record carries the event type, a JSON
payload, and an id:

```
event: tool_call
data: {"seq":1,"type":"tool_call","name":"search_schema","args":{"query":"audit cases not audited"}}

event: token
data: {"seq":5,"type":"token","text":"The"}

event: done
data: {"seq":442,"type":"done","run_id":"...","status":"succeeded"}
```

Event types are defined once in `accounting_research.agent.events` and shared by
every layer: `run_started`, `tool_call`, `tool_result`, `token`, `answer`,
`error`, `done`. A run always ends with exactly one `done`, so a client can
close on it without special-casing failures.

### How a run travels

```
POST /runs ──▶ API enqueues on Redis (RQ) ──▶ worker picks it up
                                                    │
                                          run_agent() yields events
                                                    │
                              worker appends each to a Redis Stream
                                                    │
GET /runs/{id}/stream ◀── API reads that stream ◀────┘
```

Events go through a Redis **Stream**, not pub/sub. Pub/sub only reaches whoever
is connected at that instant, and a client necessarily connects *after* starting
a run, so early events would be lost every time. A stream is an append-only log:
a late client replays from the beginning, and a dropped connection resumes from
where it left off via the `Last-Event-ID` header.

Cancellation is cooperative. `POST /cancel` sets a Redis flag; the running job
notices between events and stops at a clean boundary, reporting `cancelled`
rather than being killed mid-query.

One known limit: each open stream occupies a worker thread, because the Redis
read is blocking. Fine for a single team; moving to `redis.asyncio` is the fix
when it isn't.

## Database migrations

Two kinds of tables live in this database, and they are managed differently.

The **demo accounting tables** (`expenses`, `invoices`, …) are disposable
fixtures. They come from `test_database_resources/*.sql` and are rebuilt from
scratch by `make seed`. Dropping and recreating them is the normal workflow.

The **application tables** (chat runs, messages, and whatever follows) hold real
state, so they get versioned migrations via Alembic. Each change is a numbered
file, Postgres records which files it has applied in `alembic_version`, and
`make migrate` applies whatever is missing — so an existing database can move
forward without being wiped.

```bash
make migration name="add runs table"   # create a revision, then edit it
make migrate                           # apply everything pending
make migrate-down                      # revert the last one
make migrate-status                    # current revision + full history
make stack-migrate                     # apply from inside the api container
```

Migrations are hand-written: there are no ORM models, so `--autogenerate` has
nothing to compare against. `DATABASE_URL` is read by `migrations/env.py` from
`api.settings`, so migrations always target the same database the service uses
and no credentials sit in `alembic.ini`.

Applying migrations is always an explicit step, never something the API does on
startup — with more than one instance running, concurrent boots would race each
other on the same schema change.

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
packages/run_bus/src/run_bus/    Redis transport shared by api + worker
  keys.py                        Queue name, stream/flag key naming, TTLs
  bus.py                         Publish, read (blocking), cancel, status
services/api/                    HTTP API (FastAPI)
  Dockerfile                     Built from the repo root; installs --package api
  alembic.ini                    Migration config (URL comes from the environment)
  migrations/env.py              Wires Alembic to DATABASE_URL via api.settings
  migrations/versions/           Migration files (empty until threads land)
  src/api/main.py                App factory
  src/api/settings.py            API settings from .env
  src/api/deps.py                Shared Redis connection + RQ queue
  src/api/checks.py              Postgres + Redis readiness probes
  src/api/sse.py                 Server-Sent Events framing
  src/api/routers/health.py      /health (liveness), /ready (readiness)
  src/api/routers/runs.py        Start, stream, cancel, status
services/worker/                 Background worker
  Dockerfile                     Built from the repo root; installs --package worker
  src/worker/main.py             RQ consumer: startup retry, warm shutdown
  src/worker/tasks.py            The job: run the agent, publish its events
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
    events.py                    Run event contract shared by every layer
    runner.py                    run_agent(): the single execution path
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
