"""Regression checks for task HTTP interaction ownership and kind."""

import pytest

from nexus.task import TaskConflict, TaskStore, TaskValidationError
from nexus.web.task_api import TaskHTTP


@pytest.fixture
def interactions(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    owner = store.create(title="owner", goal="owner", workspace=str(tmp_path))
    other = store.create(title="other", goal="other", workspace=str(tmp_path))
    api = TaskHTTP({"tasks": store}, lambda *args: None)
    return api, store, owner, other


@pytest.mark.parametrize("endpoint,kind", [
    ("answer", "approval"), ("start_confirmation", "start_confirmation"),
])
def test_wrong_task_cannot_answer_interaction(interactions, endpoint, kind):
    api, store, owner, other = interactions
    interaction = store.interaction_create(
        task_id=owner.id, run_id=None, kind=kind, payload={},
        control_version=owner.control_version,
    )
    with pytest.raises(TaskConflict):
        getattr(api, endpoint)(None, other.id, interaction, {
            "answer": True, "expected_version": owner.control_version,
        })
    assert store.interaction(interaction)["status"] == "pending"
    assert store.messages(owner.id) == []


@pytest.mark.parametrize("endpoint,kind", [
    ("answer", "start_confirmation"), ("start_confirmation", "approval"),
])
def test_wrong_endpoint_cannot_answer_interaction(interactions, endpoint, kind):
    api, store, owner, _ = interactions
    interaction = store.interaction_create(
        task_id=owner.id, run_id=None, kind=kind, payload={},
        control_version=owner.control_version,
    )
    with pytest.raises(TaskConflict):
        getattr(api, endpoint)(None, owner.id, interaction, {
            "answer": True, "expected_version": owner.control_version,
        })
    assert store.interaction(interaction)["status"] == "pending"
    assert store.messages(owner.id) == []


def test_short_control_can_select_task_by_its_unique_title(interactions):
    api, store, owner, _ = interactions
    calls = []

    class Controller:
        def resume(self, task_id, **kwargs):
            calls.append(task_id)
            return store.get(task_id)

    api.state["task_controller"] = Controller()
    api.task_control(None, {"text": f"继续{owner.title}", "request_id": "title-resume"})
    assert calls == [owner.id]


def test_short_control_rejects_ambiguous_titles(interactions):
    api, store, owner, _ = interactions
    store.create(title=owner.title, goal="duplicate", workspace=owner.workspace)
    with pytest.raises(TaskConflict, match="exactly one task"):
        api.task_control(None, {"text": f"继续{owner.title}", "request_id": "ambiguous"})


@pytest.mark.parametrize("steps", [None, [{"content": "step", "status": "invalid"}]])
def test_invalid_steps_do_not_partially_apply_configuration(interactions, steps):
    api, store, owner, _ = interactions
    with pytest.raises(TaskValidationError):
        api.update(None, owner.id, {
            "expected_version": owner.control_version,
            "title": "should not be saved", "steps": steps,
        })
    unchanged = store.get(owner.id)
    assert unchanged.title == owner.title
    assert unchanged.control_version == owner.control_version
    assert unchanged.progress_version == owner.progress_version


def test_configuration_and_steps_apply_together(interactions):
    api, store, owner, _ = interactions
    api.update(None, owner.id, {
        "expected_version": owner.control_version,
        "title": "updated", "steps": [{"content": "verify"}],
    })
    updated = store.get(owner.id)
    assert updated.title == "updated"
    assert updated.steps[0].content == "verify"
