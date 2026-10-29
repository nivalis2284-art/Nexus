"""Regression checks for task HTTP interaction ownership and kind."""

import pytest
import threading
from types import SimpleNamespace

from nexus.ling import LingInbox
from nexus.runtime.controller import TaskController
from nexus.runtime.conversation import history_for_model
from nexus.runtime.work import WorkOperations
from nexus.session import Message, SessionStore
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


@pytest.mark.parametrize("operation", ["archive", "unarchive", "delete"])
def test_work_record_changes_reject_busy_main_before_mutating(tmp_path, operation):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="done", goal="done", workspace=str(tmp_path))
    store.command(task.id, "running", request_id="start")
    store.command(task.id, "completed", request_id="finish")
    if operation == "unarchive":
        store.command(task.id, "archive", request_id="archive")
    before = store.get(task.id)
    busy = threading.Lock()
    work = WorkOperations({"tasks": store, "task_controller": TaskController(store),
                           "ling": SimpleNamespace(busy=busy)})
    with busy, pytest.raises(TaskConflict, match="主交流"):
        body = {"expected_version": before.control_version, "confirm": True,
                "request_id": "organize", "operation": operation}
        if operation == "delete":
            work.delete(task.id, body)
        else:
            work.command(task.id, body)
    assert store.get(task.id).to_dict() == before.to_dict()


def test_report_rewrite_does_not_overwrite_concurrent_append(tmp_path, monkeypatch):
    sessions = SessionStore(tmp_path)
    session = sessions.create("fake")
    sessions.append(session, Message("work_report", "event", {"report": {"task_id": "work"}}))
    work = WorkOperations({"sessions": sessions, "ling": SimpleNamespace(session_id=session.id)})
    loaded, release, appended = threading.Event(), threading.Event(), threading.Event()
    load = work._main_session
    errors = []

    def paused_load():
        value = load()
        loaded.set()
        assert release.wait(3)
        return value

    def rewrite():
        try:
            work._sync_reports("work", "hide")
        except Exception as error:
            errors.append(error)

    def append():
        sessions.append(session, Message("assistant", "new answer"))
        appended.set()

    monkeypatch.setattr(work, "_main_session", paused_load)
    writer, appender = threading.Thread(target=rewrite), threading.Thread(target=append)
    try:
        writer.start()
        assert loaded.wait(3)
        appender.start()
        assert not appended.wait(.05)
    finally:
        release.set()
        writer.join(3)
        if appender.ident is not None:
            appender.join(3)
    assert not errors and not writer.is_alive() and not appender.is_alive()
    restored = sessions.load(session.id)
    assert restored.items[0].data["hidden"] is True
    assert restored.items[-1].text == "new answer"


@pytest.mark.parametrize("cleanup", ["delete", "startup"])
def test_report_cleanup_preserves_uncompressed_requirements(tmp_path, cleanup):
    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create("fake")
    for message in [Message("user", "old goal"),
                    Message("assistant", "old answer"),
                    Message("work_report", "event", {"report": {"task_id": "gone"}}),
                    Message("user", "new requirement"),
                    Message("checkpoint", "summary", {"through": "2"})]:
        sessions.append(session, message)
    state = {"sessions": sessions, "ling": SimpleNamespace(session_id=session.id),
             "tasks": TaskStore(tmp_path / "tasks.sqlite3")}
    if cleanup == "delete":
        WorkOperations(state)._sync_reports("gone", "drop")
    else:
        LingInbox._prune_orphan_reports(SimpleNamespace(state=state, store=state["tasks"]), session)
    assert [m["content"] for m in history_for_model(sessions.load(session.id))] == [
        "summary", "new requirement"]
