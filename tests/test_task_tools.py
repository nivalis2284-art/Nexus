"""The task tools cannot be used as an ordinary-chat completion shortcut."""

from __future__ import annotations

import pytest

from nexus.task import TaskStore
from nexus.tools import build


def test_task_tools_are_absent_from_ordinary_chat(tmp_path):
    registry, _ = build(root=tmp_path)
    assert registry.get("task_finish") is None
    assert registry.get("task_update") is None


def test_task_tool_dispatch_uses_shared_callbacks(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    item = store.create(title="tool", goal="do it", workspace=str(tmp_path))
    calls = []
    class Callbacks:
        def update_work(self, changes):
            calls.append(changes)
            return {"progress_version": 2}
    registry, context = build(root=tmp_path, task_callbacks=Callbacks())
    result = registry.execute("task_update", {"next_step": "test"}, context)
    assert result.ok
    assert calls == [{"next_step": "test"}]
    assert store.get(item.id).next_step == ""


def test_task_tools_without_callback_fail_loudly(tmp_path):
    registry, context = build(root=tmp_path, task_callbacks=object())
    result = registry.execute("task_update", {"next_step": "test"}, context)
    assert not result.ok
    assert "callback is not connected" in result.content
