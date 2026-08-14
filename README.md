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

# 6. Create the application tables (users, run records)
make migrate

# 7. Chat
make chat
```

The CLI needs none of the sign-in configuration below; it talks to the agent
directly. Fill that in when you want to use the HTTP API.

## Usage

```bash
make chat        # interactive chat REPL
make api         # HTTP API on :8000 with reload
make worker      # background worker
make health      # curl /health and /ready
make migrate     # apply pending migrations (run once before the API)
make test        # run the test suite
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

## Signing in

Every route that touches data requires a session. The exceptions are the sign-in
routes, `/health` and `/ready` (a probe cannot authenticate), and FastAPI's
`/docs`, `/redoc` and `/openapi.json`, which describe the API's shape but expose
none of its contents — worth closing in production as part of Phase 5 hardening.

Access is granted by one rule: a verified Google account on
the company domain. There is no invitation step and no password — anyone on the
domain who signs in gets an account as a **member** on first arrival, and every
other account is refused.

```
GET  /auth/login     ──▶ redirect to Google
GET  /auth/callback  ◀── Google returns; account created or found, cookie issued
GET  /me                 who am I, and what may I do
POST /auth/logout        drop the cookie
GET  /admin/users        every user                  (admin only)
PATCH /admin/users/{id}  set role or revoke access   (admin only)
```

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAIL_DOMAIN`,
`INITIAL_ADMIN_EMAIL` and `SESSION_SECRET` (see `.env.example`), and register
`OAUTH_REDIRECT_URL` on the OAuth client in the Google Cloud console — it must
match verbatim, port included.

The **first admin** is seeded from `INITIAL_ADMIN_EMAIL`, because an admin cannot
be appointed by an admin when there are none. That address is also restored to an
active admin on every sign-in, and an admin cannot demote or deactivate
themselves, so an instance cannot end up locked out of its own user management.
Another admin still can, so neither rule makes anyone permanent.

The session is a signed JWT in an **HttpOnly** cookie, valid for a week. It
carries an identity and nothing else: role and active status are read from the
database on every request, so revoking access or changing a role takes effect on
the next request rather than whenever a week-old cookie expires. Signed, not
encrypted — none of it is secret, and the signature is what stops it being
edited.

### Before you have Google credentials

There is a development sign-in that issues the same cookie without contacting
Google, so the API and the frontend can be worked on first. It is off unless
`DEV_LOGIN_ENABLED=true`, it still enforces the domain rule, and setting it in
production stops the API from starting at all — a route that mints sessions for
arbitrary addresses must not be one env var away from being reachable.

```bash
curl -c cookies.txt -X POST localhost:8000/auth/dev-login \
     -H 'content-type: application/json' -d '{"email":"you@your-company.com"}'
```

## Running the agent over HTTP

Open a conversation, start a run on it, then stream it. Starting returns
immediately with a `run_id` because a run takes seconds, and holding an HTTP
request open that long is fragile — a refresh or a proxy timeout would lose the
work.

Every call carries the session cookie; `-b cookies.txt` below is the jar written
by signing in above.

```bash
# 1. Open a conversation
curl -b cookies.txt -X POST localhost:8000/threads -H 'content-type: application/json' \
     -d '{"title":"Audit cases"}'
# -> {"id":"3a1c...","title":"Audit cases","created_at":"...","updated_at":"..."}

# 2. Queue a run on it (202 Accepted)
curl -b cookies.txt -X POST localhost:8000/threads/3a1c.../runs \
     -H 'content-type: application/json' \
     -d '{"message":"Which audit cases have not been audited yet?"}'
# -> {"run_id":"5e7b5c1a-...","thread_id":"3a1c...","status":"queued",...}

# 3. Watch it happen
curl -b cookies.txt -N localhost:8000/runs/5e7b5c1a-.../stream

