"""Completion and failure facts survive, and cannot be claimed by a tool exit."""

from __future__ import annotations

import threading

import pytest

from nexus.runtime.callbacks import TaskCallbacks
from nexus.task import Evidence, TaskConflict, TaskStore, TaskValidationError
from nexus.tools import build
from nexus.tools.registry import ToolResult


def make(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="gate", goal="deliver", workspace=str(tmp_path))
    store.command(task.id, "running", request_id="start")
    run = store.begin_run(task.id)
    return store, task, TaskCallbacks(store, task.id, run["id"], cancel=threading.Event())


def test_completion_requires_only_a_result_summary(tmp_path):
    store, task, callbacks = make(tmp_path)
    completed = callbacks.finish({"summary": "The requested draft is ready."})
    assert completed["state"] == "completed"
    assert store.get(task.id).work_summary == "The requested draft is ready."
    assert not store.get(task.id).steps
    assert not store.get(task.id).evidence


def test_optional_steps_and_limitations_survive_completion(tmp_path):
    store, task, callbacks = make(tmp_path)
    store.update_progress(task.id, expected_version=task.progress_version, changes={
        "steps": [{"content": "not checked", "status": "pending"}],
        "blocked": "old blocker",
    })
    store.add_evidence(task.id, Evidence(kind="verification", source="test",
                                         summary="one check", ok=True))
    result = callbacks.finish({"summary": "Draft ready", "unresolved": ["manual review"]})
    assert result["limitations"] == ["manual review"]
    restored = store.get(task.id)
    assert restored.state == "completed"
    assert restored.steps[0].status == "pending"
    assert restored.steps[0].verification == "unverified"
    assert not restored.blocked


def test_unknown_operation_is_preserved_without_forcing_completion_paperwork(tmp_path):
    store, task, callbacks = make(tmp_path)
    operation = store.operation_begin(run_id=callbacks.run_id, tool_call_id="o1",
                                      tool_name="write_file", arguments={})
    store.mark_dispatching_unknown(callbacks.run_id)
    result = callbacks.finish({"summary": "Investigation done; earlier write remains unconfirmed.",
                               "unresolved": ["Unconfirmed earlier write"]})
    assert result["state"] == "completed"
    assert store.get_operation(operation)["status"] == "unknown"


@pytest.mark.parametrize("state", ["paused", "cancelled"])
def test_stopped_task_cannot_be_marked_complete_by_old_execution(tmp_path, state):
    store, task, callbacks = make(tmp_path)
    store.command(task.id, state, request_id="owner-stop")
    with pytest.raises(TaskConflict):
        callbacks.finish({"summary": "late result"})
    assert store.get(task.id).state == state


def test_pending_user_decision_is_not_bypassed_by_completion(tmp_path):
    store, task, callbacks = make(tmp_path)
    store.interaction_create(task_id=task.id, run_id=callbacks.run_id, kind="approval",
                             payload={"tool": "write_file"}, control_version=store.get(task.id).control_version)
    with pytest.raises(TaskValidationError):
        callbacks.finish({"summary": "not authorized"})
    assert store.pending_interactions(task.id)


def test_repeated_failed_call_does_not_require_attempt_paperwork(tmp_path):
    store, task, callbacks = make(tmp_path)
    registry, context = build(root=tmp_path, task_callbacks=callbacks)
    for index in range(5):
        prepared = registry.prepare("write_file", {"path": "result.txt", "content": "x"}, context)
        call_id = f"failed-{index}"
        callbacks.prepare_dispatch(call_id, prepared, context)
        callbacks.after_dispatch(call_id, ToolResult(False, "temporary tool error"))
    assert store.get(task.id).state == "running"
    assert not store.unfinished_operations(task.id)


def test_attempt_is_persistent_and_reused_after_reopen(tmp_path):
    store, task, callbacks = make(tmp_path)
    callbacks.record_attempt({
        "tool": "bash", "error": "missing dependency", "premise": "same env",
        "attempt": "ran command", "new_information": ["module missing"],
        "excluded": ["path typo"], "next_reason": "inspect dependency",
    })
    restored = TaskStore(store.path).get(task.id)
    assert restored.decisions[-1]["excluded"] == ["path typo"]
