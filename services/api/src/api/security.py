"""The session cookie: how a browser proves who it is on every request.

A signed JWT in an HttpOnly cookie. Signed rather than encrypted because none of
it is secret — it says which user this is, and the signature is what stops that
being edited. HttpOnly because a cookie JavaScript can read is a cookie an XSS
bug can steal.

The token carries an identity and nothing else. Role and active status are read
from the database on every request, so deactivating an account or demoting an
admin takes effect immediately instead of whenever their week-old cookie happens
to expire.
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import Response
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

from .settings import ApiSettings

logger = logging.getLogger("api.security")

_ALGORITHM = "HS256"

# exp is essential, not optional: a token with no expiry that we accept anyway is
# a permanent credential.
_REQUIRED_CLAIMS = jwt.JWTClaimsRegistry(sub={"essential": True}, exp={"essential": True})


def _key(settings: ApiSettings) -> OctKey:
    return OctKey.import_key(settings.session_secret)


def issue_session(response: Response, user_id: uuid.UUID, settings: ApiSettings) -> str:
    """Sign a session for this user and attach it to the response."""
    issued_at = int(time.time())
    token = jwt.encode(
        {"alg": _ALGORITHM},
        {
            "sub": str(user_id),
            "iat": issued_at,
            "exp": issued_at + settings.session_ttl_seconds,
        },
        _key(settings),
    )

    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        # Off over plain HTTP, or the browser would never send it back to a
        # localhost API and nobody could sign in while developing.
        secure=settings.cookies_require_https,
        # Lax, not Strict: the browser arrives back from Google on a cross-site
        # top-level navigation, and Strict would withhold the cookie there.
        samesite="lax",
        path="/",
    )
    return token


def clear_session(response: Response, settings: ApiSettings) -> None:
    """Remove the session cookie.

    The same attributes as when it was set — a browser matches on name, path and
    domain, and a mismatch leaves the original cookie in place.
    """
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookies_require_https,
        samesite="lax",
    )


def read_session(token: str, settings: ApiSettings) -> uuid.UUID | None:
    """Return the user id a token vouches for, or None if it vouches for nothing.

    One answer for every kind of invalid: forged signature, expired, truncated,
    signed with a secret from a previous deployment. The caller has the same
    response to all of them, and distinguishing them in an error message tells an
    attacker which part they nearly got right.
    """
    try:
        claims = jwt.decode(token, _key(settings)).claims
        _REQUIRED_CLAIMS.validate(claims)
        return uuid.UUID(str(claims["sub"]))
    except (JoseError, ValueError, KeyError) as exc:
        logger.debug("rejected session cookie: %s", type(exc).__name__)
        return None
