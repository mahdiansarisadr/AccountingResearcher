"""Signing in with Google, and signing out.

The flow: ``/auth/login`` sends the browser to Google, Google sends it back to
``/auth/callback`` with a code, and the code is exchanged for an ID token. Authlib
verifies that token against Google's published signing keys, so the claims can be
trusted as Google's statement about who this is.

What remains is our decision, and it is deliberately kept out of the OAuth
plumbing: :func:`verify_identity` is a plain function over claims, so the rule
that actually guards this tool — one company domain, nobody else — can be read
and tested without an OAuth handshake anywhere near it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

import app_db
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import CurrentUser, SessionDep, SettingsDep
from ..oauth import get_oauth
from ..schemas import UserResponse
from ..security import clear_session, issue_session
from ..settings import ApiSettings

logger = logging.getLogger("api.auth")

router = APIRouter(tags=["auth"])


@dataclass(frozen=True)
class Identity:
    """A Google account we have decided to accept."""

    email: str
    name: str | None
    avatar_url: str | None


def verify_identity(claims: Mapping[str, Any], settings: ApiSettings) -> Identity:
    """Decide whether the person Google just vouched for may use this tool.

    Three ways to be refused, all reported identically. Which check failed is
    useful to us in the log and not to whoever is trying.
    """
    refused = HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"sign-in is restricted to {settings.normalized_domain} accounts",
    )

    email = app_db.normalize_email(str(claims.get("email") or ""))
    if not email:
        logger.warning("sign-in refused: no email in claims")
        raise refused

    # An unverified address proves nothing: it is a string someone typed into an
    # account, and accepting it would make the domain check meaningless.
    if not claims.get("email_verified"):
        logger.warning("sign-in refused for %s: email not verified", email)
        raise refused

    domain = settings.normalized_domain
    if not domain:
        # No configured domain would otherwise mean "everyone", which is the
        # opposite of the intended default.
        logger.error("sign-in refused: ALLOWED_EMAIL_DOMAIN is not set")
        raise refused

    # The leading @ is what makes this a domain check rather than a suffix match:
    # without it, evil-example.com would pass for example.com.
    if not email.endswith(f"@{domain}"):
        logger.warning("sign-in refused for %s: outside %s", email, domain)
        raise refused

    # Workspace accounts also carry their hosted domain. When present it must
    # agree — a second, independent statement of the same fact.
    hosted_domain = str(claims.get("hd") or "").strip().lower()
    if hosted_domain and hosted_domain != domain:
        logger.warning("sign-in refused for %s: hd=%s", email, hosted_domain)
        raise refused

    return Identity(
        email=email,
        name=(str(claims["name"]) if claims.get("name") else None),
        avatar_url=(str(claims["picture"]) if claims.get("picture") else None),
    )


def sign_in(session: Session, identity: Identity, settings: ApiSettings) -> app_db.User:
    """Record an accepted sign-in and return the user it belongs to."""
    is_initial_admin = bool(settings.initial_admin_email) and (
        identity.email == app_db.normalize_email(settings.initial_admin_email)
    )

    user = app_db.upsert_user(
        session,
        email=identity.email,
        name=identity.name,
        avatar_url=identity.avatar_url,
        initial_admin=is_initial_admin,
    )

    if not user.is_active:
        # An admin has revoked this account. Refusing here rather than issuing a
        # cookie that every subsequent request would reject.
        logger.warning("sign-in refused for %s: deactivated", user.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="this account is deactivated"
        )

    app_db.record_login(session, user)
    logger.info("signed in %s as %s", user.email, user.role.value)
    return user


async def exchange_code_for_claims(request: Request) -> dict[str, Any]:
    """Complete the handshake and return what Google says about the user.

    A dependency rather than inline code so the callback can be exercised
    end-to-end in tests — everything after this point is ours, and this is the
    only part that needs Google on the other end of a socket.
    """
    client = get_oauth().google
    token = await client.authorize_access_token(request)

    # Authlib parses and verifies the ID token during the exchange, checking the
    # signature against Google's keys and the nonce against the one it stored
    # before the redirect. userinfo() is the fallback if no ID token came back.
    claims = token.get("userinfo") or await client.userinfo(token=token)
    return dict(claims)


ClaimsDep = Annotated[dict[str, Any], Depends(exchange_code_for_claims)]


@router.get("/auth/login")
async def login(request: Request, settings: SettingsDep) -> Response:
    """Send the browser to Google's consent screen."""
    if not settings.sign_in_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google sign-in is not configured",
        )

    # hd pre-filters Google's account chooser to a Workspace domain. It is a
    # convenience, not a control — the callback still checks the email. It is
    # omitted for consumer Gmail, where hd=gmail.com is not a hosted domain and
    # has been seen to send people into a passkey/Bluetooth prompt instead of a
    # password.
    extra: dict[str, str] = {"prompt": "select_account"}
    if settings.hosted_domain_hint:
        extra["hd"] = settings.hosted_domain_hint
    return await get_oauth().google.authorize_redirect(
        request, settings.oauth_redirect_url, **extra
    )


@router.get("/auth/callback")
async def callback(
    claims: ClaimsDep, session: SessionDep, settings: SettingsDep
) -> Response:
    """Accept or refuse the account Google returned, and start a session."""
    user = sign_in(session, verify_identity(claims, settings), settings)

    # 303 so the browser follows with a GET regardless of what it arrived with.
    response = RedirectResponse(settings.post_login_redirect, status_code=303)
    issue_session(response, user.id, settings)
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(settings: SettingsDep) -> Response:
    """Drop the session cookie.

    No authentication required: someone with an expired or unreadable cookie
    still needs a way to be rid of it.
    """
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_session(response, settings)
    return response


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> UserResponse:
    """Who am I, and what may I do."""
    return UserResponse.of(user)


class DevSignInRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str | None = Field(default=None, max_length=200)


@router.post("/auth/dev-login", response_model=UserResponse)
def dev_login(
    payload: DevSignInRequest, session: SessionDep, settings: SettingsDep, response: Response
) -> UserResponse:
    """Start a session without Google, for local development only.

    Exists so the API and the frontend can be built before OAuth credentials do.
    It is off unless ``DEV_LOGIN_ENABLED`` is set, and settings refuse to load at
    all if that is set in production — a route that mints sessions for arbitrary
    addresses must not be one env var away from being reachable.

    The domain rule still applies: this is a shortcut past Google, not past the
    access control.
    """
    if not settings.dev_login_enabled:
        # 404 rather than 403: a disabled route should not confirm it exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    logger.warning("development sign-in used for %s", payload.email)
    identity = verify_identity(
        {"email": payload.email, "email_verified": True, "name": payload.name}, settings
    )
    user = sign_in(session, identity, settings)

    issue_session(response, user.id, settings)
    return UserResponse.of(user)
