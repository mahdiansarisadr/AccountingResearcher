"""The user table: who exists, what they may do, and what a returning user updates.

Against real Postgres, so the unique constraint on email and the CHECK on role
are part of what is asserted rather than assumed.
"""

from __future__ import annotations

import uuid

import app_db
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .conftest import DOMAIN


def test_a_first_sign_in_creates_an_ordinary_member(session) -> None:
    user = app_db.upsert_user(session, email=f"new.person@{DOMAIN}", name="New Person")

    # Member, not admin: access is granted by the domain check, and authority is
    # granted deliberately by someone who already has it.
    assert user.role is app_db.UserRole.MEMBER
    assert user.is_active is True
    assert user.created_at is not None
    # Not a login yet — that is stamped only once access has been granted.
    assert user.last_login_at is None


def test_the_configured_first_admin_is_created_as_one(session) -> None:
    user = app_db.upsert_user(session, email=f"founder@{DOMAIN}", initial_admin=True)

    assert user.role is app_db.UserRole.ADMIN


def test_the_configured_first_admin_cannot_be_locked_out(session) -> None:
    # An instance whose only admin was demoted or deactivated would otherwise have
    # no way back in. Restoring it on sign-in is the break-glass.
    app_db.upsert_user(session, email=f"founder@{DOMAIN}", initial_admin=True)
    app_db.update_user(
        session,
        app_db.get_by_email(session, f"founder@{DOMAIN}").id,
        role=app_db.UserRole.MEMBER,
        is_active=False,
    )

    restored = app_db.upsert_user(session, email=f"founder@{DOMAIN}", initial_admin=True)

    assert restored.role is app_db.UserRole.ADMIN
    assert restored.is_active is True


def test_signing_in_again_finds_the_same_person(session) -> None:
    first = app_db.upsert_user(session, email=f"repeat@{DOMAIN}")
    again = app_db.upsert_user(session, email=f"repeat@{DOMAIN}")

    assert again.id == first.id


def test_an_address_is_the_same_address_whatever_its_case(session) -> None:
    # Google treats the local part as case-insensitive, so storing what was typed
    # would let one person become two rows — and two sets of runs.
    first = app_db.upsert_user(session, email=f"Casey.Case@{DOMAIN}")
    again = app_db.upsert_user(session, email=f"casey.case@{DOMAIN.upper()}  ")

    assert again.id == first.id
    assert first.email == f"casey.case@{DOMAIN}"


def test_a_returning_user_takes_googles_current_profile(session) -> None:
    app_db.upsert_user(session, email=f"renamed@{DOMAIN}", name="Old Name")

    updated = app_db.upsert_user(
        session, email=f"renamed@{DOMAIN}", name="New Name", avatar_url="https://x/y.png"
    )

    assert updated.name == "New Name"
    assert updated.avatar_url == "https://x/y.png"


def test_a_claim_that_is_missing_does_not_erase_what_we_know(session) -> None:
    app_db.upsert_user(session, email=f"sparse@{DOMAIN}", name="Known Name")

    unchanged = app_db.upsert_user(session, email=f"sparse@{DOMAIN}")

    assert unchanged.name == "Known Name"


def test_an_existing_member_is_not_promoted_by_signing_in_again(session) -> None:
    app_db.upsert_user(session, email=f"member@{DOMAIN}")

    again = app_db.upsert_user(session, email=f"member@{DOMAIN}")

    assert again.role is app_db.UserRole.MEMBER


def test_a_login_is_stamped_when_access_is_granted(session) -> None:
    user = app_db.upsert_user(session, email=f"stamped@{DOMAIN}")

    app_db.record_login(session, user)

    assert user.last_login_at is not None


def test_users_are_listed_in_a_stable_order(session) -> None:
    for local in ("carol", "alice", "bob"):
        app_db.upsert_user(session, email=f"{local}@{DOMAIN}")

    emails = [user.email for user in app_db.list_users(session)]

    assert emails == sorted(emails)


def test_a_role_can_be_changed(session) -> None:
    user = app_db.upsert_user(session, email=f"promoted@{DOMAIN}")

    updated = app_db.update_user(session, user.id, role=app_db.UserRole.ADMIN)

    assert updated.role is app_db.UserRole.ADMIN
    assert updated.is_active is True


def test_access_can_be_revoked_without_touching_the_role(session) -> None:
    user = app_db.upsert_user(session, email=f"revoked@{DOMAIN}")
    app_db.update_user(session, user.id, role=app_db.UserRole.ADMIN)

    updated = app_db.update_user(session, user.id, is_active=False)

    assert updated.is_active is False
    assert updated.role is app_db.UserRole.ADMIN


def test_updating_someone_who_does_not_exist_reports_nothing(session) -> None:
    assert app_db.update_user(session, uuid.uuid4(), is_active=False) is None


def test_the_database_refuses_a_second_row_for_one_address(session) -> None:
    app_db.upsert_user(session, email=f"only.once@{DOMAIN}")
    session.flush()

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO app.users (id, email, role, is_active)"
                " VALUES (:id, :email, 'member', true)"
            ),
            {"id": uuid.uuid4(), "email": f"only.once@{DOMAIN}"},
        )
    session.rollback()


def test_the_database_refuses_a_role_it_does_not_recognise(session) -> None:
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO app.users (id, email, role, is_active)"
                " VALUES (:id, :email, 'superuser', true)"
            ),
            {"id": uuid.uuid4(), "email": f"invented@{DOMAIN}"},
        )
    session.rollback()
