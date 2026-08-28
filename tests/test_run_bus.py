"""The transport between the worker that produces events and the API that serves them."""

from __future__ import annotations

import run_bus
from mleng.agent.events import Done, RunStarted, Token, ToolCall


def drain(redis, run_id: str, **kwargs):
    """Read a run's events with timeouts short enough for a test."""
    options = {"block_ms": 10, "idle_timeout_s": 0.05, **kwargs}
    return list(run_bus.read_events(redis, run_id, **options))


def test_events_come_back_in_the_order_they_were_published(redis) -> None:
    published = [
        RunStarted(seq=0, run_id="r1"),
        ToolCall(seq=1, name="example_tool", args={"query": "spend"}),
        Token(seq=2, text="The"),
        Done(seq=3, run_id="r1", status="succeeded"),
    ]
    for event in published:
        run_bus.publish(redis, "r1", event)

    received = drain(redis, "r1")

    assert [event.type for _, event in received] == [e.type for e in published]
    assert [event.seq for _, event in received] == [0, 1, 2, 3]


def test_a_late_reader_still_gets_the_whole_run(redis) -> None:
    # The reason this is a stream and not pub/sub: a client necessarily connects
    # after starting a run, so with pub/sub the early events would be lost every
    # time.
    run_bus.publish(redis, "r1", RunStarted(run_id="r1"))
    run_bus.publish(redis, "r1", Done(run_id="r1", status="succeeded"))

    assert len(drain(redis, "r1")) == 2


def test_reading_stops_at_the_terminal_event(redis) -> None:
    run_bus.publish(redis, "r1", Done(run_id="r1", status="succeeded"))
    run_bus.publish(redis, "r1", Token(text="after the end"))

    received = drain(redis, "r1")

    assert len(received) == 1
    assert received[0][1].type == "done"


def test_a_reconnecting_client_resumes_instead_of_replaying(redis) -> None:
    run_bus.publish(redis, "r1", RunStarted(run_id="r1"))
    run_bus.publish(redis, "r1", Token(text="half"))
    run_bus.publish(redis, "r1", Done(run_id="r1", status="succeeded"))

    first, second = drain(redis, "r1")[:2]
    resumed = drain(redis, "r1", last_id=first[0])

    assert [event.type for _, event in resumed] == ["token", "done"]
    assert resumed[0][0] == second[0]


def test_reading_gives_up_when_nothing_arrives(redis) -> None:
    # The only protection against waiting forever on a run whose worker died.
    run_bus.publish(redis, "r1", RunStarted(run_id="r1"))

    received = drain(redis, "r1")

    assert [event.type for _, event in received] == ["run_started"]


def test_a_runs_log_is_distinguishable_from_one_that_expired(redis) -> None:
    assert run_bus.has_events(redis, "r1") is False

    run_bus.publish(redis, "r1", RunStarted(run_id="r1"))

    assert run_bus.has_events(redis, "r1") is True


def test_the_log_is_given_an_expiry_so_redis_is_not_an_archive(redis) -> None:
    run_bus.publish(redis, "r1", RunStarted(run_id="r1"))

    ttl = redis.ttl(run_bus.events_stream("r1"))

    assert 0 < ttl <= run_bus.RUN_TTL_SECONDS


def test_cancellation_is_a_flag_the_job_can_notice(redis) -> None:
    assert run_bus.cancel_requested(redis, "r1") is False

    run_bus.request_cancel(redis, "r1")

    assert run_bus.cancel_requested(redis, "r1") is True
    # Scoped to one run: cancelling does not stop anything else.
    assert run_bus.cancel_requested(redis, "r2") is False
