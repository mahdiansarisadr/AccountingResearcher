"""The durable record of a conversation.

Against real Postgres, so the foreign keys, the CHECK on role, and the cascade
from thread to messages and runs are part of what is asserted.
"""

from __future__ import annotations

import uuid

import app_db
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_a_new_thread_is_empty_and_titled(session, owner) -> None:
    thread = app_db.create_thread(session, owner.id)

    assert thread.title == app_db.DEFAULT_TITLE
    assert thread.created_at is not None
    assert thread.updated_at is not None
    assert app_db.list_messages(session, thread.id) == []


def test_a_supplied_title_is_kept(session, owner) -> None:
    thread = app_db.create_thread(session, owner.id, title="  Q2 travel  ")

    assert thread.title == "Q2 travel"


def test_an_empty_title_falls_back_to_the_default(session, owner) -> None:
    thread = app_db.create_thread(session, owner.id, title="   ")

    assert thread.title == app_db.DEFAULT_TITLE


def test_a_thread_is_found_by_its_owner_and_not_by_anyone_else(
    session, owner, other_user
) -> None:
    thread = app_db.create_thread(session, owner.id)

    assert app_db.get_owned_thread(session, thread.id, owner.id) is not None
    assert app_db.get_owned_thread(session, thread.id, other_user.id) is None


def test_threads_are_listed_for_their_owner_only(session, owner, other_user) -> None:
    mine = app_db.create_thread(session, owner.id, title="mine")
    app_db.create_thread(session, other_user.id, title="theirs")

    listed = app_db.list_threads(session, owner.id)

    assert [thread.id for thread in listed] == [mine.id]


def test_the_first_question_becomes_the_title(session, owner) -> None:
    thread = app_db.create_thread(session, owner.id)

    app_db.append_message(
        session,
        thread.id,
        app_db.MessageRole.USER,
        "How much did Finance spend?",
        title_from="How much did Finance spend?",
    )

    session.expire_all()
    assert app_db.get_owned_thread(session, thread.id, owner.id).title == (
        "How much did Finance spend?"
    )


def test_a_chosen_title_is_not_overwritten_by_the_first_question(session, owner) -> None:
    thread = app_db.create_thread(session, owner.id, title="Finance")

    app_db.append_message(
        session,
        thread.id,
        app_db.MessageRole.USER,
        "How much did Finance spend?",
        title_from="How much did Finance spend?",
    )

    session.expire_all()
    assert app_db.get_owned_thread(session, thread.id, owner.id).title == "Finance"


def test_messages_are_returned_oldest_first(session, owner) -> None:
    thread = app_db.create_thread(session, owner.id)
    app_db.append_message(session, thread.id, app_db.MessageRole.USER, "first")
    app_db.append_message(session, thread.id, app_db.MessageRole.ASSISTANT, "second")
    app_db.append_message(session, thread.id, app_db.MessageRole.USER, "third")

    contents = [message.content for message in app_db.list_messages(session, thread.id)]

    assert contents == ["first", "second", "third"]


def test_conversation_history_skips_tool_turns_and_keeps_the_tail(session, owner) -> None:
    thread = app_db.create_thread(session, owner.id)
    app_db.append_message(session, thread.id, app_db.MessageRole.USER, "old")
    app_db.append_message(session, thread.id, app_db.MessageRole.TOOL, "ran SQL")
    app_db.append_message(session, thread.id, app_db.MessageRole.ASSISTANT, "reply")

    history = app_db.conversation_history(session, thread.id)

    assert history == [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "reply"},
    ]


def test_conversation_history_is_windowed(session, owner, monkeypatch) -> None:
    thread = app_db.create_thread(session, owner.id)
    for index in range(6):
        role = app_db.MessageRole.USER if index % 2 == 0 else app_db.MessageRole.ASSISTANT
        app_db.append_message(session, thread.id, role, str(index))

    monkeypatch.setattr("app_db.threads.HISTORY_WINDOW", 2)

    history = app_db.conversation_history(session, thread.id)

    assert [turn["content"] for turn in history] == ["4", "5"]


def test_an_active_run_is_detected(session, owner, thread, run_id) -> None:
    assert app_db.has_active_run(session, thread.id) is False

    app_db.create_run(session, run_id, owner.id, thread.id)

    assert app_db.has_active_run(session, thread.id) is True

    app_db.mark_finished(session, run_id, app_db.RunStatus.SUCCEEDED)

    assert app_db.has_active_run(session, thread.id) is False


def test_active_runs_are_counted_across_a_users_threads(session, owner, thread) -> None:
    other = app_db.create_thread(session, owner.id)
    first = app_db.create_run(session, uuid.uuid4(), owner.id, thread.id)
    app_db.create_run(session, uuid.uuid4(), owner.id, other.id)

    assert app_db.count_active_runs(session, owner.id) == 2

    app_db.mark_finished(session, first.id, app_db.RunStatus.SUCCEEDED)

    assert app_db.count_active_runs(session, owner.id) == 1


def test_deleting_a_thread_removes_its_messages_and_runs(
    session, owner, thread, run_id
) -> None:
    app_db.append_message(session, thread.id, app_db.MessageRole.USER, "question")
    app_db.create_run(session, run_id, owner.id, thread.id)

    assert app_db.delete_owned_thread(session, thread.id, owner.id) is True

    assert app_db.get_run(session, run_id) is None
    assert app_db.list_messages(session, thread.id) == []
    assert app_db.get_owned_thread(session, thread.id, owner.id) is None


def test_deleting_someone_elses_thread_does_nothing(
    session, owner, other_user
) -> None:
    thread = app_db.create_thread(session, owner.id)

    assert app_db.delete_owned_thread(session, thread.id, other_user.id) is False
    assert app_db.get_owned_thread(session, thread.id, owner.id) is not None


def test_the_database_refuses_a_role_it_does_not_recognise(session, owner) -> None:
    thread = app_db.create_thread(session, owner.id)

    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO app.messages (id, thread_id, seq, role, content)"
                " VALUES (:id, :thread_id, 1, 'narrator', 'hi')"
            ),
            {"id": uuid.uuid4(), "thread_id": thread.id},
        )
    session.rollback()
