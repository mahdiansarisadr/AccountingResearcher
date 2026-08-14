"""The Google OAuth client.

Configured from Google's discovery document rather than hardcoded endpoints, so
authorization, token and JWKS URLs come from Google and stay correct when they
change. Authlib fetches it lazily on first use, which keeps the network out of
import time and out of the test suite.
"""

from __future__ import annotations

from functools import lru_cache

from authlib.integrations.starlette_client import OAuth

from .settings import get_api_settings

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"

PROVIDER = "google"


@lru_cache
def get_oauth() -> OAuth:
    settings = get_api_settings()
    oauth = OAuth()
    oauth.register(
        name=PROVIDER,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        # openid gets an ID token, which is the part we actually verify; email
        # and profile add the address and display name to it.
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
