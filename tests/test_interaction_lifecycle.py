"""A pending decision must stay usable or be visibly ended -- never orphaned.

Two real defects motivated these checks:

C: suspending a task while an approval was pending advanced
   ``control_version`` without moving the interaction, so the approval could
   never be answered (409) and could never be dismissed either.
F: an approval granted for one goal stayed reusable after the goal and
   workspace changed, because only ``tool + arguments`` were matched.

A lifecycle change (suspend, archive, restart recovery) keeps a decision
valid; a target change ends it and forces a fresh request.
"""

from __future__ import annotations

import threading

import pytest

from nexus.runtime import TaskController
from nexus.runtime.callbacks import TaskCallbacks
from nexus.task import TaskConflict, TaskStore


def make_store(tmp_path) -> TaskStore:
    return TaskStore(tmp_path / "tasks.sqlite3")


def approval_task(store: TaskStore, *, goal: str = "deploy to staging"):
    task = store.create(title="approve", goal=goal, workspace=str(store.path.parent))
    store.command(task.id, "running", request_id="run")
    return task


def pending_approval(store: TaskStore, task_id: str, arguments: dict | None = None) -> str:
    return store.interaction_create(
        task_id=task_id, run_id=None, kind="approval",
        payload={"tool": "bash", "arguments": arguments or {"cmd": "deploy", "target": "staging"}},
        control_version=store.get(task_id).control_version,
    )


def test_suspend_keeps_a_pending_approval_answerable(tmp_path):
    """Defect C: '挂起' used to strand the pending decision at an old version."""
    store = make_store(tmp_path)
    task = approval_task(store)
    interaction = pending_approval(store, task.id)
    store.command(task.id, "waiting_user", request_id="wait")

    suspended = store.command(task.id, "paused", request_id="suspend")
    assert suspended.state == "paused"
    # The decision is still the one live decision and it moved with the task.
    pending = store.pending_interactions(task.id)
    assert [item["id"] for item in pending] == [interaction]
    assert pending[0]["control_version"] == suspended.control_version

    answered = store.interaction_answer(
        interaction, answer=True, expected_version=suspended.control_version,
    )
    assert answered["id"] == interaction
    assert store.interaction(interaction)["status"] == "answered"


def test_answering_after_suspend_lets_the_task_continue_to_completion(tmp_path):
    """The approved action is still the authorization for the resumed run."""
    store = make_store(tmp_path)
    task = approval_task(store)
    arguments = {"cmd": "deploy", "target": "staging"}
    interaction = pending_approval(store, task.id, arguments)
    store.command(task.id, "waiting_user", request_id="wait")
    suspended = store.command(task.id, "paused", request_id="suspend")

    store.interaction_answer(interaction, answer=True, expected_version=suspended.control_version)

    # The saved answer is reusable for the exact same call while the target holds.
    approved = store.answered_approval(task.id, tool="bash", arguments=arguments)
    assert approved is not None and approved["answer"] is True
    store.consume_interaction(approved["id"])

    # The task can be resumed and finished normally afterwards.
    store.command(task.id, "queued", request_id="resume")
    store.command(task.id, "running", request_id="claim")
    finished = store.command(task.id, "completed", request_id="finish")
    assert finished.state == "completed"
    assert not store.pending_interactions(task.id)


def test_archive_and_unarchive_keep_a_pending_approval_answerable(tmp_path):
    """Defect C via the archive entry point named in the report."""
    store = make_store(tmp_path)
    task = approval_task(store)
    interaction = pending_approval(store, task.id)
    store.command(task.id, "waiting_user", request_id="wait")
    store.command(task.id, "paused", request_id="suspend")

    archived = store.command(task.id, "archive", request_id="archive")
    assert archived.archived is True
    pending = store.pending_interactions(task.id)
    assert [item["id"] for item in pending] == [interaction]
    assert pending[0]["control_version"] == store.get(task.id).control_version

    restored = store.command(task.id, "unarchive", request_id="unarchive")
    assert restored.archived is False
    answered = store.interaction_answer(
        interaction, answer=True, expected_version=restored.control_version,
    )
    assert answered["id"] == interaction


def test_restart_recovery_keeps_a_pending_approval_answerable(tmp_path):
    """recover_interrupted also advances the version; it must not orphan either."""
    store = make_store(tmp_path)
    task = approval_task(store)
    run = store.begin_run(task.id)
    interaction = pending_approval(store, task.id)
    assert run["id"]

    reopened = TaskStore(store.path)
    assert reopened.recover_interrupted() == [task.id]
    recovered = reopened.get(task.id)
    assert recovered.state == "paused"
    pending = reopened.pending_interactions(task.id)
    assert [item["id"] for item in pending] == [interaction]
    assert pending[0]["control_version"] == recovered.control_version
    reopened.interaction_answer(interaction, answer=True, expected_version=recovered.control_version)


def test_notification_of_a_still_pending_decision_is_not_reported_expired(tmp_path):
    """The UI reads staleness from the notification, so it must agree."""
    store = make_store(tmp_path)
    task = approval_task(store)
    interaction = pending_approval(store, task.id)
    store.command(task.id, "waiting_user", request_id="wait")
    store.command(task.id, "paused", request_id="suspend")

    notice = next(item for item in store.notifications() if item["interaction_id"] == interaction)
    assert notice["interaction_status"] == "pending"
    assert notice["interaction_expired"] is False
    assert notice["control_version"] == store.get(task.id).control_version