# 4. Ask about it, stop it, or reread the conversation
curl -b cookies.txt localhost:8000/runs/5e7b5c1a-...
curl -b cookies.txt -X POST localhost:8000/runs/5e7b5c1a-.../cancel
curl -b cookies.txt localhost:8000/threads/3a1c.../messages
```

A thread, its messages and its runs belong to whoever created them. Someone
else's id is worth nothing: ownership is part of the query rather than a check
on the result, so another user's thread is reported as `404` — confirming that
an id exists but belongs to someone else would itself be a disclosure.

History is loaded from the thread, not accepted from the client. That is what
makes a reload show the same conversation, and what stops a caller forging prior
turns. A second question on a thread that still has a run in progress is a
`409`.

Asking about a run reads the `app.runs` table, so the answer survives a restart
and outlives the hour its event log spends in Redis. The timestamps are there to
be subtracted: `started_at - created_at` is how long the run waited for a
worker, `finished_at - started_at` is how long it took. Cancelling a run that
has already finished is a `409`, not a silent no-op.

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
POST /threads/{id}/runs ──▶ API appends the question, writes a queued row (and commits)
                                      │
                                      └──▶ enqueues on Redis (RQ) ──▶ worker picks it up
                                                                          │
                                                        marks the row running, then
                                                        run_agent() yields events
                                                                          │
                                            worker appends each to a Redis Stream,
                                            records the outcome, and on success
                                            writes the assistant turn onto the thread
                                                                          │
GET /runs/{id}/stream          ◀── API reads that stream ◀────────────────┘
GET /runs/{id}                 ◀── API reads app.runs
GET /threads/{id}/messages     ◀── API reads app.messages
```

Two stores, two jobs. **Redis** carries the run while it is happening;
**Postgres** records the conversation and what became of each turn. The row is
committed *before* the job is enqueued, because a worker can claim it within
milliseconds and its first act is to look the run up by id.

Events go through a Redis **Stream**, not pub/sub. Pub/sub only reaches whoever
is connected at that instant, and a client necessarily connects *after* starting
a run, so early events would be lost every time. A stream is an append-only log:
a late client replays from the beginning, and a dropped connection resumes from
where it left off via the `Last-Event-ID` header. Once the log has expired,
streaming a finished run returns a single `done` event rebuilt from its row —
better than holding the connection open until the idle timeout to conclude the
same thing.

Cancellation is cooperative. `POST /cancel` sets a Redis flag; the running job
notices between events and stops at a clean boundary, reporting `cancelled`
rather than being killed mid-query. A run cancelled while it was still queued is
never executed at all, but still gets a terminating event, because a client may
already be streaming it.

The lifecycle transitions are guarded rather than blind writes: a run only moves
from `queued` to `running`, and only settles once. That is what makes a
redelivered job harmless and stops a crash handler on the way out from
overwriting a deliberate `cancelled` with `failed`.

One known limit: each open stream occupies a worker thread, because the Redis
read is blocking. Fine for a single team; moving to `redis.asyncio` is the fix
when it isn't.

## Database migrations

Two kinds of tables live in this database, and they are managed differently.

The **demo accounting tables** (`expenses`, `invoices`, …) are disposable
fixtures. They come from `test_database_resources/*.sql` and are rebuilt from
scratch by `make seed`. Dropping and recreating them is the normal workflow.

The **application tables** (users, threads, messages and run records) hold real
state, so they get versioned migrations via Alembic. Each
change is a numbered file, Postgres records which files it has applied in
`alembic_version`, and `make migrate` applies whatever is missing — so an
existing database can move forward without being wiped.

```bash
make migration name="add users table"  # autogenerate a revision, then review it
make migrate                           # apply everything pending
make migrate-down                      # revert the last one
make migrate-status                    # current revision + full history
make migrate-check                     # fail if models and database disagree
make stack-migrate                     # apply from inside the api container
```

They live in **separate schemas**, and that is a security boundary rather than
tidiness. The demo tables are in `public`; the application tables are in `app`.
`ar-seed` finishes with `GRANT SELECT ON ALL TABLES IN SCHEMA public TO
ar_readonly`, so an application table in `public` would be handed to the agent's
read-only role on every seed — and now that `app.users` exists, that would mean
the agent's SQL tool could read every email address in the company, at the
suggestion of anyone who could get a sentence into a prompt. `ar_readonly` is
never granted `USAGE` on `app`, so Postgres refuses those queries outright:

