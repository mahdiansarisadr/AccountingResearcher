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


# Google's `hd` parameter is a hint for Workspace account choosers. Consumer
# Gmail is not a hosted domain; sending hd=gmail.com has been seen to skip the
# normal password prompt and dump the user into a passkey/Bluetooth flow.
_CONSUMER_GOOGLE_DOMAINS = frozenset({"gmail.com", "googlemail.com", "google.com"})


class ApiSettings(BaseSettings):
    # extra="ignore" so the shared .env can also hold the agent's MLENG_* keys.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Defaults target local development (host ports from docker-compose).
    # In Compose, these are overridden with service hostnames (postgres, redis).
    database_url: str = "postgresql://mleng:mleng@localhost:5433/mleng"
    redis_url: str = "redis://localhost:6379/0"

    environment: str = "development"

    # Seconds a readiness probe waits on a dependency before calling it down.
    probe_timeout: float = 2.0

    # Ceiling on a single run before the queue abandons it. Training a model
    # can take a few minutes; a wedged job is still killed.
    run_timeout_seconds: int = 600

    # How long a stream waits with no events before telling the client to stop.
    stream_idle_timeout_seconds: float = 700.0

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
    session_cookie_name: str = "mleng_session"
    session_ttl_seconds: int = 7 * 24 * 60 * 60

    # Signs the session cookie. Deliberately has no default: a committed
    # fallback is a published private key. In development an ephemeral one is
    # generated per process, which costs a re-login after every restart and
    # nothing else. Production refuses to start without it.
    session_secret: str = ""

    # Where the browser lands after a successful sign-in.
    post_login_redirect: str = "http://localhost:3000"

    # Origins allowed to call this API from a browser. A wildcard cannot be used
    # with credentialed cookies, so this is an explicit list.
    cors_origins: str = "http://localhost:3000"

    # Public hostname the reverse proxy serves (no scheme). Production refuses
    # to start without it, because TrustedHost, OAuth and CORS all have to name
    # the same place.
    public_host: str = ""

    # Optional. Empty means error tracking is off; a missing DSN must not stop boot.
    sentry_dsn: str = ""

    # 0 disables the limiter, which is what the test suite uses so a Redis
    # stand-in is not a prerequisite for every request.
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # Ceiling on an incoming JSON body. Chat messages are already capped at
    # 4_000 characters; this is the backstop for anything that is not an upload.
    max_request_bytes: int = 64 * 1024

    # Dataset uploads are larger; applied only to POST /threads/{id}/files.
    max_upload_bytes: int = 25 * 1024 * 1024

    # Queued + running, across every thread. Stops one account filling the queue.
    max_concurrent_runs_per_user: int = 3

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
            if not self.google_client_id or not self.google_client_secret:
                raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required in production")
            if not self.public_host.strip() or "://" in self.public_host or "/" in self.public_host:
                raise ValueError("PUBLIC_HOST is required in production and must be a hostname")
            self._require_https(self.oauth_redirect_url, "OAUTH_REDIRECT_URL")
            self._require_https(self.post_login_redirect, "POST_LOGIN_REDIRECT")
            if not self.cors_origin_list:
                raise ValueError("CORS_ORIGINS is required in production")
            for origin in self.cors_origin_list:
                self._require_https(origin, "CORS_ORIGINS")

        if self.session_secret and len(self.session_secret) < MIN_SECRET_CHARS:
            raise ValueError(f"SESSION_SECRET must be at least {MIN_SECRET_CHARS} characters")

        if not self.session_secret:
            self.session_secret = secrets.token_urlsafe(48)

        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def normalized_domain(self) -> str:
        """The allowed domain, comparable against the tail of an email address."""
        return self.allowed_email_domain.strip().lower().lstrip("@")

    @property
    def hosted_domain_hint(self) -> str | None:
        """Value for Google's ``hd`` parameter, or None when it would not help.

        Only a Workspace domain belongs here. The callback still checks the
        email itself; this is only a convenience for the account picker.
        """
        domain = self.normalized_domain
        if not domain or domain in _CONSUMER_GOOGLE_DOMAINS:
            return None
        return domain

    @staticmethod
    def _require_https(url: str, name: str) -> None:
        if not url.startswith("https://"):
            raise ValueError(f"{name} must be HTTPS in production")


@lru_cache
def get_api_settings() -> ApiSettings:
    return ApiSettings()
