"""Redis naming conventions.

Every key and channel name lives here so the API (reader) and the worker
(writer) cannot disagree. A mistyped key name is otherwise invisible: nothing
errors, the reader just waits forever for events nobody is sending.
"""

from __future__ import annotations

# The RQ queue runs are submitted to.
QUEUE_NAME = "runs"

# Referenced as a string, not an import, so the API can enqueue work without
# depending on the worker package (and therefore on the agent's dependencies).
JOB_FUNCTION = "worker.tasks.execute_run"

# How long a finished run's event log stays readable. Long enough for a browser
# to reconnect and replay, short enough that Redis is not an archive — the
# durable record of a run is its row in app.runs.
RUN_TTL_SECONDS = 60 * 60


def events_stream(run_id: str) -> str:
    """Redis Stream holding every event of a run, in order."""
    return f"run:{run_id}:events"


def cancel_flag(run_id: str) -> str:
    """Set when someone asks for a run to stop; polled by the running job."""
    return f"run:{run_id}:cancel"
