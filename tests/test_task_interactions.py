"""Durable approvals and answers release only their own exact operation."""

from __future__ import annotations

import threading

import pytest

from nexus.core.agent import ApprovalRequest
from nexus.runtime.callbacks import TaskCallbacks
from nexus.task import TaskConflict, TaskStore


def make(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="interaction", goal="decide", workspace=str(tmp_path))
    store.command(task.id, "running", request_id="run")
    run = store.begin_run(task.id)
    return store, task, TaskCallbacks(store, task.id, run["id"], cancel=threading.Event())


def test_approval_answers_only_the_same_exact_action(tmp_path):
    store, task, callbacks = make(tmp_path)
    approval = ApprovalRequest("write_file", {"path": "a", "content": "x"}, "a")
    with pytest.raises(Exception):
        callbacks.approve(approval)
    pending = store.pending_interactions(task.id)[0]
    store.interaction_answer(pending["id"], answer=True,
                             expected_version=store.get(task.id).control_version)
    callbacks.approve(approval)
    with pytest.raises(Exception):
        callbacks.approve(approval)


def test_answer_with_wrong_task_version_is_rejected(tmp_path):
    store, task, callbacks = make(tmp_path)
    with pytest.raises(Exception):
        callbacks.approve(ApprovalRequest("bash", {"command": "echo safe"}, "echo safe"))
    pending = store.pending_interactions(task.id)[0]
    store.command(task.id, "paused", request_id="change")
    with pytest.raises(TaskConflict):
        store.interaction_answer(pending["id"], answer=True, expected_version=1)


def test_callbacks_cannot_answer_another_tasks_interaction(tmp_path):
    store, task, callbacks = make(tmp_path)
    other = store.create(title="other", goal="other", workspace=str(tmp_path))
    pending = store.interaction_create(
        task_id=other.id, run_id=None, kind="approval", payload={},
        control_version=other.control_version,
    )
    with pytest.raises(TaskConflict):
        callbacks.pending_answer(pending, True)
    assert store.interaction(pending)["status"] == "pending"
    assert store.messages(other.id) == []


def test_callbacks_cannot_resolve_another_tasks_operation(tmp_path):
    store, task, callbacks = make(tmp_path)
    other = store.create(title="other", goal="other", workspace=str(tmp_path))
    store.command(other.id, "running", request_id="other-run")
    run = store.begin_run(other.id)
    operation = store.operation_begin(
        run_id=run["id"], tool_call_id="other-call", tool_name="write_file",
        arguments={"path": "x"},
    )
    with pytest.raises(TaskConflict):
        callbacks.resolve_operation({
            "operation_id": operation, "conclusion": "not_applied",
            "observation": "not this task's effect"
        })
    assert store.get_operation(operation)["status"] == "dispatching"