def test_changed_goal_and_workspace_do_not_reuse_the_old_approval(tmp_path):
    """Defect F: an approval for staging must not authorize production work."""
    store = make_store(tmp_path)
    task = approval_task(store, goal="deploy to staging")
    arguments = {"cmd": "deploy", "target": "staging"}
    interaction = pending_approval(store, task.id, arguments)
    store.interaction_answer(
        interaction, answer=True, expected_version=store.get(task.id).control_version,
    )
    assert store.answered_approval(task.id, tool="bash", arguments=arguments) is not None

    changed = store.update_task(
        task.id, expected_version=store.get(task.id).control_version,
        changes={"goal": "deploy to production", "workspace": r"C:\prod"},
    )
    assert changed.goal == "deploy to production"

    assert store.answered_approval(task.id, tool="bash", arguments=arguments) is None


def test_changing_only_the_workspace_also_ends_the_old_approval(tmp_path):
    """Workspace alone changes the effect of the same tool call."""
    store = make_store(tmp_path)
    task = approval_task(store)
    arguments = {"cmd": "deploy", "target": "staging"}
    interaction = pending_approval(store, task.id, arguments)
    store.interaction_answer(
        interaction, answer=True, expected_version=store.get(task.id).control_version,
    )

    store.update_task(
        task.id, expected_version=store.get(task.id).control_version,
        changes={"workspace": str(tmp_path / "other")},
    )
    assert store.answered_approval(task.id, tool="bash", arguments=arguments) is None


def test_a_target_change_ends_a_still_pending_decision_visibly(tmp_path):
    """A pending decision cannot survive a target change, and must not linger."""
    store = make_store(tmp_path)
    task = approval_task(store)
    interaction = pending_approval(store, task.id)
    store.command(task.id, "waiting_user", request_id="wait")
    store.command(task.id, "paused", request_id="suspend")

    store.update_task(
        task.id, expected_version=store.get(task.id).control_version,
        changes={"goal": "deploy to production"},
    )
    assert store.pending_interactions(task.id) == []
    assert store.interaction(interaction)["status"] == "expired"
    with pytest.raises(TaskConflict, match="request approval again"):
        store.interaction_answer(interaction, answer=True)

    notice = next(item for item in store.notifications() if item["interaction_id"] == interaction)
    assert notice["interaction_expired"] is True
    assert notice["stale_reason"]


def test_unchanged_target_still_reuses_the_approval(tmp_path):
    """Guards against over-fixing: an unrelated edit must not drop the answer."""
    store = make_store(tmp_path)
    task = approval_task(store)
    arguments = {"cmd": "deploy", "target": "staging"}
    interaction = pending_approval(store, task.id, arguments)
    store.interaction_answer(
        interaction, answer=True, expected_version=store.get(task.id).control_version,
    )

    store.update_task(
        task.id, expected_version=store.get(task.id).control_version,
        changes={"title": "renamed", "constraints": "keep as is"},
    )
    approved = store.answered_approval(task.id, tool="bash", arguments=arguments)
    assert approved is not None and approved["answer"] is True


def test_an_unrelated_action_still_needs_its_own_approval(tmp_path):
    """The scope check must not widen what a saved answer covers."""
    store = make_store(tmp_path)
    task = approval_task(store)
    interaction = pending_approval(store, task.id, {"cmd": "deploy", "target": "staging"})
    store.interaction_answer(
        interaction, answer=True, expected_version=store.get(task.id).control_version,
    )

    assert store.answered_approval(
        task.id, tool="bash", arguments={"cmd": "deploy", "target": "production"},
    ) is None


def test_a_pending_decision_cannot_be_answered_twice(tmp_path):
    """Existing behaviour: one decision releases exactly one answer."""
    store = make_store(tmp_path)
    task = approval_task(store)
    interaction = pending_approval(store, task.id)
    store.command(task.id, "waiting_user", request_id="wait")

    store.interaction_answer(
        interaction, answer=True, expected_version=store.get(task.id).control_version,
    )
    with pytest.raises(TaskConflict, match="already been answered"):
        store.interaction_answer(interaction, answer=False)


def test_a_suspended_task_and_owner_can_still_answer_through_callbacks(tmp_path):
    """The runtime path must also reach the suspended task's decision."""
    store = make_store(tmp_path)
    task = approval_task(store)
    run = store.begin_run(task.id)
    callbacks = TaskCallbacks(store, task.id, run["id"], cancel=threading.Event())
    from nexus.core.agent import ApprovalRequest

    with pytest.raises(Exception):
        callbacks.approve(ApprovalRequest("bash", {"cmd": "deploy"}, "deploy"))
    pending = store.pending_interactions(task.id)[0]
    store.command(task.id, "paused", request_id="suspend")

    # The HTTP/browser path passes the task's current version.
    callbacks.pending_answer(pending["id"], True)
    assert store.interaction(pending["id"])["status"] == "answered"


def test_resuming_still_requires_answering_the_pending_decision(tmp_path):
    """Existing guard: suspending must not become a way around a decision."""
    store = make_store(tmp_path)
    controller = TaskController(store, run=lambda item, cancel: "paused")
    try:
        task = approval_task(store)
        pending_approval(store, task.id)
        store.command(task.id, "waiting_user", request_id="wait")
        with pytest.raises(TaskConflict, match="pending interaction"):
            controller.submit(task.id, request_id="resume")
    finally:
        controller.close()
