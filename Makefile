# Developer entry points for the Accounting Research Assistant workspace.
#
# The virtualenv deliberately lives OUTSIDE this repo. This checkout sits under
# ~/Desktop, which macOS syncs to iCloud; the sync daemon sets the UF_HIDDEN
# flag on .pth files (CPython's site.py silently skips hidden .pth files, which
# breaks editable installs) and creates "foo 2.py" conflict copies inside
# site-packages. Keeping the environment out of the synced tree avoids both.
# Override with: make sync VENV=/some/other/path
VENV ?= $(HOME)/.venvs/accounting-research
UV := UV_PROJECT_ENVIRONMENT=$(VENV) uv
PY := $(VENV)/bin/python

ALEMBIC := $(UV) run --no-sync alembic -c services/api/alembic.ini

.PHONY: help sync up down ps logs seed catalog chat api worker frontend health doctor \
        clean-venv build stack stack-down stack-logs stack-migrate test \
        migrate migrate-down migrate-status migration migrate-check \
        prod-build prod-up prod-down prod-logs prod-migrate prod-seed prod-catalog

help:
	@echo "Environment"
	@echo "  make sync        Install/refresh all workspace members into $(VENV)"
	@echo "  make doctor      Verify the environment can import every member"
	@echo "  make clean-venv  Delete the virtualenv"
	@echo "Infrastructure"
	@echo "  make up / down / ps / logs   Postgres + Redis via docker compose"
	@echo "  make build       Build the api, worker and frontend images"
	@echo "  make stack       Run everything in containers (adds api + worker + frontend)"
	@echo "  make stack-down  Stop the full stack"
	@echo "  make stack-logs  Follow api + worker + frontend logs"
	@echo "  make stack-migrate  Apply from inside the api container"
	@echo "Production (needs PUBLIC_HOST, e.g. research.example.com)"
	@echo "  make prod-build / prod-up / prod-down / prod-logs"
	@echo "  make prod-migrate / prod-seed / prod-catalog"
	@echo "Data"
	@echo "  make seed        Load synthetic accounting data"
	@echo "  make catalog     Build the schema catalog used for table selection"
	@echo "Migrations (application schema)"
	@echo "  make migrate         Apply pending migrations"
	@echo "  make migrate-down    Revert the last migration"
	@echo "  make migrate-status  Show current revision and history"
	@echo "  make migrate-check   Fail if the models and the database disagree"
	@echo '  make migration name="add users table"  Create a new revision'
	@echo "Test"
	@echo "  make test        Run the test suite"
	@echo "Run"
	@echo "  make api         Serve the HTTP API with reload"
	@echo "  make worker      Run the background worker"
	@echo "  make frontend    Serve the chat UI on :3000"
	@echo "  make chat        Interactive agent CLI"
	@echo "  make health      Curl /health and /ready"

sync:
	$(UV) sync --all-packages
	@$(MAKE) --no-print-directory doctor

# Fails loudly if editable installs are not importable, which is the symptom
# every environment problem in this project has surfaced as.
doctor:
	@$(PY) -c "import accounting_research, run_bus, api, worker; print('env OK:', '$(VENV)')"

clean-venv:
	rm -rf $(VENV)

up:
	docker compose up -d

down:
	docker compose down

ps:
	docker compose ps

logs:
	docker compose logs -f

# The "apps" profile adds the containerized api + worker on top of the
# infrastructure services; without it compose starts Postgres + Redis only.
build:
	docker compose --profile apps build

stack:
	docker compose --profile apps up -d

stack-down:
	docker compose --profile apps down

stack-logs:
	docker compose --profile apps logs -f api worker frontend

# Migrations are a deliberate step, never something the app runs on startup:
# with more than one replica, concurrent startups would race on the same DDL.
stack-migrate:
	docker compose --profile apps run --rm api alembic -c /app/services/api/alembic.ini upgrade head

COMPOSE_PROD := docker compose -f docker-compose.prod.yml

prod-build:
	@test -n "$(PUBLIC_HOST)" || { echo 'PUBLIC_HOST is required, e.g. make prod-build PUBLIC_HOST=research.example.com'; exit 1; }
	PUBLIC_HOST=$(PUBLIC_HOST) $(COMPOSE_PROD) build

prod-up:
	@test -n "$(PUBLIC_HOST)" || { echo 'PUBLIC_HOST is required, e.g. make prod-up PUBLIC_HOST=research.example.com'; exit 1; }
	PUBLIC_HOST=$(PUBLIC_HOST) $(COMPOSE_PROD) up -d

prod-down:
	$(COMPOSE_PROD) down

prod-logs:
	$(COMPOSE_PROD) logs -f api worker frontend caddy

prod-migrate:
	@test -n "$(PUBLIC_HOST)" || { echo 'PUBLIC_HOST is required, e.g. make prod-migrate PUBLIC_HOST=research.example.com'; exit 1; }
	PUBLIC_HOST=$(PUBLIC_HOST) $(COMPOSE_PROD) run --rm api alembic -c /app/services/api/alembic.ini upgrade head

prod-seed:
	@test -n "$(PUBLIC_HOST)" || { echo 'PUBLIC_HOST is required'; exit 1; }
	PUBLIC_HOST=$(PUBLIC_HOST) $(COMPOSE_PROD) run --rm api ar-seed

prod-catalog:
	@test -n "$(PUBLIC_HOST)" || { echo 'PUBLIC_HOST is required'; exit 1; }
	PUBLIC_HOST=$(PUBLIC_HOST) $(COMPOSE_PROD) run --rm api ar-catalog

migrate:
	$(ALEMBIC) upgrade head

migrate-down:
	$(ALEMBIC) downgrade -1

migrate-status:
	$(ALEMBIC) current --verbose
	$(ALEMBIC) history

# Catches a model edited without a migration written for it.
migrate-check:
	$(ALEMBIC) check

# --autogenerate diffs the models against the live database, so the revision
# arrives populated; review it before committing, as it cannot see intent.
migration:
	@test -n "$(name)" || { echo 'usage: make migration name="add users table"'; exit 1; }
	$(ALEMBIC) revision --autogenerate -m "$(name)"

test:
	$(UV) run --no-sync pytest

seed:
	$(UV) run --no-sync ar-seed

catalog:
	$(UV) run --no-sync ar-catalog

chat:
	$(UV) run --no-sync ar-chat

api:
	$(UV) run --no-sync uvicorn api.main:app --reload --port 8000

worker:
	$(UV) run --no-sync ar-worker

frontend:
	npm --prefix services/frontend run dev

health:
	@curl -fsS localhost:8000/health && echo
	@curl -sS -o /dev/stderr -w 'ready: HTTP %{http_code}\n' localhost:8000/ready
