"""Continuity storage: compatible migration, notifications, and input cursors."""

from __future__ import annotations

import sqlite3

import pytest

from nexus.task import TaskNotFound, TaskStore, TaskValidationError
from nexus.task.store import TASK_SCHEMA_VERSION


def make_store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.sqlite3")


def create(store: TaskStore, title: str):
    return store.create(title=title, goal="verify continuity", workspace=str(store.path.parent))


def test_create_request_id_is_idempotent_for_same_and_conflicting_operations(tmp_path):
    store = make_store(tmp_path)
    task = store.create(
        title="create once", goal="one task", workspace=str(tmp_path), request_id="create-once",
    )
    retried = TaskStore(store.path).create(
        title="retry text", goal="retry text", workspace=str(tmp_path), request_id="create-once",
    )
    assert retried.id == task.id
    assert len(store.list()) == 1
    with pytest.raises(Exception, match="different task command"):
        store.command(task.id, "cancelled", request_id="create-once")


def test_v1_database_is_upgraded_in_place_without_losing_tasks(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = make_store(tmp_path)
    task = create(store, "old work")
    store.add_message(task.id, kind="user", text="existing request")
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE task_input_cursors")
        connection.execute("DROP TABLE notifications")
        connection.execute("PRAGMA user_version=1")
        connection.commit()

    upgraded = TaskStore(path)
    assert upgraded.get(task.id).title == "old work"
    assert upgraded.messages(task.id)[0]["text"] == "existing request"
    assert upgraded.input_status(task.id) == {"applied_through": 0, "pending_count": 1}
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == TASK_SCHEMA_VERSION
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"notifications", "task_input_cursors"} <= tables


def test_important_notifications_are_transactional_and_deduplicated(tmp_path):
    store = make_store(tmp_path)
    task = create(store, "release")
    store.command(task.id, "running", request_id="running")
    store.add_message(task.id, kind="assistant", text="ordinary internal slice")
    assert store.notifications() == []

    store.update_progress(task.id, expected_version=1, changes={"blocked": "waiting on access"})
    blocked = store.notifications()
    assert len(blocked) == 1
    assert blocked[0]["kind"] == "blocked"
    assert blocked[0]["current_state"] == "running"
    assert blocked[0]["text"].startswith("任务遇到阻碍：release")
    assert blocked[0]["interaction_id"] is None

    store.command(task.id, "cancelled", request_id="cancel")
    cancelled = store.notifications(after=blocked[0]["id"])
    assert [item["kind"] for item in cancelled] == ["cancelled"]
    assert cancelled[0]["control_version"] == store.get(task.id).control_version


def test_completion_and_pause_use_current_work_summary(tmp_path):
    store = make_store(tmp_path)
    task = create(store, "ship")
    store.command(task.id, "running", request_id="run")
    store.update_progress(task.id, expected_version=1, changes={"work_summary": "all checks passed"})
    store.command(task.id, "completed", request_id="done")
    completed = [item for item in store.notifications() if item["kind"] == "completed"]
    assert len(completed) == 1
    assert "all checks passed" in completed[0]["text"]
    assert completed[0]["current_state"] == "completed"
    assert store.notifications(unread_only=True)[0]["kind"] == "completed"

    paused = create(store, "pause me")
    store.command(paused.id, "paused", request_id="pause")
    notice = next(item for item in store.notifications() if item["task_id"] == paused.id)
    assert notice["kind"] == "paused"
    assert "已挂起" in notice["text"]


def test_interaction_notifications_report_staleness_at_read_time(tmp_path):
    store = make_store(tmp_path)
    task = create(store, "approval")
    store.command(task.id, "running", request_id="run")
    interaction_id = store.interaction_create(
        task_id=task.id, run_id=None, kind="approval",
        payload={"tool": "write_file", "reason": "allow this write"},
        control_version=store.get(task.id).control_version,
    )
    notice = store.notifications()[0]
    assert notice["kind"] == "interaction"
    assert notice["interaction_id"] == interaction_id
    assert notice["interaction_payload"]["reason"] == "allow this write"
    assert notice["interaction_expired"] is False
    assert "allow this write" in notice["text"]

    task_now = store.get(task.id)
    store.interaction_answer(interaction_id, answer=True, expected_version=task_now.control_version)
    notice = store.notifications()[0]
    assert notice["interaction_expired"] is True
    assert notice["stale_reason"]


