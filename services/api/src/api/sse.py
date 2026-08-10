"""Server-Sent Events framing.

SSE is deliberately boring: a long-lived HTTP response of newline-delimited
records. Browsers reconnect on their own and replay the last id they saw, which
is why every record carries the Redis stream id.
"""

from __future__ import annotations

# Headers that stop intermediaries from defeating streaming. Without
# X-Accel-Buffering, an nginx in front of the API happily buffers the whole
# response and delivers it in one piece at the end.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def format_event(event_type: str, data: str, event_id: str | None = None) -> str:
    """Render one SSE record.

    The blank line at the end is what tells the client the record is complete.
    """
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    # A payload containing newlines must be split across several data: lines.
    for line in data.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"


def comment(text: str) -> str:
    """A no-op record. Keeps idle connections alive through proxy timeouts."""
    return f": {text}\n\n"