```
accounting=> select count(*) from app.users;
ERROR:  permission denied for schema app
```

Revisions are generated from the SQLAlchemy models in `app_db` and then reviewed
— `--autogenerate` sees shape, not intent. Because both schemas share one
database, `migrations/env.py` confines autogenerate to `app`; without that filter
it would compare the demo tables against empty metadata and propose dropping
every one of them. `make migrate-check` is the inverse guard, failing when a
model has been edited and no migration written for it.

`DATABASE_URL` is read by `migrations/env.py` from `api.settings`, so migrations
always target the same database the service uses and no credentials sit in
`alembic.ini`.

Applying migrations is always an explicit step, never something the API does on
startup — with more than one instance running, concurrent boots would race each
other on the same schema change.

## Tests

```bash
make test
```

Redis is replaced with an in-process fake, because nothing about the run bus
depends on it being a real server. Postgres is used for real: the schema carries
CHECK constraints, a unique email, a foreign key and server-side timestamps, and
a substitute that did not enforce them would let exactly the bugs these tests
exist to catch through. Each such test runs inside a transaction that is rolled
back, so the suite leaves no rows behind — and skips itself, rather than failing,
when Postgres is not up.

The agent is scripted, never called. `tests/doubles.py` replays the
`(mode, chunk)` sequence LangGraph produces, so the event contract is tested
without a model, an API key or a network, and the suite runs in about a second.

Google is never contacted either. The handshake is a single dependency, replaced
with the claims Google would have returned, and everything downstream of it runs
for real: requests carry a genuinely signed cookie and the guard verifies it and
loads the user. That keeps the parts worth being sure about under test — the
lookalike domains that must be refused, a token edited to name someone else, a
deactivated account locked out on its very next request, and one user's run id
being of no use to another.

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
  bus.py                         Publish, read (blocking), cancel
packages/app_db/src/app_db/      Postgres persistence shared by api + worker
  base.py                        Declarative base; the "app" schema boundary
  models.py                      Tables: users, threads, messages, runs
  engine.py                      Engine, session factory, transaction scope
  runs.py                        Guarded lifecycle transitions; owner-scoped reads
  users.py                       Accounts, roles, and recorded sign-ins
  threads.py                     Conversations, messages, and history for the agent
services/api/                    HTTP API (FastAPI)
  Dockerfile                     Built from the repo root; installs --package api
  alembic.ini                    Migration config (URL comes from the environment)
  migrations/env.py              Wires Alembic to the models and DATABASE_URL
  migrations/versions/           Migration files, applied in order
  src/api/main.py                App factory
  src/api/settings.py            API settings from .env
  src/api/deps.py                Injectable Redis, queue, DB session; the auth guard
  src/api/security.py            Issue, verify and clear the session cookie
  src/api/oauth.py               The Google OAuth client
  src/api/schemas.py             Response shapes shared by routers
  src/api/checks.py              Postgres + Redis readiness probes
  src/api/sse.py                 Server-Sent Events framing
  src/api/routers/health.py      /health (liveness), /ready (readiness)
  src/api/routers/auth.py        Sign in, sign out, /me; the domain rule
  src/api/routers/admin.py       List users, set role, revoke access
  src/api/routers/threads.py     Conversations, message history, start a run
  src/api/routers/runs.py        Stream, cancel, status
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
tests/                           Test suite (make test)
  conftest.py                    Fake Redis, rolled-back transactions, signed-in clients
  doubles.py                     Scripted stands-ins for the agent
  test_runner.py                 The run event contract
  test_run_bus.py                Publish, replay, resume, cancel
  test_run_records.py            Guarded lifecycle transitions; run ownership
  test_thread_records.py         Conversations, message order, cascade delete
  test_user_records.py           Accounts, roles, and returning users
  test_auth.py                   The domain rule, the cookie, and the guard
  test_admin.py                  Who may manage users, and what they may not do
  test_threads_api.py            List, create, read, delete; isolation
  test_runs_api.py               Accept, report, stream, stop over HTTP
  test_worker_tasks.py           Events and records agreeing on every path
  test_sse.py                    Server-Sent Events framing
```
