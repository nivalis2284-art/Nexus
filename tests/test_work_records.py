"""Work records: writer eligibility, model binding, and explicit re-open."""

from __future__ import annotations

import sqlite3

import pytest

from nexus.task import TaskConflict, TaskStore, TaskValidationError


def make_store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.sqlite3")


def create(store: TaskStore, title: str = "work", **extra):
    return store.create(title=title, goal=title, workspace=str(store.path.parent), **extra)


def test_read_only_persists_and_defaults_to_writer(tmp_path):
    store = make_store(tmp_path)
    default_work = create(store, "writer")
    reader = create(store, "reader", read_only=True)

    assert default_work.read_only is False
    assert reader.read_only is True
    reopened = TaskStore(store.path)
    assert reopened.get(reader.id).read_only is True
    assert reopened.get(default_work.id).read_only is False

    changed = store.update_read_only(reader.id, False)
    assert changed.read_only is False
    assert TaskStore(store.path).get(reader.id).read_only is False
    with pytest.raises(TaskValidationError):
        store.update_read_only(reader.id, "yes")
    with pytest.raises(TaskValidationError):
        create(store, "bad", read_only="yes")


def test_existing_database_gains_the_writer_flag_without_new_tables(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute("ALTER TABLE tasks DROP COLUMN read_only")
        # A v2 database may still hold the retired schedule and autonomy rows.
        connection.execute("CREATE TABLE schedules(task_id TEXT PRIMARY KEY, due_at TEXT)")
        connection.execute("INSERT INTO schedules VALUES(?,'2030-01-01T00:00:00+00:00')", (task.id,))
        connection.execute("CREATE TABLE autonomy_events(source_key TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO autonomy_events VALUES('kept')")
        connection.execute("PRAGMA user_version=2")
        connection.commit()

    upgraded = TaskStore(store.path)
    assert upgraded.get(task.id).read_only is False
    assert upgraded.get(task.id).state == "pending"
    with sqlite3.connect(store.path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    # Old business tables stay on disk unread until the owner archives them.
    assert "schedules" in tables and "autonomy_events" in tables
    TaskStore(tmp_path / "fresh.sqlite3")
    with sqlite3.connect(tmp_path / "fresh.sqlite3") as connection:
        fresh = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "schedules" not in fresh


def test_changing_model_during_a_run_keeps_history_and_approvals(tmp_path):
    store = make_store(tmp_path)
    task = create(store, provider_id="p1", model_id="m1", effort="low")
    store.command(task.id, "running", request_id="start")
    store.add_message(task.id, kind="user", text="keep this request")
    store.add_message(task.id, kind="assistant", text="keep this answer")
    store.update_progress(task.id, expected_version=1, changes={"work_summary": "half done"})
    approval = store.interaction_create(
        task_id=task.id, run_id=None, kind="approval",
        payload={"tool": "bash", "arguments": {"command": "echo ok"}},
    )

    updated = store.set_model(task.id, provider_id="p2", model_id="m2", effort="high")

    assert updated.state == "running"
    assert (updated.provider_id, updated.model_id, updated.effort) == ("p2", "m2", "high")
    assert updated.control_version > task.control_version
    store.interaction_answer(approval, answer=True, expected_version=updated.control_version)
    assert store.interaction(approval)["status"] == "answered"
    messages = store.messages(task.id)
    assert [item["text"] for item in messages if item["kind"] in ("user", "assistant")][:2] == [
        "keep this request", "keep this answer",
    ]
    assert store.get(task.id).work_summary == "half done"
    assert any(event["type"] == "task.model" for event in store.events(task.id))


def test_model_change_offers_an_optional_version_guard(tmp_path):
    store = make_store(tmp_path)
    task = create(store, provider_id="p1", model_id="m1")
    with pytest.raises(TaskConflict):
        store.set_model(task.id, provider_id="p2", model_id="m2", expected_version=99)
    with pytest.raises(TaskValidationError):
        store.set_model(task.id, provider_id="", model_id="m2")
    with pytest.raises(TaskValidationError):
        store.set_model(task.id, provider_id="p2", model_id="m2", effort="extreme")
    assert store.get(task.id).provider_id == "p1"


def test_reopen_of_completed_work_keeps_the_prior_result(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    store.command(task.id, "running", request_id="start")
    store.update_progress(task.id, expected_version=1,
                          changes={"work_summary": "first iteration result"})
    store.command(task.id, "completed", request_id="end")

    reopened = store.command(task.id, "reopen", request_id="reopen-1")

    assert reopened.state == "paused"
    assert reopened.wait_reason
    assert any("first iteration result" in item["text"] for item in store.messages(task.id))
    with pytest.raises(TaskConflict):
        store.current_run(reopened.id)
    assert "first iteration result" in store.get(task.id).work_summary
    assert [event["type"] for event in store.events(task.id)][-1] != "run.started"


def test_reopen_of_cancelled_work_waits_for_an_explicit_start(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    store.command(task.id, "cancelled", request_id="cancel")
    reopened = store.command(task.id, "reopen", request_id="reopen-1")
    assert reopened.state == "paused"
    assert store.pending_interactions(task.id) == []
    with pytest.raises(TaskConflict):
        store.command(task.id, "reopen", request_id="reopen-2")
