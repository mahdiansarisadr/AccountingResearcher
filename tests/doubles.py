"""Stand-ins for the agent.

``run_agent`` consumes LangGraph's dual-mode stream: ``("messages", (chunk,
meta))`` for token-level output and ``("values", state)`` for accumulated state,
which is where completed tool calls and the final structured answer appear.
These helpers script that shape, so the event contract can be exercised without
a model, an API key or a network.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from accounting_research.agent.schemas import AgentAnswer
from langchain_core.messages import AIMessageChunk


class FakeMessage:
    """A completed LangChain message as it appears in accumulated state."""

    def __init__(
        self,
        *,
        type: str = "ai",
        content: str = "",
        name: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        self.type = type
        self.content = content
        self.name = name
        self.tool_calls = tool_calls or []


class FakeAgent:
    """Replays a scripted (mode, chunk) sequence, recording how it was called."""

    def __init__(self, chunks: list[tuple[str, Any]]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []

    def stream(self, payload: dict[str, Any], **kwargs: Any) -> Iterator[tuple[str, Any]]:
        self.calls.append({"payload": payload, **kwargs})
        yield from self.chunks


class ExplodingAgent:
    """Fails partway through streaming, as a model or network outage would."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def stream(self, _payload: dict[str, Any], **_kwargs: Any) -> Iterator[tuple[str, Any]]:
        yield state_chunk()
        raise self.error


def make_answer(text: str = "42 dollars.") -> AgentAnswer:
    return AgentAnswer(answer=text, confidence=0.9)


def token_chunk(text: str) -> tuple[str, Any]:
    """A fragment of model output.

    The agent returns structured output, which OpenAI streams as raw JSON text,
    so a realistic fragment is a slice of ``{"answer": "..."}`` and not prose.
    """
    return "messages", (AIMessageChunk(content=text), {})


def state_chunk(
    messages: list[Any] | None = None, structured: AgentAnswer | None = None
) -> tuple[str, Any]:
    state: dict[str, Any] = {"messages": messages or []}
    if structured is not None:
        state["structured_response"] = structured
    return "values", state
