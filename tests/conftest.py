"""Shared fixtures.

Two classes of dependency are handled differently. Redis is replaced with an
in-process fake, because nothing about the run bus depends on it being a real
server. Postgres is used for real: the schema carries a CHECK constraint and
server-side timestamps, and a substitute that did not enforce them would let
exactly the bugs these tests exist to catch through. Tests that need it skip
themselves when it is unreachable, so the suite still runs without Docker.

Google is never contacted. Sign-in is exercised by handing the code the claims
Google would have returned, which is the whole of what it decides on.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import app_db
import fakeredis
import pytest
from api.deps import get_session, get_session_factory
from api.main import create_app
from api.security import issue_session
from api.settings import ApiSettings, get_api_settings
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

# The company domain, for tests. Deliberately not a real one, so a test that
# reaches the network fails instead of succeeding quietly.
DOMAIN = "example-firm.test"

# Long enough to satisfy the minimum the settings enforce.
SECRET = "test-signing-secret-not-used-anywhere-real"

# A complete production configuration. Tests that assert a *missing* field still
# construct ApiSettings themselves; this is the baseline that should boot.
PRODUCTION = {
    "environment": "production",
    "session_secret": "a-production-secret-of-adequate-length-here",
    "allowed_email_domain": DOMAIN,
    "google_client_id": "prod-client-id",
    "google_client_secret": "prod-client-secret",
    "oauth_redirect_url": "https://research.example.test/auth/callback",
    "post_login_redirect": "https://research.example.test",
    "cors_origins": "https://research.example.test",
    "public_host": "research.example.test",
    "dev_login_enabled": False,
}


def production_settings(**overrides) -> ApiSettings:
    return ApiSettings(**{**PRODUCTION, **overrides})


@pytest.fixture
def redis() -> Iterator[fakeredis.FakeRedis]:
    """An in-process Redis, streams included, which the run bus is built on."""
    client = fakeredis.FakeRedis()
    yield client
    client.close()


@pytest.fixture(scope="session")
def engine() -> Engine:
    """An engine against the development database, or a skip if there is none."""
    from api.settings import get_api_settings

    url = get_api_settings().database_url
    candidate = app_db.get_engine(url)
    try:
        with candidate.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"Postgres is not reachable at {url}: {exc}")
    return candidate


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back, so tests leave no rows behind.

    Bound to an already-open transaction rather than to the engine, and told to
    turn its own commits into savepoints. That is what lets the code under test
    commit for real — as the API must, before it enqueues — while everything
    still disappears at the end of the test.
    """
    connection = engine.connect()
    transaction = connection.begin()
    scoped = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield scoped
    finally:
        scoped.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def run_id() -> uuid.UUID:
    return uuid.uuid4()


# --- Authentication ----------------------------------------------------------


@pytest.fixture
def api_settings() -> ApiSettings:
    """Settings with sign-in configured, independent of the developer's .env.

    Every field the auth code reads is passed explicitly. Constructor arguments
    outrank the environment in pydantic-settings, so a local ``.env`` cannot
    change what these tests are asserting — including switching on the
    development sign-in that several of them expect to be off.
    """
    return ApiSettings(
        environment="test",
        allowed_email_domain=DOMAIN,
        initial_admin_email=f"founder@{DOMAIN}",
        google_client_id="test-client-id",
        google_client_secret="test-client-secret",
        session_secret=SECRET,
        dev_login_enabled=False,
        # Tests follow the post-login redirect on the API itself; the real
        # default points at the frontend.
        post_login_redirect="/me",
        # The limiter talks to Redis. Off here so the rest of the suite does not
        # need a stand-in on every request; tests for the limiter turn it on.
        rate_limit_requests=0,
    )


@pytest.fixture
def owner(session: Session) -> app_db.User:
    """An ordinary member, and the owner of runs that tests create."""
    return app_db.upsert_user(session, email=f"analyst@{DOMAIN}", name="Ana Lyst")


@pytest.fixture
def other_user(session: Session) -> app_db.User:
    """A second member, for checking that one person cannot see another's work."""
    return app_db.upsert_user(session, email=f"someone.else@{DOMAIN}", name="Sam Else")


@pytest.fixture
def admin(session: Session) -> app_db.User:
    return app_db.upsert_user(
        session, email=f"founder@{DOMAIN}", name="Ada Min", initial_admin=True
    )


@pytest.fixture
def thread(session: Session, owner: app_db.User) -> app_db.Thread:
    """A conversation belonging to ``owner``, which runs in tests attach to."""
    return app_db.create_thread(session, owner.id)


@pytest.fixture
def queued_run(session: Session, owner: app_db.User, thread: app_db.Thread):
    """Record a queued run belonging to ``owner`` on ``thread``.

    Runs need an owner and a thread now, and in most tests which ones are beside
    the point.
    """

    def record(run_id: uuid.UUID) -> app_db.Run:
        return app_db.create_run(session, run_id, owner.id, thread.id)

    return record


@pytest.fixture
def api_app(session: Session, api_settings: ApiSettings) -> FastAPI:
    """The application with its database and settings replaced.

    Nothing about authentication is stubbed: requests carry a real signed cookie
    and the guard verifies it and loads the user for real. Only the edges — the
    database transaction and the configuration — are swapped.
    """
    app = create_app(api_settings)
    app.dependency_overrides[get_session] = lambda: session
    # The streaming path opens and closes its own transaction; hand it the test
    # session without letting it be closed at the end of the request.
    app.dependency_overrides[get_session_factory] = lambda: lambda: nullcontext(session)
    app.dependency_overrides[get_api_settings] = lambda: api_settings
    return app


def session_cookie(user_id: uuid.UUID, settings: ApiSettings) -> str:
    """The cookie value the API would hand this user.

    Produced by the code that issues it for real, rather than assembled here, so
    a test cannot pass against a token the application would refuse.
    """
    carrier = Response()
    issue_session(carrier, user_id, settings)
    return carrier.headers["set-cookie"].split("=", 1)[1].split(";")[0]


def sign_in(client: TestClient, user: app_db.User, settings: ApiSettings) -> None:
    """Put a valid session on the client, skipping the sign-in itself.

    The cookie is placed straight into the jar rather than obtained from a
    response, which is enough for any test that needs to *be* someone. A test
    about the cookie's own lifecycle should sign in through the callback instead:
    a cookie the client was never handed is not one it will let the server
    delete.
    """
    client.cookies.set(settings.session_cookie_name, session_cookie(user.id, settings))


@pytest.fixture
def anonymous(api_app: FastAPI) -> Iterator[TestClient]:
    """A client with no session, for checking that routes are actually closed."""
    with TestClient(api_app) as client:
        yield client


@pytest.fixture
def as_member(
    api_app: FastAPI, api_settings: ApiSettings, owner: app_db.User
) -> Iterator[TestClient]:
    with TestClient(api_app) as client:
        sign_in(client, owner, api_settings)
        yield client


@pytest.fixture
def as_admin(
    api_app: FastAPI, api_settings: ApiSettings, admin: app_db.User
) -> Iterator[TestClient]:
    with TestClient(api_app) as client:
        sign_in(client, admin, api_settings)
        yield client
