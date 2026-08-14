"""Signing in: who is let in, what their session can do, and how it ends.

Google is never contacted. The handshake is a single dependency, overridden here
with the claims Google would have returned, and everything downstream of it — the
domain rule, the account it creates, the cookie it issues — runs for real.

The domain check is the entire access control model of this tool, so it is tested
from both directions: the addresses that must be accepted, and the near misses
that must not be.
"""

from __future__ import annotations

import base64
import json
import uuid

import app_db
import pytest
from api.routers.auth import exchange_code_for_claims, verify_identity
from api.security import issue_session, read_session
from api.settings import ApiSettings, get_api_settings
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient

from .conftest import DOMAIN, session_cookie, sign_in


def claims(email: str, **overrides) -> dict:
    """What Google returns for a verified Workspace account."""
    return {
        "email": email,
        "email_verified": True,
        "name": "Ana Lyst",
        "picture": "https://lh3.example/photo.png",
        "hd": DOMAIN,
        **overrides,
    }


@pytest.fixture
def complete_sign_in(api_app):
    """Finish a sign-in with given claims, without following the redirect."""

    def complete(email: str = f"analyst@{DOMAIN}", **claim_overrides):
        api_app.dependency_overrides[exchange_code_for_claims] = lambda: claims(
            email, **claim_overrides
        )
        with TestClient(api_app, follow_redirects=False) as client:
            return client.get("/auth/callback?code=whatever&state=whatever")

    return complete


@pytest.fixture
def with_settings(api_app, api_settings):
    """Run against a variation on the test configuration."""

    def override(**changes) -> ApiSettings:
        changed = api_settings.model_copy(update=changes)
        api_app.dependency_overrides[get_api_settings] = lambda: changed
        return changed

    return override


# --- The domain rule ---------------------------------------------------------


def test_a_company_account_is_accepted(api_settings) -> None:
    identity = verify_identity(claims(f"ana@{DOMAIN}"), api_settings)

    assert identity.email == f"ana@{DOMAIN}"
    assert identity.name == "Ana Lyst"
    assert identity.avatar_url == "https://lh3.example/photo.png"


def test_an_address_is_accepted_whatever_its_case(api_settings) -> None:
    identity = verify_identity(claims(f"Ana.Lyst@{DOMAIN.upper()}"), api_settings)

    assert identity.email == f"ana.lyst@{DOMAIN}"


@pytest.mark.parametrize(
    "email",
    [
        "someone@gmail.com",
        # A domain that merely ends with ours. Comparing against "@domain" rather
        # than "domain" is what rejects this.
        f"someone@evil-{DOMAIN}",
        # A subdomain is a different domain, and not one we vouch for.
        f"someone@sub.{DOMAIN}",
        # Our domain present, but not where it counts.
        f"someone@{DOMAIN}.attacker.test",
        "",
    ],
)
def test_an_outside_account_is_refused(api_settings, email) -> None:
    with pytest.raises(HTTPException) as refusal:
        verify_identity(claims(email), api_settings)

    assert refusal.value.status_code == 403


def test_an_unverified_address_is_refused(api_settings) -> None:
    # Unverified means nobody proved they control it, which would make the domain
    # check meaningless — anyone could put a company address on an account.
    with pytest.raises(HTTPException) as refusal:
        verify_identity(claims(f"ana@{DOMAIN}", email_verified=False), api_settings)

    assert refusal.value.status_code == 403


def test_a_hosted_domain_that_disagrees_is_refused(api_settings) -> None:
    with pytest.raises(HTTPException):
        verify_identity(claims(f"ana@{DOMAIN}", hd="somewhere-else.test"), api_settings)


def test_an_account_without_a_hosted_domain_is_judged_on_its_address(api_settings) -> None:
    # hd is absent for non-Workspace accounts, so it cannot be required — the
    # address is what decides, and hd only has to agree when it is there.
    identity = verify_identity(claims(f"ana@{DOMAIN}", hd=None), api_settings)

    assert identity.email == f"ana@{DOMAIN}"


def test_nobody_is_accepted_when_no_domain_is_configured(api_settings) -> None:
    # An unset domain must mean "no one", not "everyone".
    with pytest.raises(HTTPException):
        verify_identity(
            claims(f"ana@{DOMAIN}"),
            api_settings.model_copy(update={"allowed_email_domain": ""}),
        )


# --- The callback ------------------------------------------------------------


def test_a_first_sign_in_creates_an_account_and_a_session(
    complete_sign_in, session, api_settings
) -> None:
    response = complete_sign_in()

    assert response.status_code == 303
    assert response.headers["location"] == api_settings.post_login_redirect
    assert api_settings.session_cookie_name in response.cookies

    user = app_db.get_by_email(session, f"analyst@{DOMAIN}")
    # A member: access is granted by the domain, authority is granted deliberately.
    assert user.role is app_db.UserRole.MEMBER
    assert user.last_login_at is not None


