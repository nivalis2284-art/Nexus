"""Persistent task authority: versioning, isolation, idempotency, and lifecycle."""

from __future__ import annotations

import sqlite3

import pytest

from nexus.task import Evidence, TaskConflict, TaskStore, TaskValidationError
from nexus.task.store import TASK_SCHEMA_VERSION


def make_store(tmp_path):
    return TaskStore(tmp_path / "tasks.sqlite3")


def create(store, title="Task"):
    return store.create(
        title=title,
        goal="Complete a multi-step task",
        constraints="Use the current workspace",
        workspace=str(store.path.parent),
    )


def test_tasks_survive_reopen_and_stay_independent(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    first = TaskStore(path)
    a, b, c = (create(first, name) for name in ("A", "B", "C"))
    first.update_progress(a.id, expected_version=a.progress_version, changes={
        "steps": [{"content": "research", "status": "completed"}],
        "next_step": "implement",
    })
    first.command(b.id, "queued", request_id="b-queue")

    second = TaskStore(path)
    loaded = {item.id: item for item in second.list()}
    assert set(loaded) == {a.id, b.id, c.id}
    assert loaded[a.id].next_step == "implement"
    assert loaded[b.id].state == "queued"
    assert loaded[c.id].state == "pending"
    assert not loaded[c.id].steps


def test_progress_conflict_preserves_user_correction(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    updated = store.update_progress(task.id, expected_version=1, changes={"next_step": "user edit"})
    with pytest.raises(TaskConflict):
        store.update_progress(task.id, expected_version=1, changes={"next_step": "stale model"})
    assert store.get(task.id).next_step == "user edit"
    assert updated.progress_version == 2


def test_command_is_idempotent_but_rejects_different_operation(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    store.command(task.id, "cancelled", request_id="cancel-1")
    assert store.command(task.id, "cancelled", request_id="cancel-1").state == "cancelled"
    with pytest.raises(TaskConflict):
        store.command(task.id, "paused", request_id="cancel-1")


def test_cancel_requires_reopen_and_reopen_does_not_start(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    cancelled = store.command(task.id, "cancelled", request_id="cancel-1")
    assert cancelled.state == "cancelled"
    reopened = store.command(task.id, "reopen", request_id="reopen-1")
    assert reopened.state == "paused"
    with pytest.raises(TaskConflict):
        store.command(task.id, "reopen", request_id="reopen-2")


def test_evidence_is_persistent_and_does_not_claim_verification(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    store.add_evidence(task.id, Evidence(
        kind="command", source="bash", summary="exit: 0", ok=True,
        limitations="does not prove the requested feature works",
    ))
    loaded = TaskStore(store.path).get(task.id)
    assert loaded.evidence[0].summary == "exit: 0"
    assert loaded.evidence[0].limitations
    assert loaded.state == "pending"


def test_events_are_monotonic_and_cursor_is_explicit(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    events = store.events(task.id, after=0)
    assert events and [item["seq"] for item in events] == sorted(item["seq"] for item in events)
    cursor = events[0]["seq"]
    later = store.events(task.id, after=cursor)
    assert all(item["seq"] > cursor for item in later)


def test_active_task_cannot_be_archived_or_deleted(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    store.command(task.id, "queued", request_id="start-1")
    with pytest.raises(TaskConflict):
        store.command(task.id, "archive", request_id="archive-1")
    with pytest.raises(TaskConflict):
        store.delete(task.id, expected_version=store.get(task.id).control_version)


def test_bad_persisted_json_fails_loudly(tmp_path):
    store = make_store(tmp_path)
    task = create(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE tasks SET steps_json='not-json' WHERE id=?", (task.id,))
        connection.commit()
    with pytest.raises(TaskValidationError):
        store.get(task.id)


def test_newer_database_version_is_rejected(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version={TASK_SCHEMA_VERSION + 1}")
        connection.commit()
    with pytest.raises(TaskValidationError):
        TaskStore(path)


def test_orphaned_run_is_paused_and_dispatching_is_unknown(tmp_path):
    store = make_store(tmp_path)
    item = create(store)
    store.command(item.id, "running", request_id="run-start")
    run = store.begin_run(item.id)
    operation_id = store.operation_begin(
        run_id=run["id"], tool_call_id="c1", tool_name="write_file",
        arguments={"path": "x", "content": "x"},
    )
    reopened = TaskStore(store.path)
    assert reopened.recover_interrupted() == [item.id]
    task = reopened.get(item.id)
    assert task.state == "paused"
    assert "未知操作" in task.wait_reason
    unfinished = reopened.unfinished_operations(item.id)
    assert [operation["id"] for operation in unfinished] == [operation_id]
    assert unfinished[0]["status"] == "unknown"


def test_interaction_answer_requires_the_answers_current_version(tmp_path):
    """A status change keeps the decision live, but an old version still fails.

    The earlier form of this test asserted that any ``control_version`` change
    made the pending decision permanently unanswerable. That was the defect
    it was written around: suspending a task with an approval open left the
    approval with no possible outcome (and no dismissal) until the task was
    deleted. Lifecycle transitions now carry the decision to the new version;
    what remains rejected is answering on behalf of a version the user is no
    longer looking at, and replaying an answer for a changed target.
    """
    store = make_store(tmp_path)
    item = create(store)
    pending = store.interaction_create(
        task_id=item.id, run_id=None, kind="approval",
        payload={"tool": "write_file", "arguments": {"path": "x"}},
        control_version=item.control_version,
    )
    store.command(item.id, "queued", request_id="control-change")
    with pytest.raises(TaskConflict):
        store.interaction_answer(pending, answer=True, expected_version=1)
    assert store.interaction(pending)["status"] == "pending"
    store.interaction_answer(pending, answer=True, expected_version=2)
    assert store.interaction(pending)["status"] == "answered"
