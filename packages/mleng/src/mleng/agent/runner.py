"""The single way to execute an agent run.

Everything that runs the agent — the CLI and the background worker —
goes through :func:`run_agent`. It yields events instead of printing,
so the caller decides whether they become terminal output, Redis messages, or
assertions in a test.

History is passed in explicitly rather than relying on the checkpointer. The
API and the worker are separate processes, so in-process memory cannot be shared
between them; the caller owns the conversation and hands it over each turn.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessageChunk
from langchain_core.utils.json import parse_partial_json

from .builder import build_agent
from .events import Answer, Done, Error, RunEvent, RunStarted, Token, ToolCall, ToolResult

# A turn in the conversation, as {"role": ..., "content": ...}.
Message = dict[str, Any]


def _answer_so_far(buffer: str) -> str:
    """Extract the answer text from a partially received JSON object.

    The model returns structured output, which OpenAI streams as raw JSON text:
    ``{"``, ``answer``, ``":"``, ``The``, ``` following```… Forwarding that
    verbatim would show JSON to the user, so each chunk is parsed as incomplete
    JSON and only the ``answer`` field is followed as it grows.

    Returns an empty string until enough has arrived to parse, which is harmless
    because the caller only ever emits the part it has not sent yet.
    """
    try:
        data = parse_partial_json(buffer)
    except Exception:
        return ""
    if isinstance(data, dict):
        answer = data.get("answer")
        if isinstance(answer, str):
            return answer
    return ""


def _summarize_tool_result(name: str, content: str, ok: bool) -> str:
    """Describe a tool result in one short line."""
    if not ok:
        return content.splitlines()[0][:200]
    if name in {"profile_dataset", "train_model"}:
        return content.splitlines()[0][:200]
    return f"{len(content)} chars"


def _tool_events(message: Any) -> Iterator[RunEvent]:
    """Turn one completed message into tool_call / tool_result events."""
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        for call in tool_calls:
            yield ToolCall(name=call.get("name", "tool"), args=call.get("args") or {})
        return

    if getattr(message, "type", None) == "tool":
        name = getattr(message, "name", "tool") or "tool"
        content = str(getattr(message, "content", ""))
        # The tools report failures as ERROR-prefixed text rather than raising,
        # so the model can recover; the event contract keeps that distinction.
        ok = not content.startswith("ERROR")
        yield ToolResult(name=name, ok=ok, summary=_summarize_tool_result(name, content, ok))


def run_agent(
    message: str,
    *,
    history: Sequence[Message] | None = None,
    run_id: str | None = None,
    agent: Any | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[RunEvent]:
    """Execute one turn and yield its events in order.

    Args:
        message: The user's question for this turn.
        history: Prior turns, oldest first. The caller owns this.
        run_id: Identifier for the run; generated when omitted.
        agent: A prebuilt agent to reuse. The worker passes one so it is not
            rebuilt per job.
        is_cancelled: Polled between events. When it returns True the run stops
            and the final event reports ``cancelled``.

    The stream always ends with exactly one Done event, so a consumer can close
    a connection on it without special-casing failures.
    """
    run_id = run_id or str(uuid4())
    agent = agent if agent is not None else build_agent()

    messages: list[Message] = [*(history or []), {"role": "user", "content": message}]
    # Skip the messages we supplied when diffing for new ones, otherwise a
    # history containing tool calls would replay as fresh events.
    consumed = len(messages)

    seq = 0

    def numbered(event: RunEvent) -> RunEvent:
        nonlocal seq
        event.seq = seq
        seq += 1
        return event

    yield numbered(RunStarted(run_id=run_id))

    answer: Any = None
    buffer = ""
    streamed_chars = 0
    status = "succeeded"

    try:
        # Two stream modes at once: "messages" carries token-level chunks,
        # "values" carries the accumulated state, which is where completed tool
        # calls and the final structured answer become visible.
        for mode, chunk in agent.stream(
            {"messages": messages},
            config={"configurable": {"thread_id": run_id}},
            stream_mode=["values", "messages"],
        ):
            if is_cancelled is not None and is_cancelled():
                status = "cancelled"
                break

            if mode == "messages":
                part, _meta = chunk
                # Only model output is a token. Tool results also arrive here,
                # and forwarding those as tokens would dump raw SQL rows.
                if isinstance(part, AIMessageChunk) and isinstance(part.content, str):
                    buffer += part.content
                    text = _answer_so_far(buffer)
                    if len(text) > streamed_chars:
                        yield numbered(Token(text=text[streamed_chars:]))
                        streamed_chars = len(text)
                continue

            state_messages = chunk.get("messages", [])
            for state_message in state_messages[consumed:]:
                for event in _tool_events(state_message):
                    yield numbered(event)
            consumed = max(consumed, len(state_messages))

            if chunk.get("structured_response") is not None:
                answer = chunk["structured_response"]

    except Exception as exc:  # noqa: BLE001 - surfaced to the client as an event
        yield numbered(Error(message=f"{type(exc).__name__}: {exc}"))
        yield numbered(Done(run_id=run_id, status="failed"))
        return

    if status == "cancelled":
        yield numbered(Done(run_id=run_id, status="cancelled"))
        return

    if answer is None:
        # The agent stopped without producing a grounded answer, which happens
        # when it exhausts its step budget. Reported as a failure rather than
        # invented as an abstention, since the agent never actually said it.
        yield numbered(
            Error(message="No grounded answer was produced within the step budget.")
        )
        yield numbered(Done(run_id=run_id, status="failed"))
        return

    yield numbered(Answer(answer=answer))
    yield numbered(Done(run_id=run_id, status="succeeded"))
