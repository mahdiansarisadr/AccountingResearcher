"""FastAPI application factory.

Building the app inside a function (rather than at import time) keeps
construction explicit and lets tests create isolated instances with their own
configuration. Endpoints are grouped into routers by concern.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .routers import admin, auth, health, runs, threads
from .settings import get_api_settings

# The OAuth handshake takes two requests — out to Google and back — and the state
# and nonce that tie them together have to survive in between. Ten minutes is
# long enough to read a consent screen and short enough that an abandoned attempt
# does not linger.
OAUTH_HANDSHAKE_TTL_SECONDS = 600


def create_app() -> FastAPI:
    settings = get_api_settings()

    app = FastAPI(
        title="Accounting Research Assistant API",
        version=__version__,
    )
    app.state.settings = settings

    # Authlib keeps the OAuth state and nonce here. Separate from our own session
    # cookie and far shorter-lived: this one exists only during a sign-in, while
    # that one is the sign-in. Same signing key, since both are ours to verify.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="ar_oauth",
        max_age=OAUTH_HANDSHAKE_TTL_SECONDS,
        # Lax so the cookie survives Google's redirect back, which is a
        # cross-site navigation.
        same_site="lax",
        https_only=settings.cookies_require_https,
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(threads.router)
    app.include_router(runs.router)
    return app


app = create_app()
