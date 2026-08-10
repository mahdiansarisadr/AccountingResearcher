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

.PHONY: help sync up down ps logs seed catalog chat api worker health doctor \
        clean-venv build stack stack-down stack-logs stack-migrate \
        migrate migrate-down migrate-status migration

help:
	@echo "Environment"
	@echo "  make sync        Install/refresh all workspace members into $(VENV)"
	@echo "  make doctor      Verify the environment can import every member"
	@echo "  make clean-venv  Delete the virtualenv"
	@echo "Infrastructure"
	@echo "  make up / down / ps / logs   Postgres + Redis via docker compose"
	@echo "  make build       Build the api and worker images"
	@echo "  make stack       Run everything in containers (adds api + worker)"
	@echo "  make stack-down  Stop the full stack"
	@echo "  make stack-logs  Follow api + worker logs"
	@echo "Data"
	@echo "  make seed        Load synthetic accounting data"
	@echo "  make catalog     Build the schema catalog used for table selection"
	@echo "Migrations (application schema)"
	@echo "  make migrate         Apply pending migrations"
	@echo "  make migrate-down    Revert the last migration"
	@echo "  make migrate-status  Show current revision and history"
	@echo '  make migration name="add runs table"   Create a new revision'
	@echo "Run"
	@echo "  make api         Serve the HTTP API with reload"
	@echo "  make worker      Run the background worker"
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
	docker compose --profile apps logs -f api worker

# Migrations are a deliberate step, never something the app runs on startup:
# with more than one replica, concurrent startups would race on the same DDL.
stack-migrate:
	docker compose --profile apps run --rm api alembic -c /app/services/api/alembic.ini upgrade head

migrate:
	$(ALEMBIC) upgrade head

migrate-down:
	$(ALEMBIC) downgrade -1

migrate-status:
	$(ALEMBIC) current --verbose
	$(ALEMBIC) history

migration:
	@test -n "$(name)" || { echo 'usage: make migration name="add runs table"'; exit 1; }
	$(ALEMBIC) revision -m "$(name)"

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

health:
	@curl -fsS localhost:8000/health && echo
	@curl -sS -o /dev/stderr -w 'ready: HTTP %{http_code}\n' localhost:8000/ready
