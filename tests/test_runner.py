"""The event contract: what run_agent promises every caller.

Phase 1's whole premise is that the agent yields events instead of printing, and
that the stream is well-formed whatever happens inside it. These tests pin that
down with a scripted agent, so they say nothing about answer quality and need no
model, no key and no database.
"""

from __future__ import annotations

import json

import pytest
from accounting_research.agent.runner import run_agent

from .doubles import (
    ExplodingAgent,
    FakeAgent,
    FakeMessage,
    make_answer,
    state_chunk,
    token_chunk,
)


def events_of(agent, message: str = "How much did Finance spend?", **kwargs):
    return list(run_agent(message, agent=agent, run_id="run-1", **kwargs))


def test_a_run_starts_and_ends_with_exactly_one_terminal_event() -> None:
    agent = FakeAgent([state_chunk(structured=make_answer())])

    events = events_of(agent)
    types = [event.type for event in events]

    assert types[0] == "run_started"
    assert types[-1] == "done"
    assert types.count("done") == 1
    assert events[-1].status == "succeeded"


def test_sequence_numbers_are_monotonic_from_zero() -> None:
    agent = FakeAgent(
        [token_chunk('{"answer": "hi'), state_chunk(structured=make_answer())]
    )

    events = events_of(agent)

    assert [event.seq for event in events] == list(range(len(events)))


def test_tokens_carry_the_answer_text_not_its_json_envelope() -> None:
    # The model streams structured output as raw JSON. A client rendering tokens
    # verbatim would show the braces and the field name to the user.
    fragments = ['{"', "answer", '": "', "The total ", "is $5", '.", "confidence": 0.9}']
    agent = FakeAgent(
        [token_chunk(fragment) for fragment in fragments]
        + [state_chunk(structured=make_answer("The total is $5."))]
    )

    events = events_of(agent)
    streamed = "".join(e.text for e in events if e.type == "token")

    assert streamed == "The total is $5."
    assert "{" not in streamed
    assert "confidence" not in streamed


def test_tool_calls_and_results_are_reported_as_they_complete() -> None:
    user = FakeMessage(type="human", content="How much did Finance spend?")
    call = FakeMessage(
        type="ai", tool_calls=[{"name": "search_schema", "args": {"query": "spend"}}]
    )
    result = FakeMessage(
        type="tool", name="search_schema", content="Table: expenses\nTable: budgets\n"
    )
    agent = FakeAgent(
        [
            state_chunk([user, call]),
            state_chunk([user, call, result]),
            state_chunk([user, call, result], structured=make_answer()),
        ]
    )

    events = events_of(agent)

    tool_call = next(e for e in events if e.type == "tool_call")
    assert tool_call.name == "search_schema"
    assert tool_call.args == {"query": "spend"}

    tool_result = next(e for e in events if e.type == "tool_result")
    assert tool_result.ok is True
    # Summarised, never the payload: a result can be thousands of rows.
    assert tool_result.summary == "2 candidate tables"


def test_a_query_result_is_summarised_by_shape() -> None:
    payload = json.dumps({"columns": ["dept", "total"], "rows": [["Finance", 5], ["HR", 3]]})
    user = FakeMessage(type="human")
    result = FakeMessage(type="tool", name="run_sql_query", content=payload)
    agent = FakeAgent(
        [state_chunk([user, result]), state_chunk([user, result], structured=make_answer())]
    )

    tool_result = next(e for e in events_of(agent) if e.type == "tool_result")

    assert tool_result.summary == "2 rows x 2 columns"


def test_a_failing_tool_is_reported_as_not_ok() -> None:
    user = FakeMessage(type="human")
    result = FakeMessage(
        type="tool", name="run_sql_query", content="ERROR: relation does not exist"
    )
    agent = FakeAgent(
        [state_chunk([user, result]), state_chunk([user, result], structured=make_answer())]
    )

    tool_result = next(e for e in events_of(agent) if e.type == "tool_result")

    assert tool_result.ok is False
    assert "relation does not exist" in tool_result.summary


def test_history_is_not_replayed_as_new_events() -> None:
    # The caller owns the conversation and hands it over each turn. Diffing the
    # accumulated state naively would re-emit every tool call it contains.
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    prior = [FakeMessage(type="human"), FakeMessage(type="ai"), FakeMessage(type="human")]
    fresh = FakeMessage(type="tool", name="search_schema", content="Table: expenses\n")
    agent = FakeAgent(
        [
            state_chunk([*prior, fresh]),
            state_chunk([*prior, fresh], structured=make_answer()),
        ]
    )

    events = events_of(agent, history=history)

    assert sum(1 for e in events if e.type == "tool_result") == 1
    assert agent.calls[0]["payload"]["messages"][:2] == history


def test_cancellation_stops_the_run_and_says_so() -> None:
    agent = FakeAgent(
        [token_chunk('{"answer": "par'), state_chunk(structured=make_answer())]
    )

    events = events_of(agent, is_cancelled=lambda: True)

    assert events[-1].type == "done"
    assert events[-1].status == "cancelled"
    # Cancelled, not failed: nothing went wrong, someone asked it to stop.
    assert not any(e.type == "error" for e in events)


def test_a_crash_becomes_an_error_event_and_a_failed_run() -> None:
    agent = ExplodingAgent(RuntimeError("model unavailable"))

    events = events_of(agent)

    error = next(e for e in events if e.type == "error")
    assert "RuntimeError" in error.message
    assert "model unavailable" in error.message
    assert events[-1].type == "done"
    assert events[-1].status == "failed"


def test_stopping_without_a_grounded_answer_is_a_failure_not_an_abstention() -> None:
    # The agent exhausted its step budget. Reporting this as an abstention would
    # be inventing a refusal the agent never made.
    agent = FakeAgent([state_chunk([FakeMessage(type="ai", content="thinking")])])

    events = events_of(agent)

    assert not any(e.type == "answer" for e in events)
    assert events[-1].status == "failed"
    assert "step budget" in next(e for e in events if e.type == "error").message


def test_the_answer_event_carries_the_structured_result() -> None:
    agent = FakeAgent([state_chunk(structured=make_answer("Exactly $42."))])

    answer_event = next(e for e in events_of(agent) if e.type == "answer")

    assert answer_event.answer.answer == "Exactly $42."
    assert answer_event.answer.confidence == pytest.approx(0.9)