def test_the_session_cookie_cannot_be_read_by_the_page(complete_sign_in) -> None:
    cookie = complete_sign_in().headers["set-cookie"].lower()

    # HttpOnly so an XSS bug cannot read it; Lax so it survives the return trip
    # from Google without being sent on arbitrary cross-site requests.
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    # Not Secure here only because local development is plain HTTP, and a Secure
    # cookie would never come back. The production case is below.
    assert "secure" not in cookie


def test_in_production_the_session_cookie_is_https_only(
    api_app, with_settings, complete_sign_in
) -> None:
    with_settings(environment="production")

    cookie = complete_sign_in().headers["set-cookie"].lower()

    assert "secure" in cookie


def test_the_configured_first_admin_signs_in_as_an_admin(api_app) -> None:
    api_app.dependency_overrides[exchange_code_for_claims] = lambda: claims(
        f"founder@{DOMAIN}"
    )

    with TestClient(api_app) as client:
        # Redirect followed, so this also shows the fresh cookie is accepted by
        # the guard on the very next request.
        body = client.get("/auth/callback?code=c&state=s").json()

    assert body["email"] == f"founder@{DOMAIN}"
    assert body["role"] == "admin"


def test_an_outsider_is_refused_and_no_account_is_created(
    complete_sign_in, session
) -> None:
    response = complete_sign_in("someone@gmail.com")

    assert response.status_code == 403
    assert DOMAIN in response.json()["detail"]
    assert "set-cookie" not in response.headers
    assert app_db.get_by_email(session, "someone@gmail.com") is None


def test_a_deactivated_account_is_refused_at_sign_in(
    complete_sign_in, session, owner
) -> None:
    # Refused here rather than handed a cookie that every later request rejects.
    app_db.update_user(session, owner.id, is_active=False)

    response = complete_sign_in(owner.email)

    assert response.status_code == 403
    assert "deactivated" in response.json()["detail"]
    assert "set-cookie" not in response.headers


def test_sign_in_is_unavailable_rather_than_broken_when_google_is_not_configured(
    api_app, with_settings
) -> None:
    with_settings(google_client_id="", google_client_secret="")

    with TestClient(api_app) as client:
        response = client.get("/auth/login")

    # 503 with a reason, rather than attempting a handshake that cannot succeed.
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


# --- The session -------------------------------------------------------------


def test_who_am_i(as_member, owner) -> None:
    body = as_member.get("/me").json()

    assert body["email"] == owner.email
    assert body["role"] == "member"
    assert body["is_active"] is True


def test_without_a_session_there_is_no_answer(anonymous) -> None:
    assert anonymous.get("/me").status_code == 401


@pytest.mark.parametrize(
    "cookie",
    [
        "not-a-token",
        "",
        # The shape of a JWT, with a signature that is not ours.
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhIn0.YWJj",
    ],
)
def test_a_cookie_we_did_not_sign_is_refused(anonymous, api_settings, cookie) -> None:
    anonymous.cookies.set(api_settings.session_cookie_name, cookie)

    assert anonymous.get("/me").status_code == 401


@pytest.mark.parametrize("edit", ["become someone else", "live forever", "drop the signature"])
def test_a_session_cannot_be_edited(api_settings, owner, edit) -> None:
    # The signature is the only thing standing between a readable token and one
    # that can be rewritten, so the three ways of trying are worth stating.
    carrier = Response()
    token = issue_session(carrier, owner.id, api_settings)
    header, payload, signature = token.split(".")
    claims_in_token = json.loads(_b64_decode(payload))

    if edit == "become someone else":
        claims_in_token["sub"] = str(uuid.uuid4())
    elif edit == "live forever":
        claims_in_token["exp"] += 10**9
    else:
        # "alg": "none" — the classic attempt at persuading a verifier not to.
        header, signature = _b64_encode(b'{"alg":"none"}'), ""

    edited = f"{header}.{_b64_encode(json.dumps(claims_in_token).encode())}.{signature}"

    assert read_session(edited, api_settings) is None


def _b64_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_a_session_carries_an_identity_and_nothing_more(api_settings, owner) -> None:
    # Anyone can read a signed token, so what it says matters. Role in particular
    # is absent by design: it is read from the database, which is what lets a
    # demotion take effect before the cookie expires.
    token = issue_session(Response(), owner.id, api_settings)
    payload = json.loads(_b64_decode(token.split(".")[1]))

    assert sorted(payload) == ["exp", "iat", "sub"]


def test_a_session_signed_with_another_secret_is_refused(
    api_app, api_settings, owner
) -> None:
    # A cookie from a previous deployment, or a forgery by someone who worked out
    # the format but not the key.
    stranger = api_settings.model_copy(
        update={"session_secret": "a-different-secret-of-perfectly-adequate-length"}
    )

    with TestClient(api_app) as client:
        sign_in(client, owner, stranger)
        assert client.get("/me").status_code == 401


def test_an_expired_session_is_refused(api_app, api_settings, owner) -> None:
    expired = api_settings.model_copy(update={"session_ttl_seconds": -60})

    with TestClient(api_app) as client:
        sign_in(client, owner, expired)
        assert client.get("/me").status_code == 401


