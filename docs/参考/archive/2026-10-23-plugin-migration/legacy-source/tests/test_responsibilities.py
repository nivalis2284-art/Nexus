import json

import pytest

from nexus.responsibilities import ResponsibilityService
from nexus.runtime import TaskController
from nexus.task import TaskConflict, TaskStore, TaskValidationError


def make(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    return store, TaskController(store)


def task(store, tmp_path, title):
    return store.create(title=title, goal=title, workspace=str(tmp_path))


def test_records_preserve_origin_and_links_across_reopen(tmp_path):
    store, controller = make(tmp_path)
    service = ResponsibilityService(store, controller)
    record = service.save(title="  Health  ", instruction="  Walk daily  ",
                          summary="  Morning  ")
    assert record["title"] == "Health"
    assert record["instruction"] == "Walk daily"
    assert record["summary"] == "Morning"
    assert record["origin"] == "user"
    updated = service.save(id=record["id"], title="  Longevity  ", origin="ling")
    assert updated["title"] == "Longevity"
    assert updated["origin"] == "user"

    reopened_store = TaskStore(store.path)
    reopened = ResponsibilityService(reopened_store, controller)
    assert reopened.get(record["id"])["title"] == "Longevity"
    item = task(reopened_store, tmp_path, "Check sleep")
    assert reopened.work_metadata(item.id) == {
        "task_id": item.id, "responsibility_id": None, "report_requested": False,
    }
    metadata = reopened.link_work(item.id, record["id"], report_requested=True)
    assert metadata["responsibility_id"] == record["id"]
    assert metadata["report_requested"] is True
    listing = reopened.list()["responsibilities"]
    assert [linked["id"] for linked in listing[0]["tasks"]] == [item.id]


def test_pause_remembers_only_work_it_stops_and_resume_does_not_dispatch(tmp_path):
    store, controller = make(tmp_path)
    service = ResponsibilityService(store, controller)
    record = service.save(title="Maintenance", instruction="Watch feedback")
    linked = task(store, tmp_path, "Linked")
    user_paused = task(store, tmp_path, "Already paused")
    service.link_work(linked.id, record["id"])
    service.link_work(user_paused.id, record["id"])
    controller.submit(linked.id, request_id="linked-start")
    controller.submit(user_paused.id, request_id="user-start")
    controller.pause(user_paused.id, request_id="user-pause")

    paused = service.control(record["id"], "pause")
    assert paused["state"] == "paused"
    assert paused["paused_task_ids"] == [linked.id]
    assert store.get(linked.id).state == "paused"
    assert store.get(user_paused.id).state == "paused"
    assert service.task_paused(linked.id)

    resumed = service.control(id=record["id"], operation="resume")
    assert resumed["state"] == "active"
    assert controller.queued_ids() == []
    assert store.get(linked.id).state == "paused"
    assert service.resumable_task_ids() == [linked.id]
    context = json.loads(service.work_context())
    active_task = context["active"][0]["tasks"][0]
    assert set(active_task) == {"id", "title", "state", "work_summary", "wait_reason"}

    service.forget_paused_task(linked.id)
    assert service.resumable_task_ids() == []
    ended = service.control(record["id"], "end")
    assert ended["state"] == "ended"
    assert store.get(linked.id).state == "paused"
    assert controller.queued_ids() == []
    assert not service.task_paused(linked.id)


def test_new_responsibility_requires_title_and_instruction(tmp_path):
    store, controller = make(tmp_path)
    service = ResponsibilityService(store, controller)
    with pytest.raises(TaskValidationError):
        service.save(title=" ", instruction="Do it")
    with pytest.raises(TaskValidationError):
        service.save(title="Title", instruction=" ")

    record = service.save(title="Title", instruction="Do it")
    service.control(record["id"], "pause")
    item = task(store, tmp_path, "New work")
    with pytest.raises(TaskConflict):
        service.link_work(item.id, record["id"])
