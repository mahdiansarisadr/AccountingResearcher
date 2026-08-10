"""FastAPI application factory.

Building the app inside a function (rather than at import time) keeps
construction explicit and lets tests create isolated instances with their own
configuration. Endpoints are grouped into routers by concern; later phases add
auth and threads routers here.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .routers import health, runs
from .settings import get_api_settings


def create_app() -> FastAPI:
    settings = get_api_settings()

    app = FastAPI(
        title="Accounting Research Assistant API",
        version=__version__,
    )
    app.state.settings = settings
    app.include_router(health.router)
    app.include_router(runs.router)
    return app


app = create_app()