def test_a_session_for_an_account_that_is_gone_is_refused(
    api_app, api_settings, session
) -> None:
    ghost = app_db.upsert_user(session, email=f"ghost@{DOMAIN}")
    ghost_id = ghost.id
    session.delete(ghost)
    session.flush()

    with TestClient(api_app) as client:
        client.cookies.set(
            api_settings.session_cookie_name, session_cookie(ghost_id, api_settings)
        )
        assert client.get("/me").status_code == 401


def test_deactivating_someone_takes_effect_on_their_next_request(
    as_member, session, owner
) -> None:
    # The reason the guard reads the database instead of trusting the cookie: a
    # week-long session must not outlive the decision to revoke it.
    assert as_member.get("/me").status_code == 200

    app_db.update_user(session, owner.id, is_active=False)

    response = as_member.get("/me")
    # 403, not 401: the credential is valid, the account is not, and a fresh
    # sign-in would only loop.
    assert response.status_code == 403
    assert "deactivated" in response.json()["detail"]


def test_a_promotion_takes_effect_on_the_next_request(as_member, session, owner) -> None:
    # Role is read from the database rather than carried in the token, so it does
    # not lag behind by the lifetime of a cookie.
    assert as_member.get("/me").json()["role"] == "member"

    app_db.update_user(session, owner.id, role=app_db.UserRole.ADMIN)

    assert as_member.get("/me").json()["role"] == "admin"


def test_signing_out_clears_the_session(api_app, api_settings) -> None:
    # Signed in through the callback rather than with a planted cookie, because
    # what is being tested is the browser's view of the cookie's whole life: set
    # by one response, dropped on the instruction of another.
    api_app.dependency_overrides[exchange_code_for_claims] = lambda: claims(
        f"analyst@{DOMAIN}"
    )

    with TestClient(api_app) as client:
        client.get("/auth/callback?code=c&state=s")
        assert client.get("/me").status_code == 200

        response = client.post("/auth/logout")

        assert response.status_code == 204
        assert client.cookies.get(api_settings.session_cookie_name) is None
        assert client.get("/me").status_code == 401


def test_signing_out_without_a_session_is_still_allowed(anonymous) -> None:
    # Someone holding a cookie we can no longer read still needs a way to be rid
    # of it, and that cannot require us to read it first.
    assert anonymous.post("/auth/logout").status_code == 204


# --- The development shortcut ------------------------------------------------


def test_the_development_sign_in_does_not_exist_by_default(anonymous) -> None:
    # 404 rather than 403: a route that is off should not confirm it is there.
    response = anonymous.post("/auth/dev-login", json={"email": f"ana@{DOMAIN}"})

    assert response.status_code == 404


def test_the_development_sign_in_issues_a_real_session_when_enabled(
    api_app, with_settings
) -> None:
    with_settings(dev_login_enabled=True)

    with TestClient(api_app) as client:
        response = client.post("/auth/dev-login", json={"email": f"ana@{DOMAIN}"})

        assert response.status_code == 200
        # The cookie it hands back is the same one Google's callback would issue.
        assert client.get("/me").json()["email"] == f"ana@{DOMAIN}"


def test_the_development_sign_in_still_obeys_the_domain_rule(api_app, with_settings) -> None:
    # A shortcut past Google, not past the access control.
    with_settings(dev_login_enabled=True)

    with TestClient(api_app) as client:
        response = client.post("/auth/dev-login", json={"email": "outsider@gmail.com"})

    assert response.status_code == 403


# --- Configuration that must not be possible ---------------------------------


def test_the_development_sign_in_cannot_be_switched_on_in_production() -> None:
    # Enforced by refusing to load the configuration at all: a route that mints
    # sessions for arbitrary addresses must not be one env var away from being
    # reachable in production.
    with pytest.raises(ValueError, match="DEV_LOGIN_ENABLED"):
        ApiSettings(
            environment="production",
            dev_login_enabled=True,
            session_secret="a-production-secret-of-adequate-length-here",
            allowed_email_domain=DOMAIN,
        )


def test_production_refuses_to_start_without_a_signing_secret() -> None:
    # A committed default would be a published private key: anyone could forge a
    # session for any address.
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        ApiSettings(environment="production", session_secret="", allowed_email_domain=DOMAIN)


def test_production_refuses_to_start_without_a_domain_to_restrict_to() -> None:
    with pytest.raises(ValueError, match="ALLOWED_EMAIL_DOMAIN"):
        ApiSettings(
            environment="production",
            session_secret="a-production-secret-of-adequate-length-here",
            allowed_email_domain="",
        )


def test_a_signing_secret_too_short_to_be_worth_having_is_refused() -> None:
    with pytest.raises(ValueError, match="at least"):
        ApiSettings(environment="test", session_secret="short")


def test_development_generates_a_secret_rather_than_shipping_one() -> None:
    generated = ApiSettings(environment="development", session_secret="").session_secret
    again = ApiSettings(environment="development", session_secret="").session_secret

    assert len(generated) >= 32
    # Different every time, so an unset secret costs a re-login after a restart
    # and never a forgeable session.
    assert generated != again
