# syntax=docker/dockerfile:1.7
#
# Image for the background worker. Build from the REPO ROOT, not this directory:
#   docker build -f services/worker/Dockerfile .
# See services/api/Dockerfile for the reasoning behind each stage; the only
# differences here are the installed member and the entrypoint.

FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/accounting_research/pyproject.toml packages/accounting_research/
COPY services/api/pyproject.toml services/api/
COPY services/worker/pyproject.toml services/worker/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package worker

COPY packages/accounting_research packages/accounting_research
COPY services/worker services/worker
COPY test_database_resources test_database_resources
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package worker

RUN useradd --create-home --uid 10001 app && chown -R app:app /app /opt/venv
USER app

# No HEALTHCHECK: the worker exposes no endpoint, and its real liveness signal
# (jobs being consumed) does not exist until Phase 1.
CMD ["ar-worker"]
