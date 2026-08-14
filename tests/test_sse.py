"""Server-Sent Events framing.

Small surface, but a mistake here is silent: a browser given a malformed record
simply never fires the event, and the UI looks like a hung run.
"""

from __future__ import annotations

from api.sse import SSE_HEADERS, comment, format_event


def test_a_record_ends_with_a_blank_line() -> None:
    # The blank line is what tells the client the record is complete; without it
    # the event is buffered indefinitely.
    assert format_event("token", '{"text":"hi"}').endswith("\n\n")


def test_a_record_names_its_type_and_carries_its_payload() -> None:
    record = format_event("token", '{"text":"hi"}')

    assert "event: token" in record
    assert 'data: {"text":"hi"}' in record


def test_an_id_is_included_when_given_and_omitted_when_not() -> None:
    assert format_event("token", "{}", event_id="1712-0").startswith("id: 1712-0\n")
    assert "id:" not in format_event("token", "{}")


def test_a_multiline_payload_is_split_across_data_lines() -> None:
    # A raw newline inside a payload would otherwise terminate the record early
    # and the rest would be read as a new one.
    record = format_event("error", "line one\nline two")

    assert "data: line one\ndata: line two" in record
    assert record.count("\n\n") == 1


def test_an_empty_payload_still_produces_a_data_line() -> None:
    assert "data: \n" in format_event("done", "")


def test_a_comment_is_a_record_a_client_ignores() -> None:
    assert comment("stream open") == ": stream open\n\n"


def test_buffering_is_disabled_for_intermediaries() -> None:
    # Without this an nginx in front of the API buffers the whole response and
    # delivers it in one piece at the end, which defeats streaming entirely.
    assert SSE_HEADERS["X-Accel-Buffering"] == "no"
    assert SSE_HEADERS["Cache-Control"] == "no-cache"
