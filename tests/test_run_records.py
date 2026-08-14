"""The durable record of a run.

The transitions are guarded rather than blind writes, because the API and the
worker drive the same lifecycle from two processes and a redelivered job must not
be able to rewind it. These tests run against real Postgres: the CHECK
constraint and the server-side timestamps are part of what is being asserted.
"""

from __future__ import annotations

import uuid

import app_db
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_a_new_run_is_queued_and_not_yet_started(session, run_id, owner) -> None:
    run = app_db.create_run(session, run_id, owner.id)

    assert run.status is app_db.RunStatus.QUEUED
    # Stamped by the database, so the API and the worker cannot disagree by clock.
    assert run.created_at is not None
    assert run.started_at is None
    assert run.finished_at is None
    assert run.error is None


def test_a_run_can_be_found_by_id_and_is_absent_otherwise(session, run_id, owner) -> None:
    app_db.create_run(session, run_id, owner.id)

    assert app_db.get_run(session, run_id) is not None
    assert app_db.get_run(session, uuid.uuid4()) is None


def test_starting_a_run_records_when_it_was_picked_up(session, run_id, owner) -> None:
    app_db.create_run(session, run_id, owner.id)

    assert app_db.mark_running(session, run_id) is True

    run = app_db.get_run(session, run_id)
    assert run.status is app_db.RunStatus.RUNNING
    assert run.started_at is not None
    # Queue wait is created_at -> started_at, so ordering has to hold.
    assert run.started_at >= run.created_at


def test_a_redelivered_job_cannot_restart_a_running_run(session, run_id, owner) -> None:
    app_db.create_run(session, run_id, owner.id)
    app_db.mark_running(session, run_id)
    first_started = app_db.get_run(session, run_id).started_at

    assert app_db.mark_running(session, run_id) is False

    session.expire_all()
    assert app_db.get_run(session, run_id).started_at == first_started


def test_a_redelivered_job_cannot_resurrect_a_settled_run(session, run_id, owner) -> None:
    app_db.create_run(session, run_id, owner.id)
    app_db.mark_finished(session, run_id, app_db.RunStatus.CANCELLED)

    assert app_db.mark_running(session, run_id) is False

    session.expire_all()
    assert app_db.get_run(session, run_id).status is app_db.RunStatus.CANCELLED


def test_settling_a_run_records_the_outcome_and_when(session, run_id, owner) -> None:
    app_db.create_run(session, run_id, owner.id)
    app_db.mark_running(session, run_id)

    assert app_db.mark_finished(session, run_id, app_db.RunStatus.SUCCEEDED) is True

    session.expire_all()
    run = app_db.get_run(session, run_id)
    assert run.status is app_db.RunStatus.SUCCEEDED
    assert run.finished_at is not None
    assert run.error is None


def test_a_failed_run_records_why(session, run_id, owner) -> None:
    app_db.create_run(session, run_id, owner.id)

    app_db.mark_finished(
        session, run_id, app_db.RunStatus.FAILED, "RuntimeError: model unavailable"
    )

    session.expire_all()
    assert app_db.get_run(session, run_id).error == "RuntimeError: model unavailable"


def test_the_first_outcome_wins(session, run_id, owner) -> None:
    # A crash handler on the way out must not overwrite a deliberate
    # cancellation with "failed".
    app_db.create_run(session, run_id, owner.id)
    app_db.mark_finished(session, run_id, app_db.RunStatus.CANCELLED)

    assert app_db.mark_finished(session, run_id, app_db.RunStatus.FAILED, "boom") is False

    session.expire_all()
    run = app_db.get_run(session, run_id)
    assert run.status is app_db.RunStatus.CANCELLED
    assert run.error is None


def test_settling_requires_a_terminal_status(session, run_id, owner) -> None:
    app_db.create_run(session, run_id, owner.id)

    with pytest.raises(ValueError, match="not a terminal status"):
        app_db.mark_finished(session, run_id, app_db.RunStatus.RUNNING)


def test_an_enormous_error_is_truncated_rather_than_stored_whole(session, run_id, owner) -> None:
    # A driver error can carry an entire query; the head is where the cause is.
    app_db.create_run(session, run_id, owner.id)

    app_db.mark_finished(session, run_id, app_db.RunStatus.FAILED, "x" * 10_000)

    session.expire_all()
    assert len(app_db.get_run(session, run_id).error) == app_db.MAX_ERROR_CHARS


def test_status_is_stored_as_the_value_the_api_speaks(session, run_id, owner) -> None:
    # Guards against SQLAlchemy's default of persisting enum *names*: a column
    # holding "QUEUED" would not match the wire format or the CHECK constraint.
    app_db.create_run(session, run_id, owner.id)
    session.flush()

    stored = session.execute(
        text("SELECT status FROM app.runs WHERE id = :id"), {"id": run_id}
    ).scalar_one()

    assert stored == "queued"


def test_the_database_refuses_a_status_it_does_not_recognise(session, run_id, owner) -> None:
    # The constraint is the backstop for anything writing to this table without
    # going through the model.
    with pytest.raises(IntegrityError):
        session.execute(
            text(
                "INSERT INTO app.runs (id, user_id, status)"
                " VALUES (:id, :user_id, 'sideways')"
            ),
            {"id": run_id, "user_id": owner.id},
        )
    session.rollback()


# --- Ownership ---------------------------------------------------------------


def test_a_run_is_found_by_its_owner(session, run_id, owner) -> None:
    app_db.create_run(session, run_id, owner.id)

    assert app_db.get_owned_run(session, run_id, owner.id) is not None


def test_a_run_is_not_found_by_anyone_else(session, run_id, owner, other_user) -> None:
    # The whole of per-user isolation: someone else's run id is worth nothing,
    # and the query is what enforces that rather than a check on the result.
    app_db.create_run(session, run_id, owner.id)

    assert app_db.get_owned_run(session, run_id, other_user.id) is None


def test_the_database_refuses_a_run_with_no_owner(session, run_id) -> None:
    with pytest.raises(IntegrityError):
        session.execute(
            text("INSERT INTO app.runs (id, status) VALUES (:id, 'queued')"),
            {"id": run_id},
        )
    session.rollback()


def test_the_database_refuses_a_run_owned_by_nobody_real(session, run_id) -> None:
    # The foreign key, so a run cannot outlive the account it is attributed to by
    # pointing at an id that was never there.
    with pytest.raises(IntegrityError):
        app_db.create_run(session, run_id, uuid.uuid4())
    session.rollback()
