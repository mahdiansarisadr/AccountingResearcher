"""The job the worker executes: the seam between the queue, the agent and the record.

This is where Phase 1 is either finished or not: every path out of a run has to
leave a client with a terminating event and leave the database with a settled row.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any

import app_db
import pytest
import run_bus
from worker import tasks

from .doubles import ExplodingAgent, FakeAgent, make_answer, state_chunk, token_chunk


@pytest.fixture
def wired(session, redis, monkeypatch):
    """Point the job at the in-process Redis and the test's transaction."""
    monkeypatch.setattr(tasks, "Redis", SimpleNamespace(from_url=lambda _url: redis))
    monkeypatch.setattr(app_db, "session_scope", lambda _url: nullcontext(session))
    return monkeypatch


@pytest.fixture
def execute(wired):
    """Run the job against a given agent, so each test scripts one behaviour."""

    def run(agent: Any, run_id, message: str = "How much did Finance spend?", **kwargs):
        wired.setattr(tasks, "_get_agent", lambda: agent)
        return tasks.execute_run(str(run_id), message, **kwargs)

    return run


def published(redis, run_id) -> list[Any]:
    return [
        event
        for _, event in run_bus.read_events(
            redis, str(run_id), block_ms=10, idle_timeout_s=0.05
        )
    ]


def reload(session, run_id) -> app_db.Run:
    session.expire_all()
    return app_db.get_run(session, run_id)


def test_a_successful_run_is_recorded_and_streamed(
    execute, session, redis, queued_run, run_id
) -> None:
    queued_run(run_id)
    agent = FakeAgent([token_chunk('{"answer": "It was $5'), state_chunk(structured=make_answer())])

    assert execute(agent, run_id) == "succeeded"

    run = reload(session, run_id)
    assert run.status is app_db.RunStatus.SUCCEEDED
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.error is None
    assert [event.type for event in published(redis, run_id)][-1] == "done"


def test_the_outcome_is_recorded_before_the_terminal_event_is_published(
    execute, session, redis, queued_run, run_id, monkeypatch
) -> None:
    # A client that sees "done" may ask about the run in the same breath, and
    # must not be told it is still running.
    queued_run(run_id)
    observed: list[app_db.RunStatus] = []
    original = run_bus.publish

    def spy(client, identifier, event):
        if event.type == "done":
            observed.append(reload(session, run_id).status)
        return original(client, identifier, event)

    monkeypatch.setattr(run_bus, "publish", spy)

    execute(FakeAgent([state_chunk(structured=make_answer())]), run_id)

    assert observed == [app_db.RunStatus.SUCCEEDED]


def test_a_failed_run_records_why_it_failed(
    execute, session, redis, queued_run, run_id
) -> None:
    # The runner reports failure as an error event followed by done; the record
    # should say why, not only that.
    queued_run(run_id)
    agent = FakeAgent([state_chunk()])  # finishes with no grounded answer

    assert execute(agent, run_id) == "failed"

    run = reload(session, run_id)
    assert run.status is app_db.RunStatus.FAILED
    assert "step budget" in run.error


def test_an_agent_that_breaks_mid_run_is_recorded_not_raised(
    execute, session, redis, queued_run, run_id
) -> None:
    # run_agent turns a failure inside the agent into an error event followed by
    # done, so the job completes normally and the reason reaches the record.
    queued_run(run_id)

    assert execute(ExplodingAgent(RuntimeError("model unavailable")), run_id) == "failed"

    run = reload(session, run_id)
    assert run.status is app_db.RunStatus.FAILED
    assert "model unavailable" in run.error
    assert [event.type for event in published(redis, run_id)][-1] == "done"


def test_a_failure_outside_the_agent_still_ends_the_stream(
    wired, session, redis, queued_run, run_id
) -> None:
    # Everything run_agent does not catch: a broken agent build, a Redis error.
    # The stream still has to end, and the queue still has to see a failed job.
    queued_run(run_id)

    def broken_build() -> Any:
        raise RuntimeError("no API key configured")

    wired.setattr(tasks, "_get_agent", broken_build)

    with pytest.raises(RuntimeError):
        tasks.execute_run(str(run_id), "How much did Finance spend?")

    run = reload(session, run_id)
    assert run.status is app_db.RunStatus.FAILED
    assert "no API key configured" in run.error
    types = [event.type for event in published(redis, run_id)]
    assert types == ["error", "done"]


def test_a_run_cancelled_while_queued_is_never_executed(
    execute, session, redis, queued_run, run_id
) -> None:
    queued_run(run_id)
    run_bus.request_cancel(redis, str(run_id))
    agent = FakeAgent([state_chunk(structured=make_answer())])

    assert execute(agent, run_id) == "cancelled"

    assert agent.calls == []
    assert reload(session, run_id).status is app_db.RunStatus.CANCELLED
    # A client may already be streaming it, so it still needs an ending.
    events = published(redis, run_id)
    assert [event.type for event in events] == ["done"]
    assert events[0].status == "cancelled"


def test_a_run_cancelled_midway_stops_and_is_recorded_as_cancelled(
    execute, session, redis, queued_run, run_id
) -> None:
    queued_run(run_id)

    class CancellingAgent:
        """Asks for its own cancellation once output has started."""

        def stream(self, _payload: dict[str, Any], **_kwargs: Any) -> Iterator[Any]:
            yield token_chunk('{"answer": "star')
            run_bus.request_cancel(redis, str(run_id))
            yield state_chunk(structured=make_answer())

    assert execute(CancellingAgent(), run_id) == "cancelled"

    run = reload(session, run_id)
    assert run.status is app_db.RunStatus.CANCELLED
    # Cancelled is not a failure: nothing went wrong.
    assert run.error is None
    assert published(redis, run_id)[-1].status == "cancelled"


def test_the_conversation_is_handed_to_the_agent(
    execute, session, queued_run, run_id
) -> None:
    queued_run(run_id)
    history = [{"role": "user", "content": "earlier"}]
    agent = FakeAgent([state_chunk(structured=make_answer())])

    execute(agent, run_id, "and now?", history=history)

    messages = agent.calls[0]["payload"]["messages"]
    assert messages[0] == history[0]
    assert messages[-1] == {"role": "user", "content": "and now?"}
