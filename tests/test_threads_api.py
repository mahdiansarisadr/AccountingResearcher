"""The HTTP surface of a conversation: open it, list it, read it, delete it.

Every route here requires a session, and every lookup is scoped to the person who
made the request — so these tests also cover what one user can learn about
another's threads, which is nothing.
"""

from __future__ import annotations

import uuid

import app_db
import pytest
from fastapi.testclient import TestClient

from .conftest import sign_in


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/threads"),
        ("post", "/threads"),
        ("get", "/threads/{thread_id}"),
        ("delete", "/threads/{thread_id}"),
        ("get", "/threads/{thread_id}/messages"),
        ("post", "/threads/{thread_id}/runs"),
    ],
)
def test_every_thread_route_refuses_a_request_without_a_session(
    anonymous, thread, method, path
) -> None:
    response = anonymous.request(
        method,
        path.format(thread_id=thread.id),
        json={"message": "a question"},
    )

    assert response.status_code == 401


def test_creating_a_thread_returns_it(as_member) -> None:
    response = as_member.post("/threads", json={"title": "Q2 travel"})

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Q2 travel"
    assert uuid.UUID(body["id"])


def test_a_thread_without_a_title_gets_the_default(as_member) -> None:
    body = as_member.post("/threads", json={}).json()

    assert body["title"] == app_db.DEFAULT_TITLE


def test_listing_returns_only_this_users_threads(
    as_member, session, owner, other_user
) -> None:
    mine = app_db.create_thread(session, owner.id, title="mine")
    app_db.create_thread(session, other_user.id, title="theirs")

    body = as_member.get("/threads").json()

    assert [thread["id"] for thread in body] == [str(mine.id)]
    assert body[0]["title"] == "mine"


def test_a_thread_is_invisible_to_everyone_but_its_owner(
    api_app, api_settings, other_user, thread
) -> None:
    with TestClient(api_app) as intruder:
        sign_in(intruder, other_user, api_settings)
        assert intruder.get(f"/threads/{thread.id}").status_code == 404
        assert intruder.get(f"/threads/{thread.id}/messages").status_code == 404
        assert (
            intruder.post(
                f"/threads/{thread.id}/runs", json={"message": "anything"}
            ).status_code
            == 404
        )
        assert intruder.delete(f"/threads/{thread.id}").status_code == 404


def test_messages_survive_as_the_record_of_the_conversation(
    as_member, session, thread
) -> None:
    app_db.append_message(session, thread.id, app_db.MessageRole.USER, "How much?")
    app_db.append_message(
        session,
        thread.id,
        app_db.MessageRole.ASSISTANT,
        "42 dollars.",
        payload={"confidence": 0.9},
    )

    body = as_member.get(f"/threads/{thread.id}/messages").json()

    assert [message["role"] for message in body] == ["user", "assistant"]
    assert body[0]["content"] == "How much?"
    assert body[1]["payload"] == {"confidence": 0.9}


def test_deleting_a_thread_removes_it(as_member, thread) -> None:
    response = as_member.delete(f"/threads/{thread.id}")

    assert response.status_code == 204
    assert as_member.get(f"/threads/{thread.id}").status_code == 404
    assert as_member.get("/threads").json() == []


def test_starting_a_run_on_an_unknown_thread_is_not_found(as_member) -> None:
    response = as_member.post(
        f"/threads/{uuid.uuid4()}/runs", json={"message": "anything"}
    )

    assert response.status_code == 404
