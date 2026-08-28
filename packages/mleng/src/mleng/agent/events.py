"""Event contract for a single agent run.

This is the vocabulary shared by every layer: the runner emits these, the worker
publishes them to Redis, the API serializes them as SSE, and the browser renders
them. Keeping it a typed contract in one place means a change here surfaces as a
type error rather than as a silently ignored field three services away.

The plan's "step" event is intentionally absent: tool_call and tool_result
already describe every observable step this agent takes, and an event nobody
populates is worse than no event.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter

from .schemas import AgentAnswer


class _Event(BaseModel):
    # Monotonic within a run. Lets a client detect gaps and, later, resume a
    # dropped SSE connection from the last event it saw.
    seq: int = 0


class RunStarted(_Event):
    type: Literal["run_started"] = "run_started"
    run_id: str


class ToolCall(_Event):
    """The model decided to call a tool. Emitted once the call is complete."""

    type: Literal["tool_call"] = "tool_call"
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class ToolResult(_Event):
    """A tool returned. Carries a summary, never the full payload.

    A tool payload can be large; the UI wants a one-line summary, and
    shipping the whole thing through Redis and SSE would be waste.
    """

    type: Literal["tool_result"] = "tool_result"
    name: str
    ok: bool
    summary: str


class Token(_Event):
    """A fragment of the answer text, for progressive rendering."""

    type: Literal["token"] = "token"
    text: str


class Answer(_Event):
    """The complete structured answer: text, confidence, abstention."""

    type: Literal["answer"] = "answer"
    answer: AgentAnswer


class Error(_Event):
    type: Literal["error"] = "error"
    message: str


class Done(_Event):
    """Always the last event of a run, whatever the outcome."""

    type: Literal["done"] = "done"
    run_id: str
    status: Literal["succeeded", "failed", "cancelled"]


RunEvent = Annotated[
    Union[RunStarted, ToolCall, ToolResult, Token, Answer, Error, Done],
    Field(discriminator="type"),
]

_ADAPTER: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)


def parse_event(raw: str | bytes) -> RunEvent:
    """Rebuild an event from its JSON form, dispatching on the type field."""
    return _ADAPTER.validate_json(raw)
