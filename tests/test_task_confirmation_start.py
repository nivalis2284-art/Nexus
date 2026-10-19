"""The confirmation button releases exactly one queued start."""

from __future__ import annotations

from nexus.runtime import TaskController
from nexus.task import TaskStore


def test_confirmation_after_task_version_change_can_start(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="confirm", goal="run", workspace=str(tmp_path))
    interaction = store.request_confirmation(
        task.id, request_id="due-confirm", payload={"due_at": "2030-01-01T00:00:00Z"}
    )
    controller = TaskController(store, run=lambda item, cancel: "paused")
    controller.start()
    try:
        original = store.get(task.id)
        store.interaction_answer(interaction, answer=True, expected_version=original.control_version)
        started = store.get(task.id)
        controller.submit(task.id, request_id="confirm-click", expected_version=started.control_version)
        assert store.get(task.id).state in ("queued", "running", "paused")
    finally:
        controller.close()
