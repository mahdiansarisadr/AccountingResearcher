# syntax=docker/dockerfile:1.7
#
# Image for the HTTP API. Build from the REPO ROOT, not this directory:
#   docker build -f services/api/Dockerfile .
# A workspace member cannot build alone; it needs uv.lock and its sibling
# packages/accounting_research.

FROM python:3.12-slim-bookworm

# uv is pinned so image builds resolve exactly like local `make sync` does.
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # The venv lives outside /app so a dev bind-mount over /app cannot shadow it.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    # Cache mounts are a different filesystem, so hardlinking them in would fail.
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependency layer first: only manifests are copied, so editing source code
# does not invalidate the (slow) dependency install. --no-install-workspace
# installs third-party deps but skips our own members, which arrive below.
COPY pyproject.toml uv.lock ./
COPY packages/accounting_research/pyproject.toml packages/accounting_research/
COPY packages/app_db/pyproject.toml packages/app_db/
COPY packages/run_bus/pyproject.toml packages/run_bus/
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package api

# Source layer: changes here rebuild only this step and the one after it.
COPY packages/accounting_research packages/accounting_research
COPY packages/app_db packages/app_db
COPY packages/run_bus packages/run_bus
COPY services/api services/api
COPY test_database_resources test_database_resources
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package api

# Least privilege: nothing at runtime needs to write to the image.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app /opt/venv
USER app

EXPOSE 8000

# Reports the same readiness signal an orchestrator would poll. Uses urllib
# because the slim image has no curl and adding one just for this is waste.
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# Production default; compose overrides this with --reload for development.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
