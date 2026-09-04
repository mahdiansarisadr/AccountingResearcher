# MLEng

An agent that lets people who know their domain — but not machine learning —
upload a table, chat, and train a model. Each account has its own on-disk
workspace (`data/users/{user_id}/`) for uploads and MLflow runs, so one
person's files never land in another's.

Runs locally: Postgres and Redis in Docker, a FastAPI service, a worker, and a
Next.js chat UI. A CLI is still there.

## Prerequisites

- Docker (for Postgres and Redis)
- Python 3.11+
- Node 22+ (for the chat UI)
- [`uv`](https://docs.astral.sh/uv/)
- An Anthropic API key (the agent) and (optional) LangSmith key.

## Setup

All commands go through `make`, which pins the virtualenv location (see
[Environment notes](#environment-notes)). Run `make help` for the full list.

```bash
# 1. Configure secrets
cp .env.example .env   # then fill in ANTHROPIC_API_KEY / LANGSMITH_API_KEY

# 2. Install every workspace member into the shared virtualenv
make sync

# 3. Start Postgres and Redis
make up

# 4. Create the application tables (users, threads, messages, run records)
make migrate

# 5. Chat in the terminal, or open the UI
make chat
make frontend   # http://localhost:3000 — needs make api + make worker too
```

The CLI needs none of the sign-in configuration below; it talks to the agent
directly. Fill that in when you want to use the HTTP API.

In the chat UI, **Attach** a CSV or Parquet file on a conversation, then ask
the agent to profile it or train. Each signed-in account writes only under
`data/users/{that user's id}/` (uploads per thread, MLflow in that user's
`mlflow.db` and `artifacts/`).

If you previously ran the accounting-research stack, stop those containers
(`docker stop ar_api ar_worker ar_postgres`) before `make up`. They publish
the same host ports (8000, 5433). On macOS, the browser’s `localhost` is
often IPv6 and will hit the old API even while `make api` is bound to
`127.0.0.1` — Attach then 413s because that service still has a 64 KB cap.
Recreate the Postgres volume (`docker compose down -v` then `make up`) so
the new `mleng` database user exists.

## Usage

```bash
make chat        # interactive chat REPL
make api         # HTTP API on :8000 with reload
make worker      # background worker
make frontend    # chat UI on :3000
make health      # curl /health and /ready
make migrate     # apply pending migrations (run once before the API)
make test        # run the test suite
```

For flags the Makefile does not wrap, call the console scripts directly:

```bash
uv run --no-sync mleng-chat --ask "..."     # one-shot question
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

### Production (Compose + Caddy)

`docker-compose.prod.yml` is the shippable stack: no bind-mounts, no `--reload`,
Postgres and Redis unpublished, Caddy on 80/443 terminating TLS and serving the
UI and API on the same host. Let's Encrypt issues a certificate when
`PUBLIC_HOST` is a real DNS name pointing at the VM.

```bash
# On the host, with .env filled in (SESSION_SECRET, Google, ANTHROPIC_API_KEY)
make prod-build PUBLIC_HOST=mleng.example.com
make prod-up PUBLIC_HOST=mleng.example.com
make prod-migrate PUBLIC_HOST=mleng.example.com
```

Register `https://mleng.example.com/auth/callback` on the Google OAuth
client. Change `POSTGRES_PASSWORD` in `.env` before the machine is public.
`SENTRY_DSN` is optional; leave it empty to skip error tracking.

Until a VM and domain exist, this file is the deploy artifact — same images,
production settings, reverse proxy — waiting on DNS.

## Signing in

Every route that touches data requires a session. The exceptions are the sign-in
routes, `/health` and `/ready` (a probe cannot authenticate). FastAPI's `/docs`,
`/redoc` and `/openapi.json` are served in development and closed in production.

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

After Google returns, the API sets that cookie and redirects to
`POST_LOGIN_REDIRECT` (the chat UI at `http://localhost:3000` in development).
The UI then calls the API with `credentials: include`. `CORS_ORIGINS` must name
that UI origin explicitly: a wildcard cannot be combined with cookies.

## Chat UI

```bash
make api && make worker && make frontend
# then open http://localhost:3000
```

Sign in with Google, ask a question, watch tokens and tool steps stream in, stop
a run, open an earlier conversation. Admins get a **Users** link to promote or
deactivate people. Conversations are scoped to the signed-in account; a reload
restores them from Postgres.

First time: `npm --prefix services/frontend install`.

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
     -d '{"title":"Churn model"}'
# -> {"id":"3a1c...","title":"Churn model","created_at":"...","updated_at":"..."}

# 2. Queue a run on it (202 Accepted)
curl -b cookies.txt -X POST localhost:8000/threads/3a1c.../runs \
     -H 'content-type: application/json' \
     -d '{"message":"I want to predict which customers will churn."}'
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
`409`. A fourth in-flight run across a user's threads (cap: 3) is a `429`.

Asking about a run reads the `app.runs` table, so the answer survives a restart
and outlives the hour its event log spends in Redis. The timestamps are there to
be subtracted: `started_at - created_at` is how long the run waited for a
worker, `finished_at - started_at` is how long it took. Cancelling a run that
has already finished is a `409`, not a silent no-op.

The stream is Server-Sent Events. Every record carries the event type, a JSON
payload, and an id:

```
event: tool_call
data: {"seq":1,"type":"tool_call","name":"example_tool","args":{"query":"churn"}}

event: token
data: {"seq":5,"type":"token","text":"The"}

event: done
data: {"seq":442,"type":"done","run_id":"...","status":"succeeded"}
```

Event types are defined once in `mleng.agent.events` and shared by
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

Application tables (users, threads, messages and run records) live in the `app`
schema and are versioned with Alembic. Each change is a numbered file, Postgres
records which files it has applied in `alembic_version`, and `make migrate`
applies whatever is missing — so an existing database can move forward without
being wiped.

```bash
make migration name="add users table"  # autogenerate a revision, then review it
make migrate                           # apply everything pending
make migrate-down                      # revert the last one
make migrate-status                    # current revision + full history
make migrate-check                     # fail if models and database disagree
make stack-migrate                     # apply from inside the api container
```

`migrations/env.py` confines autogenerate to `app`. `make migrate-check` is the
inverse guard, failing when a model has been edited and no migration written
for it.

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

The virtualenv lives at `~/.venvs/mleng`, **outside** this
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

## Layout

A `uv` workspace: one lockfile and one virtualenv shared by several members.
`packages/` holds importable libraries, `services/` holds deployable processes.

```
Makefile                         Developer entry points (make help)
pyproject.toml                   Workspace root (members + shared dev deps)
docker-compose.yml               Postgres, Redis, api + worker + frontend (apps profile)
docker-compose.prod.yml          Production stack: unpublished DB, Caddy TLS, no reload
deploy/Caddyfile                 Reverse proxy: same-host UI + API, security headers
packages/run_bus/                Redis transport shared by api + worker
packages/app_db/                 Postgres persistence: users, threads, messages, runs
packages/mleng/src/mleng/
  core/settings.py               LLM settings from .env
  agent/
    schemas.py                   Structured output (answer / confidence / abstention)
    events.py                    Run event contract shared by every layer
    runner.py                    run_agent(): the single execution path
    builder.py                   create_agent assembly + middleware
    prompts/system.md            System prompt
    tools/                       Empty for now; workflow tools go here
  interfaces/cli.py              Chat REPL (mleng-chat)
services/api/                    HTTP API (FastAPI)
services/worker/                 Background worker (mleng-worker)
services/frontend/               Chat UI (Next.js)
tests/                           Test suite (make test)
```
