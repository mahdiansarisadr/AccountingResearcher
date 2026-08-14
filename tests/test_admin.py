"""User management: who may do it, and the one thing an admin may not do to themselves."""

from __future__ import annotations

import uuid

import app_db
import pytest

from .conftest import DOMAIN


def test_an_admin_can_see_everyone(as_admin, owner, other_user) -> None:
    body = as_admin.get("/admin/users").json()

    emails = {user["email"] for user in body}
    assert {owner.email, other_user.email} <= emails


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/admin/users"), ("patch", f"/admin/users/{uuid.uuid4()}")],
)
def test_a_member_cannot_reach_user_management(as_member, method, path) -> None:
    response = as_member.request(method, path, json={"is_active": False})

    # 403, not 404: they are signed in and this is a real route. It is simply not
    # theirs.
    assert response.status_code == 403
    assert "admin" in response.json()["detail"]


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/admin/users"), ("patch", f"/admin/users/{uuid.uuid4()}")],
)
def test_user_management_is_closed_to_strangers(anonymous, method, path) -> None:
    response = anonymous.request(method, path, json={"is_active": False})

    assert response.status_code == 401


def test_an_admin_can_promote_someone(as_admin, session, owner) -> None:
    response = as_admin.patch(f"/admin/users/{owner.id}", json={"role": "admin"})

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    session.expire_all()
    assert app_db.get_by_id(session, owner.id).role is app_db.UserRole.ADMIN


def test_an_admin_can_revoke_access(as_admin, session, owner) -> None:
    response = as_admin.patch(f"/admin/users/{owner.id}", json={"is_active": False})

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_a_revoked_account_is_locked_out_at_once(
    as_admin, as_member, session, owner
) -> None:
    # The point of the whole feature: a member with a valid week-long cookie stops
    # being able to use the tool on their next request, not on their next sign-in.
    assert as_member.get("/me").status_code == 200

    as_admin.patch(f"/admin/users/{owner.id}", json={"is_active": False})

    assert as_member.get("/me").status_code == 403
    assert as_member.post("/threads", json={}).status_code == 403


def test_a_role_change_does_not_disturb_access(as_admin, owner) -> None:
    # Both fields are optional and only what is sent is applied, so changing one
    # cannot reset the other by omission.
    response = as_admin.patch(f"/admin/users/{owner.id}", json={"role": "admin"})

    assert response.json()["is_active"] is True


def test_an_admin_cannot_demote_themselves(as_admin, admin) -> None:
    # How an instance ends up with no working administrator. Another admin can
    # still do it, so this does not make anyone permanent.
    response = as_admin.patch(f"/admin/users/{admin.id}", json={"role": "member"})

    assert response.status_code == 409
    assert "their own" in response.json()["detail"]


def test_an_admin_cannot_lock_themselves_out(as_admin, admin) -> None:
    response = as_admin.patch(f"/admin/users/{admin.id}", json={"is_active": False})

    assert response.status_code == 409


def test_a_change_to_nobody_in_particular_is_not_found(as_admin) -> None:
    response = as_admin.patch(f"/admin/users/{uuid.uuid4()}", json={"role": "member"})

    assert response.status_code == 404


def test_a_request_that_changes_nothing_is_rejected(as_admin, owner) -> None:
    response = as_admin.patch(f"/admin/users/{owner.id}", json={})

    assert response.status_code == 422


def test_a_role_that_does_not_exist_is_rejected(as_admin, owner) -> None:
    response = as_admin.patch(f"/admin/users/{owner.id}", json={"role": "superuser"})

    assert response.status_code == 422


def test_the_listing_shows_what_a_user_sees_of_themselves(as_admin, session) -> None:
    # One response shape for /me and for this listing, so the two cannot drift.
    app_db.upsert_user(session, email=f"listed@{DOMAIN}", name="Listed Person")

    listed = next(
        user
        for user in as_admin.get("/admin/users").json()
        if user["email"] == f"listed@{DOMAIN}"
    )

    assert set(listed) == {
        "id",
        "email",
        "name",
        "avatar_url",
        "role",
        "is_active",
        "created_at",
        "last_login_at",
    }
