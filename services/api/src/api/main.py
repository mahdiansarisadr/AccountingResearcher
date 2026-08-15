"""FastAPI application factory.

Building the app inside a function (rather than at import time) keeps
construction explicit and lets tests create isolated instances with their own
configuration. Endpoints are grouped into routers by concern.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from . import __version__
from .deps import get_redis
from .logconfig import configure_logging
from .middleware import RateLimitMiddleware, RequestContextMiddleware, RequestSizeLimitMiddleware
from .observability import init_sentry
from .routers import admin, auth, health, runs, threads
from .settings import ApiSettings, get_api_settings

# The OAuth handshake takes two requests — out to Google and back — and the state
# and nonce that tie them together have to survive in between. Ten minutes is
# long enough to read a consent screen and short enough that an abandoned attempt
# does not linger.
OAUTH_HANDSHAKE_TTL_SECONDS = 600


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    settings = settings or get_api_settings()
    configure_logging(settings)
    init_sentry(settings, release=__version__)

    # OpenAPI is a map of every route. Fine on a laptop; on the public internet
    # it is an unauthenticated description of the attack surface.
    docs = None if settings.is_production else "/docs"
    app = FastAPI(
        title="Accounting Research Assistant API",
        version=__version__,
        docs_url=docs,
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings
    app.state.redis_factory = get_redis

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
    app.add_middleware(
        RateLimitMiddleware,
        settings=settings,
        redis_factory=lambda: app.state.redis_factory(),
    )
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(RequestContextMiddleware, settings=settings)

    if settings.is_production and settings.public_host:
        # Only Caddy can reach this process on the compose network, so trusting
        # forwarded headers is how the rate limiter sees the browser rather than
        # the proxy. TrustedHost then refuses a request that did not name us.
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=[settings.public_host])
        app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    # Added last so it runs first: a preflight must be answered before anything
    # else looks at the request, and a 429/413 still needs CORS headers or the
    # browser will hide them. Origins are explicit because a wildcard cannot be
    # combined with credentialed cookies.
    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(admin.router)
    app.include_router(threads.router)
    app.include_router(runs.router)
    return app


app = create_app()
