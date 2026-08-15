"""Optional error tracking.

Sentry is a configuration choice, not a dependency of boot: an empty DSN leaves
this as a no-op so a missing key cannot take the API down. PII stays off —
emails live in the user table, not in the error tracker.
"""

from __future__ import annotations

from .settings import ApiSettings


def init_sentry(settings: ApiSettings, *, release: str) -> None:
    if not settings.sentry_dsn:
        return

    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=release,
        send_default_pii=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
