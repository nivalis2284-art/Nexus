"""Task outcomes enter the existing wake queue without replaying old notices."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from nexus.automation.runtime import AutomationRuntime
from nexus.automation.store import RunStore
from nexus.responsibilities import ResponsibilityService
from nexus.runtime import TaskController
from nexus.runtime.callbacks import TaskCallbacks
from nexus.runtime.controller import RunFnResult
from nexus.task import TaskStore
from nexus.tools.registry import TaskSuspension


def task(store, tmp_path, title):
    return store.create(title=title, goal=title, workspace=str(tmp_path))


def wait_state(store, task_id, state, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.get(task_id).state == state:
            return
        time.sleep(0.01)
    raise AssertionError(f"task stayed {store.get(task_id).state!r}, expected {state!r}")


def metadata_controller():
    return SimpleNamespace(active_task=None, queued_ids=lambda: [])


def run_agent_pause(store, item, reason):
    store.command(item.id, "running", request_id=f"running-{item.id}")
    run = store.begin_run(item.id)
    callbacks = TaskCallbacks(store, item.id, run["id"], cancel=threading.Event())
    with pytest.raises(TaskSuspension):
        callbacks.pause({"reason": reason})


def test_feedback_filters_owner_stops_and_reports_real_outcomes(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    runs = RunStore(tmp_path / "automation.sqlite3")
    responsibilities = ResponsibilityService(tasks, metadata_controller())

    owner_pause = task(tasks, tmp_path, "owner pause")
    tasks.command(owner_pause.id, "paused", request_id="owner-pause")
    owner_cancel = task(tasks, tmp_path, "owner cancel")
    tasks.command(owner_cancel.id, "cancelled", request_id="owner-cancel")

    agent_pause = task(tasks, tmp_path, "model pause")
    run_agent_pause(tasks, agent_pause, "Need a source file from the owner.")
    responsibilities.link_work(agent_pause.id, report_requested=True)

    failed = task(tasks, tmp_path, "model failure")
    tasks.command(failed.id, "running", request_id="failure-running")
    tasks.command(failed.id, "paused", request_id="failure-pause",
                  reason="provider connection failed", source="error")

    completed = task(tasks, tmp_path, "completed without report request")
    tasks.command(completed.id, "running", request_id="complete-running")
    tasks.update_progress(completed.id, expected_version=1,
                          changes={"work_summary": "The artifact is ready."})
    tasks.command(completed.id, "completed", request_id="complete")

    waiting = task(tasks, tmp_path, "needs approval")
    tasks.command(waiting.id, "running", request_id="waiting-running")
    interaction_id = tasks.interaction_create(
        task_id=waiting.id, run_id=None, kind="approval",
        payload={"tool": "write_file", "reason": "Allow this write"},
        control_version=tasks.get(waiting.id).control_version,
    )

    result = runs.collect_work_feedback(tasks, report_for=responsibilities.work_metadata)
    assert result is not None
    assert result["notify"] is False
    reasons = {reason["task_id"]: reason for reason in result["reasons"]}
    assert set(reasons) == {agent_pause.id, failed.id, completed.id, waiting.id}
    assert owner_pause.id not in reasons and owner_cancel.id not in reasons

    assert reasons[agent_pause.id]["source"] == "agent"
    assert reasons[agent_pause.id]["state"] == "paused"
    assert "source file" in reasons[agent_pause.id]["summary"]
    assert reasons[failed.id]["source"] == "error"
    assert reasons[failed.id]["summary"] == "provider connection failed"
    assert reasons[completed.id]["state"] == "completed"
    assert reasons[completed.id]["summary"] == "The artifact is ready."
    assert reasons[waiting.id]["event"] == "interaction.pending"
    assert reasons[waiting.id]["interaction_id"] == interaction_id
    assert reasons[waiting.id]["state"] == "waiting_user"
    assert "report_requested" not in reasons[agent_pause.id]

    assert runs.feedback_cursor() == tasks.notifications()[-1]["id"]
    assert runs.collect_work_feedback(tasks, report_for=responsibilities.work_metadata) is None
    assert len(runs.runs()) == 1


def test_owner_and_responsibility_batch_pause_do_not_wake(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    runs = RunStore(tmp_path / "automation.sqlite3")
    controller = TaskController(tasks)
    service = ResponsibilityService(tasks, controller)
    responsibility = service.save(title="Maintenance", instruction="Check updates")

    linked = task(tasks, tmp_path, "linked work")
    service.link_work(linked.id, responsibility["id"])
    controller.submit(linked.id, request_id="linked-start")
    service.control(responsibility["id"], "pause")

    cancelled = task(tasks, tmp_path, "cancelled by owner")
    controller.cancel(cancelled.id, request_id="owner-cancel")

    assert tasks.get(linked.id).state == "paused"
    assert tasks.get(cancelled.id).state == "cancelled"
    assert runs.collect_work_feedback(tasks) is None
    assert runs.runs() == []
    assert runs.feedback_cursor() == tasks.notifications()[-1]["id"]
    controller.close()


def test_multiple_outcomes_merge_and_only_reported_completion_notifies(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    runs = RunStore(tmp_path / "automation.sqlite3")
    service = ResponsibilityService(tasks, metadata_controller())

    reported = task(tasks, tmp_path, "report completion")
    service.link_work(reported.id, report_requested=True)
    tasks.command(reported.id, "running", request_id="reported-running")
    tasks.update_progress(reported.id, expected_version=1,
                          changes={"work_summary": "All checks passed."})
    tasks.command(reported.id, "completed", request_id="reported-complete")

    ordinary = task(tasks, tmp_path, "ordinary completion")
    tasks.command(ordinary.id, "running", request_id="ordinary-running")
    tasks.command(ordinary.id, "completed", request_id="ordinary-complete")

    paused = task(tasks, tmp_path, "paused with report flag")
    service.link_work(paused.id, report_requested=True)
    run_agent_pause(tasks, paused, "Waiting for material.")

    waiting = task(tasks, tmp_path, "waiting with report flag")
    service.link_work(waiting.id, report_requested=True)
    tasks.command(waiting.id, "running", request_id="waiting-running")
    tasks.interaction_create(
        task_id=waiting.id, run_id=None, kind="question",
        payload={"questions": [{"question": "Which source?"}]},
        control_version=tasks.get(waiting.id).control_version,
    )

    result = runs.collect_work_feedback(tasks, report_for=service.work_metadata)
    assert result is not None and result["notify"] is True
    assert len(result["reasons"]) == 4
    assert len(runs.runs()) == 1
    flagged = [reason for reason in result["reasons"] if reason.get("report_requested")]
    assert [reason["task_id"] for reason in flagged] == [reported.id]


def test_feedback_cursor_baselines_once_survives_restart_and_ignores_read_state(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    old = task(tasks, tmp_path, "old result")
    tasks.command(old.id, "cancelled", request_id="old-cancel")
    old_head = tasks.notifications()[-1]["id"]

    path = tmp_path / "automation.sqlite3"
    runs = RunStore(path)
    assert runs.feedback_cursor() == old_head
    assert runs.collect_work_feedback(tasks) is None
    assert RunStore(path).feedback_cursor() == old_head

    ignored = task(tasks, tmp_path, "owner pause after baseline")
    tasks.command(ignored.id, "paused", request_id="owner-pause-after")
    assert runs.collect_work_feedback(tasks) is None
    paused_head = tasks.notifications()[-1]["id"]
    assert paused_head > old_head
    assert RunStore(path).feedback_cursor() == paused_head

    completed = task(tasks, tmp_path, "new completion")
    tasks.command(completed.id, "running", request_id="new-running")
    tasks.update_progress(completed.id, expected_version=1,
                          changes={"work_summary": "New result"})
    tasks.command(completed.id, "completed", request_id="new-complete")
    notice = next(item for item in tasks.notifications() if item["task_id"] == completed.id)
    tasks.ack_notification(notice["id"])
    first = runs.collect_work_feedback(tasks)
    assert first is not None
    assert [reason["task_id"] for reason in first["reasons"]] == [completed.id]

    restarted = RunStore(path)
    assert restarted.collect_work_feedback(tasks) is None
    restarted.update_run(first["id"], status="done")

    later = task(tasks, tmp_path, "later completion")
    tasks.command(later.id, "running", request_id="later-running")
    tasks.command(later.id, "completed", request_id="later-complete")
    second = restarted.collect_work_feedback(tasks)
    assert second is not None and second["id"] != first["id"]
    assert [reason["task_id"] for reason in second["reasons"]] == [later.id]
    assert len(restarted.runs()) == 2


def test_feedback_cursor_and_wake_roll_back_together(tmp_path, monkeypatch):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    runs = RunStore(tmp_path / "automation.sqlite3")
    baseline = runs.feedback_cursor()
    item = task(tasks, tmp_path, "atomic result")
    tasks.command(item.id, "running", request_id="atomic-running")
    tasks.command(item.id, "completed", request_id="atomic-complete")

    def fail(*_args, **_kwargs):
        raise RuntimeError("queue write failed")

    monkeypatch.setattr(runs, "_add_run", fail)
    with pytest.raises(RuntimeError, match="queue write failed"):
        runs.collect_work_feedback(tasks)
    assert runs.feedback_cursor() == baseline
    assert runs.runs() == []


@pytest.mark.parametrize("mode,expected", [("raised", "provider unavailable"),
                                           ("reported", "model_error")])
def test_controller_failures_persist_an_error_reason(tmp_path, mode, expected):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    runs = RunStore(tmp_path / "automation.sqlite3")

    def run(_task, _cancel):
        if mode == "raised":
            raise RuntimeError("provider unavailable")
        return RunFnResult(status="paused", wait_reason="model_error")

    controller = TaskController(tasks, run=run)
    controller.start()
    item = task(tasks, tmp_path, f"controller {mode}")
    controller.submit(item.id, request_id=f"start-{mode}")
    wait_state(tasks, item.id, "paused")
    controller.close()

    assert tasks.get(item.id).state == "paused"
    assert expected in tasks.get(item.id).wait_reason
    result = runs.collect_work_feedback(tasks)
    assert result is not None
    reason = next(reason for reason in result["reasons"] if reason["task_id"] == item.id)
    assert reason["source"] == "error"
    assert expected in reason["summary"]
    assert reason["state"] == "paused"


def test_automation_tick_collects_feedback_before_selecting_the_next_run(tmp_path):
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    state = {
        "store": SimpleNamespace(home=tmp_path),
        "tasks": tasks,
        "task_controller": SimpleNamespace(active_task=None, queued_ids=lambda: []),
    }
    runtime = AutomationRuntime(state, threading.Lock(), lambda: None, lambda *_args: None)
    item = task(tasks, tmp_path, "tick delivery")
    tasks.command(item.id, "running", request_id="tick-running")
    tasks.command(item.id, "completed", request_id="tick-complete")
    observed = {}
    runtime._execute_pending = lambda: observed.update(run=runtime.store.next_run())

    runtime.tick()

    assert observed["run"] is not None
    assert observed["run"]["reasons"][0]["task_id"] == item.id
    assert runtime.scheduler is None
