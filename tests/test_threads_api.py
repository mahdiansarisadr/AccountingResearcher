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
        ("get", "/threads/{thread_id}/files"),
        ("post", "/threads/{thread_id}/files"),
        ("get", "/threads/{thread_id}/experiments"),
        ("get", "/threads/{thread_id}/progress"),
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
        assert intruder.get(f"/threads/{thread.id}/files").status_code == 404
        assert intruder.get(f"/threads/{thread.id}/experiments").status_code == 404
        assert intruder.get(f"/threads/{thread.id}/progress").status_code == 404
        assert (
            intruder.post(
                f"/threads/{thread.id}/files",
                files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")},
            ).status_code
            == 404
        )


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


def test_the_owner_can_upload_a_csv(as_member, thread, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MLENG_DATA_DIR", str(tmp_path))
    response = as_member.post(
        f"/threads/{thread.id}/files",
        files={"file": ("churn.csv", b"label,x\n0,1\n1,2\n", "text/csv")},
    )

    assert response.status_code == 201
    assert response.json()["name"] == "churn.csv"
    listed = as_member.get(f"/threads/{thread.id}/files").json()
    assert [item["name"] for item in listed] == ["churn.csv"]
    stored = tmp_path / "users" / str(thread.user_id) / "uploads" / str(thread.id) / "churn.csv"
    assert stored.is_file()


def test_two_users_uploading_the_same_name_stay_apart(
    api_app, api_settings, session, owner, other_user, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MLENG_DATA_DIR", str(tmp_path))
    mine = app_db.create_thread(session, owner.id)
    theirs = app_db.create_thread(session, other_user.id)

    with TestClient(api_app) as member:
        sign_in(member, owner, api_settings)
        uploaded = member.post(
            f"/threads/{mine.id}/files",
            files={"file": ("data.csv", b"a,b\n1,2\n", "text/csv")},
        )
        assert uploaded.status_code == 201

    with TestClient(api_app) as other:
        sign_in(other, other_user, api_settings)
        uploaded = other.post(
            f"/threads/{theirs.id}/files",
            files={"file": ("data.csv", b"x,y\n9,8\n", "text/csv")},
        )
        assert uploaded.status_code == 201
        listed = other.get(f"/threads/{theirs.id}/files").json()
        assert [item["name"] for item in listed] == ["data.csv"]
        assert other.get(f"/threads/{mine.id}/files").status_code == 404

    path_a = tmp_path / "users" / str(owner.id) / "uploads" / str(mine.id) / "data.csv"
    path_b = (
        tmp_path / "users" / str(other_user.id) / "uploads" / str(theirs.id) / "data.csv"
    )
    assert path_a.read_bytes() == b"a,b\n1,2\n"
    assert path_b.read_bytes() == b"x,y\n9,8\n"


def test_deleting_a_thread_removes_only_that_threads_uploads(
    as_member, session, owner, thread, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MLENG_DATA_DIR", str(tmp_path))
    other = app_db.create_thread(session, owner.id)
    as_member.post(
        f"/threads/{thread.id}/files",
        files={"file": ("gone.csv", b"a,b\n1,2\n", "text/csv")},
    )
    as_member.post(
        f"/threads/{other.id}/files",
        files={"file": ("kept.csv", b"x,y\n9,8\n", "text/csv")},
    )

    assert as_member.delete(f"/threads/{thread.id}").status_code == 204
    gone = tmp_path / "users" / str(owner.id) / "uploads" / str(thread.id)
    kept = tmp_path / "users" / str(owner.id) / "uploads" / str(other.id) / "kept.csv"
    assert not gone.exists()
    assert kept.is_file()


def test_experiments_are_empty_before_anyone_trains(
    as_member, thread, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MLENG_DATA_DIR", str(tmp_path))
    assert as_member.get(f"/threads/{thread.id}/experiments").json() == []


def test_experiments_list_the_threads_mlflow_runs(
    as_member, thread, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MLENG_DATA_DIR", str(tmp_path))
    from mleng.agent.tools.train import train_model_impl
    from mleng.core.workspace import reset_run_context, save_upload, set_run_context

    rows = ["x,z,y"]
    for i in range(1, 11):
        rows.append(f"{i},{i + 1},{i * 2.0}")
    save_upload(
        str(thread.user_id),
        str(thread.id),
        "data.csv",
        ("\n".join(rows) + "\n").encode(),
        data_dir=tmp_path,
    )
    token = set_run_context(str(thread.user_id), str(thread.id), data_dir=tmp_path)
    try:
        raw = train_model_impl(
            "y",
            filename="data.csv",
            model="ridge",
            task="regression",
            hypothesis="baseline ridge",
        )
    finally:
        reset_run_context(token)
    assert not raw.startswith("ERROR:")

    listed = as_member.get(f"/threads/{thread.id}/experiments").json()
    assert len(listed) == 1
    assert listed[0]["model"] == "ridge"
    assert listed[0]["primary_metric"] == "r2"
    assert listed[0]["hypothesis"] == "baseline ridge"
    assert "mae" in listed[0]["metrics"]
    assert "rmse" in listed[0]["metrics"]
    assert listed[0]["recipe_version"] == 1
    assert listed[0]["recipe_kind"] == "default"
    assert listed[0]["reused"] is False


def test_experiments_report_the_version_each_run_executed(
    as_member, thread, tmp_path, monkeypatch
) -> None:
    """The sidebar groups by version, so a re-run must not look like a new idea."""
    monkeypatch.setenv("MLENG_DATA_DIR", str(tmp_path))
    from mleng.agent.tools.train import train_model_impl
    from mleng.core.workspace import reset_run_context, save_upload, set_run_context

    rows = ["x,z,y"]
    for i in range(1, 13):
        rows.append(f"{i},{i + 1},{i * 2.0}")
    save_upload(
        str(thread.user_id),
        str(thread.id),
        "data.csv",
        ("\n".join(rows) + "\n").encode(),
        data_dir=tmp_path,
    )
    token = set_run_context(str(thread.user_id), str(thread.id), data_dir=tmp_path)
    try:
        train_model_impl("y", filename="data.csv", model="ridge", task="regression")
        train_model_impl("y", filename="data.csv", recipe_version=1, split_seed=7)
        train_model_impl(
            "y",
            filename="data.csv",
            model="random_forest",
            task="regression",
            parent_version=1,
        )
    finally:
        reset_run_context(token)

    listed = as_member.get(f"/threads/{thread.id}/experiments").json()
    versions = sorted(row["recipe_version"] for row in listed)

    assert versions == [1, 1, 2]
    reran = [row for row in listed if row["recipe_version"] == 1 and row["reused"]]
    assert len(reran) == 1
    assert reran[0]["split_seed"] == "7"
    forked = next(row for row in listed if row["recipe_version"] == 2)
    assert forked["recipe_parent"] == 1


def test_progress_traces_the_search_in_the_order_it_happened(
    as_member, thread, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MLENG_DATA_DIR", str(tmp_path))
    from mleng.agent.tools.train import train_model_impl
    from mleng.core.workspace import reset_run_context, save_upload, set_run_context

    empty = as_member.get(f"/threads/{thread.id}/progress").json()
    assert empty["steps"] == []
    assert empty["improved"] is False

    rows = ["x,z,y"]
    for i in range(1, 17):
        rows.append(f"{i},{i + 1},{i * 2.0}")
    save_upload(
        str(thread.user_id),
        str(thread.id),
        "data.csv",
        ("\n".join(rows) + "\n").encode(),
        data_dir=tmp_path,
    )
    token = set_run_context(str(thread.user_id), str(thread.id), data_dir=tmp_path)
    try:
        train_model_impl("y", filename="data.csv", model="ridge", task="regression")
        train_model_impl(
            "y", filename="data.csv", model="random_forest", task="regression"
        )
    finally:
        reset_run_context(token)

    body = as_member.get(f"/threads/{thread.id}/progress").json()

    assert body["metric"] == "r2"
    assert body["runs"] == 2
    assert body["versions"] == 2
    assert [step["order"] for step in body["steps"]] == [1, 2]
    assert [step["version"] for step in body["steps"]] == [1, 2]
    curve = [step["best_so_far"] for step in body["steps"]]
    assert curve[0] <= curve[1]
    assert body["best"] == curve[-1]
    assert body["best_version"] in (1, 2)
