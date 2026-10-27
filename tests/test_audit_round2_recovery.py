import sqlite3
import threading

from nexus.ling import LingInbox
from nexus.session import SessionStore
from nexus.task import TaskStore


def test_ling_worker_survives_inbox_read_error(tmp_path, monkeypatch):
    state = {"tasks": TaskStore(tmp_path / "tasks.db"),
             "sessions": SessionStore(tmp_path / "sessions")}
    original = LingInbox._next
    failed = threading.Event()
    executed = threading.Event()
    def next_item(self):
        if not failed.is_set():
            failed.set()
            raise sqlite3.OperationalError("temporary read error")
        return original(self)
    monkeypatch.setattr(LingInbox, "_next", next_item)
    inbox = LingInbox(state, lambda *args: executed.set(), threading.Lock())
    try:
        assert failed.wait(2)
        assert "temporary read error" in inbox.last_error
        inbox.enqueue("hello", [], [], "test-input")
        assert executed.wait(3)
        assert inbox._thread.is_alive()
    finally:
        inbox.close()


def test_pending_decision_cannot_start_after_pause(tmp_path):
    import pytest
    from nexus.runtime import TaskController
    from nexus.task import TaskConflict
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create(title="approval", goal="test", workspace=str(tmp_path))
    controller = TaskController(store)
    store.command(task.id, "running", request_id="run")
    interaction = store.interaction_create(task_id=task.id, run_id=None, kind="approval", payload={})
    store.command(task.id, "waiting_user", request_id="wait")
    controller.pause(task.id, request_id="pause")
    with pytest.raises(TaskConflict, match="pending interaction"):
        controller.submit(task.id, request_id="restart")
    assert store.pending_interactions(task.id)[0]["id"] == interaction
    assert controller.queued_ids() == []


def test_interaction_uses_current_version_and_rejects_stale_writer(tmp_path):
    import pytest
    from nexus.task import TaskConflict
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create(title="approval", goal="test", workspace=str(tmp_path))
    store.command(task.id, "running", request_id="run")
    with pytest.raises(TaskConflict, match="version changed"):
        store.interaction_create(task_id=task.id, run_id=None, kind="question", payload={},
                                 control_version=task.control_version)
    current = store.get(task.id)
    interaction = store.interaction_create(task_id=task.id, run_id=None, kind="question", payload={})
    store.interaction_answer(interaction, answer="yes", expected_version=current.control_version)
    assert not store.pending_interactions(task.id)
    store.command(task.id, "paused", request_id="pause")
    with pytest.raises(TaskConflict, match="suspended"):
        store.interaction_create(task_id=task.id, run_id="old-run", kind="approval", payload={})
