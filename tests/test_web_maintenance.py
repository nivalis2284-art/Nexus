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


def test_wrong_task_cannot_answer_interaction(interactions):
    api, store, owner, other = interactions
    interaction = store.interaction_create(task_id=owner.id, run_id=None, kind="approval",
                                           payload={}, control_version=owner.control_version)
    with pytest.raises(TaskConflict):
        api.answer(None, other.id, interaction, {"answer": True, "expected_version": owner.control_version})
    assert store.interaction(interaction)["status"] == "pending"


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
