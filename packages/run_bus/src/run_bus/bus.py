"""Moving run events between the worker that produces them and the API that
serves them.

Implemented on a Redis Stream rather than pub/sub. Pub/sub delivers only to
whoever is listening at that instant, and a client necessarily connects *after*
starting a run, so early events would be lost on every request. A stream is an
append-only log: a late subscriber replays from the start, and a client whose
connection dropped resumes from the last id it saw.
"""

from __future__ import annotations

from collections.abc import Iterator

from accounting_research.agent.events import RunEvent, parse_event
from redis import Redis

from .keys import RUN_TTL_SECONDS, cancel_flag, events_stream, status_key

# Redis stream ids are opaque strings; "0" means "from the very beginning".
START = "0"

_FIELD = "event"


def publish(redis: Redis, run_id: str, event: RunEvent) -> str:
    """Append one event to the run's log. Returns its stream id."""
    key = events_stream(run_id)
    stream_id = redis.xadd(key, {_FIELD: event.model_dump_json()})
    # Refreshed on every write so the TTL counts from the end of the run.
    redis.expire(key, RUN_TTL_SECONDS)
    return stream_id.decode() if isinstance(stream_id, bytes) else str(stream_id)


def read_events(
    redis: Redis,
    run_id: str,
    *,
    last_id: str = START,
    block_ms: int = 1_000,
    idle_timeout_s: float = 120.0,
) -> Iterator[tuple[str, RunEvent]]:
    """Yield (stream_id, event) pairs, waiting for new ones as they arrive.

    Stops after the run's Done event, or once nothing has arrived for
    ``idle_timeout_s`` — which is the only protection against hanging forever on
    a run whose worker died mid-job.

    ``last_id`` lets a reconnecting client skip what it already received.
    """
    key = events_stream(run_id)
    cursor = last_id
    waited = 0.0

    while True:
        # BLOCK parks the connection in Redis instead of polling in a loop, so an
        # idle stream costs nothing and a new event is delivered immediately.
        response = redis.xread({key: cursor}, count=100, block=block_ms)

        if not response:
            waited += block_ms / 1000
            if waited >= idle_timeout_s:
                return
            continue

        waited = 0.0
        for _stream, entries in response:
            for stream_id, fields in entries:
                cursor = stream_id.decode() if isinstance(stream_id, bytes) else stream_id
                raw = fields.get(_FIELD.encode()) or fields.get(_FIELD)
                if raw is None:
                    continue
                event = parse_event(raw)
                yield cursor, event
                if event.type == "done":
                    return


def request_cancel(redis: Redis, run_id: str) -> None:
    """Ask a run to stop. Cooperative: the job notices between events."""
    redis.set(cancel_flag(run_id), "1", ex=RUN_TTL_SECONDS)


def cancel_requested(redis: Redis, run_id: str) -> bool:
    return bool(redis.exists(cancel_flag(run_id)))


def set_status(redis: Redis, run_id: str, status: str) -> None:
    redis.set(status_key(run_id), status, ex=RUN_TTL_SECONDS)


def get_status(redis: Redis, run_id: str) -> str | None:
    value = redis.get(status_key(run_id))
    if value is None:
        return None
    return value.decode() if isinstance(value, bytes) else str(value)
