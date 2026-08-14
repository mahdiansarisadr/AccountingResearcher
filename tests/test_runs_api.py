"""The HTTP surface of a run: accept it, report it, stream it, stop it.

Every route here requires a session, and every lookup is scoped to the person who
made the request — so these tests also cover what one user can learn about
another's runs, which is nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import app_db
import pytest
import run_bus
from accounting_research.agent.events import Done, RunStarted, Token
from api.deps import get_queue, get_redis
from fastapi.testclient import TestClient

from .conftest import sign_in


class FakeQueue:
    """Records what was enqueued instead of handing it to a worker.

    Every attempt is recorded, including a failed one, so a test can still tell
    which run the API was trying to queue when the queue was down.
    """

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.jobs: list[dict[str, Any]] = []

    def enqueue(self, function: str, *args: Any, **kwargs: Any) -> Any:
        self.jobs.append({"function": function, "args": args, "kwargs": kwargs})
        if self.error is not None:
            raise self.error
        return object()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def client(api_app, api_settings, owner, redis, queue, monkeypatch) -> Iterator[TestClient]:
    """A signed-in client, with Redis and the queue replaced.

    The Redis stand-in is installed twice over: through dependency_overrides for
    the handlers that receive it as an argument, and by patching the name the
    streaming path calls directly — that one cannot use a dependency, because
    FastAPI holds dependencies open until the response ends and a stream would
    pin a connection for the length of an entire run.
    """
    api_app.dependency_overrides[get_redis] = lambda: redis
    api_app.dependency_overrides[get_queue] = lambda: queue
    monkeypatch.setattr("api.routers.runs.get_redis", lambda: redis)

    with TestClient(api_app) as test_client:
        sign_in(test_client, owner, api_settings)
        yield test_client


def start(client: TestClient, message: str = "How much did Finance spend?", **body: Any):
    return client.post("/runs", json={"message": message, **body})


# --- Who may ask -------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/runs"),
        ("get", "/runs/{run_id}"),
        ("post", "/runs/{run_id}/cancel"),
        ("get", "/runs/{run_id}/stream"),
    ],
)
def test_every_run_route_refuses_a_request_without_a_session(
    anonymous, queued_run, run_id, method, path
) -> None:
    # Parametrised rather than written out, so a route added later without a
    # guard is caught by adding one line here instead of being overlooked.
    queued_run(run_id)

    response = anonymous.request(
        method, path.format(run_id=run_id), json={"message": "a question"}
    )

    assert response.status_code == 401


def test_a_forged_session_is_refused(anonymous, api_settings) -> None:
    anonymous.cookies.set(api_settings.session_cookie_name, "not.a.token")

    assert anonymous.get(f"/runs/{uuid.uuid4()}").status_code == 401


def test_a_run_is_invisible_to_everyone_but_its_owner(
    api_app, api_settings, other_user, queued_run, run_id
) -> None:
    queued_run(run_id)

    with TestClient(api_app) as intruder:
        sign_in(intruder, other_user, api_settings)
        # 404 rather than 403: confirming the id exists but belongs to someone
        # else is itself a disclosure.
        assert intruder.get(f"/runs/{run_id}").status_code == 404
        assert intruder.post(f"/runs/{run_id}/cancel").status_code == 404
        assert intruder.get(f"/runs/{run_id}/stream").status_code == 404


def test_a_run_records_who_asked(client, session, owner) -> None:
    run_id = uuid.UUID(start(client).json()["run_id"])

    assert app_db.get_run(session, run_id).user_id == owner.id


# --- Accepting a run ---------------------------------------------------------


def test_starting_a_run_accepts_it_and_reports_it_as_queued(client, queue) -> None:
    response = start(client)

    # 202, not 200: the work has been accepted, not completed.
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["created_at"] is not None
    assert body["started_at"] is None
    assert body["finished_at"] is None
    assert uuid.UUID(body["run_id"])
    assert len(queue.jobs) == 1


def test_the_queued_job_carries_the_question_and_the_conversation(client, queue) -> None:
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    response = start(client, "and the year before?", history=history)

    job = queue.jobs[0]
    assert job["function"] == run_bus.JOB_FUNCTION
    assert job["args"] == (response.json()["run_id"], "and the year before?", history)
    # A wedged run must not occupy the worker for good.
    assert job["kwargs"]["job_timeout"] > 0


def test_a_run_that_cannot_be_queued_is_settled_rather_than_left_pending(
    client, session
) -> None:
    # The row is committed before the job is enqueued, so a failure here would
    # otherwise leave a run sitting at "queued" that nothing will ever execute.
    broken = FakeQueue(error=ConnectionError("redis is down"))
    client.app.dependency_overrides[get_queue] = lambda: broken

    response = start(client)

    assert response.status_code == 503
    attempted = uuid.UUID(broken.jobs[0]["args"][0])
    session.expire_all()
    settled = app_db.get_run(session, attempted)
    assert settled.status is app_db.RunStatus.FAILED
    assert "could not queue run" in settled.error


def test_an_empty_question_is_rejected(client) -> None:
    assert start(client, "").status_code == 422


# --- Reporting a run ---------------------------------------------------------


def test_a_run_can_be_asked_about_after_it_is_started(client) -> None:
    run_id = start(client).json()["run_id"]

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id


def test_a_finished_run_reports_its_outcome_and_reason(
    client, session, queued_run, run_id
) -> None:
    # Read from Postgres, so this answer survives a restart and outlives the hour
    # the event log spends in Redis.
    queued_run(run_id)
    app_db.mark_finished(session, run_id, app_db.RunStatus.FAILED, "RuntimeError: boom")

    body = client.get(f"/runs/{run_id}").json()

    assert body["status"] == "failed"
    assert body["error"] == "RuntimeError: boom"
    assert body["finished_at"] is not None


def test_an_unknown_run_is_not_found(client) -> None:
    assert client.get(f"/runs/{uuid.uuid4()}").status_code == 404


def test_something_that_is_not_a_run_id_is_rejected_as_malformed(client) -> None:
    # 422 rather than 404: the request is wrong, not the run missing.
    assert client.get("/runs/not-a-uuid").status_code == 422


# --- Stopping a run ----------------------------------------------------------


def test_cancelling_asks_the_run_to_stop(client, redis) -> None:
    run_id = start(client).json()["run_id"]

    response = client.post(f"/runs/{run_id}/cancel")

    assert response.status_code == 202
    assert run_bus.cancel_requested(redis, run_id) is True
    # Still queued in the response: cancellation is cooperative, so 202 says the
    # request was accepted, not that it has taken effect.
    assert response.json()["status"] == "queued"


def test_cancelling_a_finished_run_is_a_conflict(
    client, session, redis, queued_run, run_id
) -> None:
    queued_run(run_id)
    app_db.mark_finished(session, run_id, app_db.RunStatus.SUCCEEDED)

    response = client.post(f"/runs/{run_id}/cancel")

    assert response.status_code == 409
    assert "succeeded" in response.json()["detail"]
    assert run_bus.cancel_requested(redis, str(run_id)) is False


def test_cancelling_an_unknown_run_is_not_found(client) -> None:
    assert client.post(f"/runs/{uuid.uuid4()}/cancel").status_code == 404


# --- Streaming a run ---------------------------------------------------------


def test_streaming_an_unknown_run_is_not_found(client) -> None:
    assert client.get(f"/runs/{uuid.uuid4()}/stream").status_code == 404


def test_streaming_replays_the_events_of_a_run(client, redis) -> None:
    run_id = start(client).json()["run_id"]
    for event in (
        RunStarted(seq=0, run_id=run_id),
        Token(seq=1, text="The total"),
        Done(seq=2, run_id=run_id, status="succeeded"),
    ):
        run_bus.publish(redis, run_id, event)

    response = client.get(f"/runs/{run_id}/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: run_started" in body
    assert "event: token" in body
    assert body.rstrip().endswith('"status":"succeeded"}')


def test_streaming_a_run_whose_events_expired_answers_from_the_record(
    client, session, queued_run, run_id
) -> None:
    # Redis forgets a run an hour after it ends. Without this the connection
    # would be held open until the idle timeout only to conclude the same thing.
    queued_run(run_id)
    app_db.mark_finished(session, run_id, app_db.RunStatus.CANCELLED)

    body = client.get(f"/runs/{run_id}/stream").text

    assert body.count("event: done") == 1
    assert '"status":"cancelled"' in body
