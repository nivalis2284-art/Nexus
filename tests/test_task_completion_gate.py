"""Completion and failure facts survive, and cannot be claimed by a tool exit."""

from __future__ import annotations

import threading

import pytest

from nexus.runtime.callbacks import TaskCallbacks
from nexus.task import Evidence, TaskStore, TaskValidationError


def make(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="gate", goal="deliver", workspace=str(tmp_path))
    store.command(task.id, "running", request_id="start")
    run = store.begin_run(task.id)
    return store, task, TaskCallbacks(store, task.id, run["id"], cancel=threading.Event())


def test_completion_requires_verified_or_explicit_limitations(tmp_path):
    store, task, callbacks = make(tmp_path)
    store.update_progress(task.id, expected_version=task.progress_version, changes={
        "steps": [{"content": "verified action", "status": "completed"}]
    })
    with pytest.raises(TaskValidationError):
        callbacks.finish({"summary": "looks good", "evidence_ids": [], "unresolved": []})
    store.add_evidence(task.id, Evidence(
        kind="verification", source="test", summary="target file matches", ok=True
    ))
    current = store.get(task.id)
    store.update_progress(task.id, expected_version=current.progress_version, changes={
        "steps": [{"content": "verified action", "status": "completed",
                   "verification": "verified", "evidence": ["1"]}]
    })
    completed = callbacks.finish({"summary": "tested", "evidence_ids": ["1"], "unresolved": []})
    assert completed["state"] == "completed"


def test_unverified_step_cannot_finish_even_with_a_limitation(tmp_path):
    store, task, callbacks = make(tmp_path)
    store.update_progress(task.id, expected_version=task.progress_version, changes={
        "steps": [{"content": "not checked", "status": "completed"}]
    })
    store.add_evidence(task.id, Evidence(kind="verification", source="test",
                                         summary="one check", ok=True))
    with pytest.raises(TaskValidationError):
        callbacks.finish({"summary": "partial", "unresolved": ["manual review"]})


def test_unknown_operation_blocks_completion(tmp_path):
    store, task, callbacks = make(tmp_path)
    store.update_progress(task.id, expected_version=task.progress_version, changes={
        "steps": [{"content": "action", "status": "completed",
                   "verification": "verified", "evidence": ["1"]}]
    })
    store.add_evidence(task.id, Evidence(kind="verification", source="test", summary="ok", ok=True))
    store.operation_begin(run_id=store.begin_run(task.id)["id"], tool_call_id="o1",
                          tool_name="write_file", arguments={})
    with pytest.raises(TaskValidationError):
        callbacks.finish({"summary": "done", "evidence_ids": [], "unresolved": []})


def test_attempt_is_persistent_and_reused_after_reopen(tmp_path):
    store, task, callbacks = make(tmp_path)
    callbacks.record_attempt({
        "tool": "bash", "error": "missing dependency", "premise": "same env",
        "attempt": "ran command", "new_information": ["module missing"],
        "excluded": ["path typo"], "next_reason": "inspect dependency",
    })
    restored = TaskStore(store.path).get(task.id)
    assert restored.decisions[-1]["excluded"] == ["path typo"]