def test_waiting_transition_does_not_duplicate_the_pending_interaction(tmp_path):
    store = make_store(tmp_path)
    task = create(store, "one decision")
    store.command(task.id, "running", request_id="running")
    interaction_id = store.interaction_create(
        task_id=task.id, run_id=None, kind="question",
        payload={"questions": [{"question": "which environment?"}]},
        control_version=store.get(task.id).control_version,
    )
    store.command(task.id, "waiting_user", request_id="waiting")
    assert store.get(task.id).state == "waiting_user"
    notices = [item for item in store.notifications() if item["task_id"] == task.id]
    assert len(notices) == 1
    assert notices[0]["interaction_id"] == interaction_id
    assert notices[0]["interaction_payload"]["questions"][0]["question"] == "which environment?"
    assert notices[0]["current_state"] == "waiting_user"
    assert store.get(task.id).state == "waiting_user"
    # The surviving interaction notice is a live projection at read time.
    assert notices[0]["control_version"] == store.get(task.id).control_version
    assert notices[0]["interaction_expired"] is False

    second = store.interaction_create(
        task_id=task.id, run_id=None, kind="approval",
        payload={"tool": "write_file"},
        control_version=store.get(task.id).control_version,
    )
    notices = [item for item in store.notifications() if item["task_id"] == task.id]
    assert len(notices) == 2
    assert notices[-1]["interaction_id"] == second


def test_waiting_transition_without_interaction_gets_a_fallback_notice(tmp_path):
    store = make_store(tmp_path)
    task = create(store, "queued")
    store.command(task.id, "queued", request_id="queued")
    store.command(task.id, "waiting_user", request_id="waiting")
    notices = [item for item in store.notifications() if item["task_id"] == task.id]
    assert len(notices) == 1
    assert notices[0]["kind"] == "interaction"
    assert notices[0]["interaction_id"] is None
    assert notices[0]["current_state"] == "waiting_user"


def test_restart_and_held_queue_persist_interrupted_and_interaction(tmp_path):
    store = make_store(tmp_path)
    task = create(store, "interrupted")
    store.command(task.id, "running", request_id="run")
    store.begin_run(task.id)
    reopened = TaskStore(store.path)
    assert reopened.recover_interrupted() == [task.id]
    interrupted = [item for item in reopened.notifications() if item["kind"] == "interrupted"]
    assert len(interrupted) == 1
    assert "意外中断" in interrupted[0]["text"]

    held = create(store, "held")
    store.command(held.id, "queued", request_id="queue")
    reopened = TaskStore(store.path)
    assert reopened.hold_queued_at_startup() == [held.id]
    assert reopened.get(held.id).state == "paused"
    assert not reopened.pending_interactions(held.id)
    held_notice = next(item for item in reopened.notifications() if item["task_id"] == held.id)
    assert held_notice["kind"] == "paused"


def test_notification_ack_is_idempotent_and_persistent(tmp_path):
    store = make_store(tmp_path)
    task = create(store, "read me")
    store.command(task.id, "cancelled", request_id="cancel")
    notice = store.notifications()[0]
    first = store.ack_notification(notice["id"])
    second = TaskStore(store.path).ack_notification(notice["id"])
    assert first["read_at"]
    assert second["read_at"] == first["read_at"]
    assert TaskStore(store.path).notifications(unread_only=True) == []
    with pytest.raises(TaskNotFound):
        store.ack_notification(999_999)


def test_input_cursor_never_moves_backwards_and_counts_user_messages(tmp_path):
    store = make_store(tmp_path)
    task = create(store, "inputs")
    first = store.add_message(task.id, kind="user", text="one")
    second = store.add_message(task.id, kind="user", text="two")
    assert store.input_status(task.id) == {"applied_through": first["seq"] - 1,
                                           "pending_count": 2}
    assert store.mark_inputs_applied(task.id, second["seq"]) == {
        "applied_through": second["seq"], "pending_count": 0,
    }
    assert store.mark_inputs_applied(task.id, first["seq"]) == {
        "applied_through": second["seq"], "pending_count": 0,
    }
    with pytest.raises(TaskValidationError):
        store.mark_inputs_applied(task.id, second["seq"] + 1)


def test_notification_cursor_is_global_and_pageable(tmp_path):
    store = make_store(tmp_path)
    one = create(store, "one")
    two = create(store, "two")
    store.command(one.id, "cancelled", request_id="cancel-one")
    store.command(two.id, "cancelled", request_id="cancel-two")
    first_page = store.notifications(limit=1)
    second_page = store.notifications(after=first_page[0]["id"], limit=1)
    assert first_page[0]["task_id"] == one.id
    assert second_page[0]["task_id"] == two.id
    assert second_page[0]["id"] > first_page[0]["id"]


def test_notification_count_is_not_limited_by_page_size(tmp_path):
    store = make_store(tmp_path)
    tasks = [create(store, f"notice {index}") for index in range(4)]
    for index, task in enumerate(tasks):
        store.command(task.id, "cancelled", request_id=f"cancel-{index}")
    assert len(store.notifications(limit=2)) == 2
    assert store.notification_count() == 4
    assert store.notification_count(unread_only=False) == 4
    store.ack_notification(store.notifications(limit=1)[0]["id"])
    assert store.notification_count() == 3
    assert store.notification_count(unread_only=False) == 4
