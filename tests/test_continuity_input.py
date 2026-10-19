"""Input acceptance must not replace a pending task decision or run."""

from __future__ import annotations

import pytest

from nexus.task import TaskStore, TaskValidationError
from nexus.web.task_api import TaskHTTP


class Responder:
    def __init__(self):
        self.calls = []

    def __call__(self, handler, status, value):
        self.calls.append((handler, status, value))

    @property
    def value(self):
        return self.calls[-1][2]


class Controller:
    def __init__(self):
        self.submissions = []

    def submit(self, task_id, **kwargs):
        self.submissions.append((task_id, kwargs))
        raise AssertionError("this fixture should only exercise non-start paths")


def api_for(store: TaskStore, controller):
    response = Responder()
    home = type("Home", (), {"home": store.path.parent})()
    return TaskHTTP({"tasks": store, "task_controller": controller, "store": home}, response), response


def test_pending_input_is_saved_with_stable_seq_and_does_not_start(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="pending", goal="accept input", workspace=str(tmp_path))
    controller = Controller()
    api, response = api_for(store, controller)
    api.messages(None, task.id, {"text": "new context", "request_id": "input-1"})
    assert response.value["delivery"] == "pending"
    assert isinstance(response.value["seq"], int)
    first_seq = response.value["seq"]
    assert controller.submissions == []
    assert store.input_status(task.id)["pending_count"] == 1

    api.messages(None, task.id, {"text": "new context", "request_id": "input-1"})
    assert response.value["seq"] == first_seq
    assert controller.submissions == []
    assert len(store.messages(task.id)) == 1
    assert store.get(task.id).state == "pending"
    with pytest.raises(Exception, match="different message content"):
        api.messages(None, task.id, {"text": "changed context", "request_id": "input-1"})
    assert len(store.messages(task.id)) == 1


def test_waiting_input_keeps_original_interaction_answerable(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="approval", goal="wait", workspace=str(tmp_path))
    store.command(task.id, "running", request_id="running")
    store.command(task.id, "waiting_user", request_id="wait")
    interaction = store.interaction_create(
        task_id=task.id, run_id=None, kind="approval", payload={"tool": "write_file"},
        control_version=store.get(task.id).control_version,
    )
    controller = Controller()
    api, response = api_for(store, controller)
    waiting_version = store.get(task.id).control_version
    api.messages(None, task.id, {"text": "additional clarification", "request_id": "wait-input"})
    assert response.value["delivery"] == "pending"
    assert controller.submissions == []
    current = store.get(task.id)
    assert current.state == "waiting_user"
    assert current.control_version == waiting_version
    answered = store.interaction_answer(interaction, answer=True,
                                        expected_version=waiting_version)
    assert answered["id"] == interaction


def test_duplicate_request_id_cannot_be_reused_for_another_task(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    one = store.create(title="one", goal="one", workspace=str(tmp_path))
    two = store.create(title="two", goal="two", workspace=str(tmp_path))
    controller = Controller()
    api, _ = api_for(store, controller)
    api.messages(None, one.id, {"text": "first", "request_id": "shared"})
    with pytest.raises(Exception, match="another task"):
        api.messages(None, two.id, {"text": "second", "request_id": "shared"})
    assert not store.messages(two.id)


def test_empty_input_is_rejected_without_persisting(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="empty", goal="empty", workspace=str(tmp_path))
    api, _ = api_for(store, Controller())
    with pytest.raises(TaskValidationError):
        api.messages(None, task.id, {"text": "", "request_id": "empty"})
    assert store.messages(task.id) == []
