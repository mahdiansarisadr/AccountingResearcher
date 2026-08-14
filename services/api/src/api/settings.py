"""API configuration, read from the environment and validated at startup.

Uses pydantic-settings so a missing or malformed value fails fast on boot
rather than surfacing as a confusing runtime error later. Field names map to
env vars case-insensitively (``database_url`` <- ``DATABASE_URL``).
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Below this a signing key is not worth having; joserfc warns under 112 bits and
# HS256 wants at least as much entropy as its output to be worth the algorithm.
MIN_SECRET_CHARS = 32


class ApiSettings(BaseSettings):
    # extra="ignore" so the shared .env can also hold the agent's AR_* keys.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults target local development (host ports from docker-compose).
    # In Compose, these are overridden with service hostnames (postgres, redis).
    database_url: str = "postgresql://ar_admin:ar_admin@localhost:5433/accounting"
    redis_url: str = "redis://localhost:6379/0"

    environment: str = "development"

    # Seconds a readiness probe waits on a dependency before calling it down.
    probe_timeout: float = 2.0

    # Ceiling on a single run before the queue abandons it. Generous next to the
    # ~10s target so a slow-but-working run is not killed, while a wedged one is.
    run_timeout_seconds: int = 120

    # How long a stream waits with no events before telling the client to stop.
    stream_idle_timeout_seconds: float = 150.0

    # --- Google sign-in ---
    # Empty by default so the service still boots (and /health still answers)
    # without credentials; /auth/login reports itself unavailable instead.
    google_client_id: str = ""
    google_client_secret: str = ""

    # Where Google sends the browser back. Must match the redirect URI registered
    # in the Google Cloud console exactly, including scheme and port.
    oauth_redirect_url: str = "http://localhost:8000/auth/callback"

    # The single company domain allowed to sign in. Every other verified Google
    # account is refused, which is the whole access control model for this tool.
    allowed_email_domain: str = ""

    # Seeded manually, because the first admin cannot be appointed by an admin.
    # This account is also kept active and admin on every login, so an instance
    # cannot be locked out of its own user management.
    initial_admin_email: str = ""

    # --- Session ---
    session_cookie_name: str = "ar_session"
    session_ttl_seconds: int = 7 * 24 * 60 * 60

    # Signs the session cookie. Deliberately has no default: a committed
    # fallback is a published private key. In development an ephemeral one is
    # generated per process, which costs a re-login after every restart and
    # nothing else. Production refuses to start without it.
    session_secret: str = ""

    # Where the browser lands after a successful sign-in. Points at the frontend
    # once there is one.
    post_login_redirect: str = "/me"

    # --- Development sign-in ---
    # Issues a session for any allowed-domain email without contacting Google,
    # so the API and the frontend can be worked on before OAuth credentials
    # exist. Refuses to run outside development, enforced below.
    dev_login_enabled: bool = False

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    @property
    def cookies_require_https(self) -> bool:
        """Whether to mark cookies Secure.

        Off in development because localhost is plain HTTP and a Secure cookie
        would simply never be sent back.
        """
        return self.is_production

    @property
    def sign_in_configured(self) -> bool:
        return bool(
            self.google_client_id and self.google_client_secret and self.allowed_email_domain
        )

    @model_validator(mode="after")
    def _validate_secrets(self) -> ApiSettings:
        if self.is_production:
            if not self.session_secret:
                raise ValueError(
                    "SESSION_SECRET is required in production; generate one with"
                    " `python -c 'import secrets; print(secrets.token_urlsafe(48))'`"
                )
            if self.dev_login_enabled:
                # A backdoor that mints sessions for arbitrary addresses. Failing
                # to boot is the only reaction to this that cannot be ignored.
                raise ValueError("DEV_LOGIN_ENABLED must be off in production")
            if not self.allowed_email_domain:
                raise ValueError("ALLOWED_EMAIL_DOMAIN is required in production")

        if self.session_secret and len(self.session_secret) < MIN_SECRET_CHARS:
            raise ValueError(f"SESSION_SECRET must be at least {MIN_SECRET_CHARS} characters")

        if not self.session_secret:
            self.session_secret = secrets.token_urlsafe(48)

        return self

    @property
    def normalized_domain(self) -> str:
        """The allowed domain, comparable against the tail of an email address."""
        return self.allowed_email_domain.strip().lower().lstrip("@")


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
