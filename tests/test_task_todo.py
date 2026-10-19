"""One authoritative workflow: todo status and verification remain distinct."""

from __future__ import annotations

from nexus.task import TaskStore
from nexus.tools import build


def test_todo_completion_does_not_verify_a_task_step(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    item = store.create(title="todo", goal="work", workspace=str(tmp_path))
    class Callbacks:
        def write_todos(self, todos):
            store.update_progress(item.id, expected_version=store.get(item.id).progress_version, changes={"steps": [
                {"content": todo["content"], "status": todo["status"], "verification": "unverified"}
                for todo in todos
            ]})
    registry, context = build(root=tmp_path, task_callbacks=Callbacks())
    context.task_todos = Callbacks().write_todos
    result = registry.execute("todo_write", {"todos": [
        {"content": "verify externally", "status": "completed"}
    ]}, context)
    assert result.ok
    step = store.get(item.id).steps[0]
    assert step.status == "completed"
    assert step.verification == "unverified"
