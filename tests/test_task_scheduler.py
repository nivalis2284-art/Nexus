"""One-shot scheduling, confirmation, and no double dispatch."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from nexus.runtime.controller import TaskController
from nexus.runtime.scheduler import TaskScheduler
from nexus.task import TaskStore


def setup(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    ran = []
    controller = TaskController(store, run=lambda task, cancel: ran.append(task.id) or "paused")
    controller.start()
    return store, controller, TaskScheduler(store, controller), ran


def create(store, title):
    return store.create(title=title, goal=title, workspace=str(store.path.parent))


def test_due_auto_schedule_starts_once(tmp_path):
    store, controller, scheduler, ran = setup(tmp_path)
    try:
        item = create(store, "scheduled")
        store.set_schedule(item.id, due_at="2026-01-01T00:00:00+00:00",
                           timezone="UTC", mode="auto")
        scheduler.tick(datetime(2026, 1, 2, tzinfo=timezone.utc))
        assert scheduler.tick(datetime(2026, 1, 3, tzinfo=timezone.utc)) == []
        deadline = datetime.now(timezone.utc) + timedelta(seconds=3)
        while not ran and datetime.now(timezone.utc) < deadline:
            threading.Event().wait(0.02)
        assert ran == [item.id]
        assert store.schedule(item.id)["status"] == "consumed"
    finally:
        controller.close()
        scheduler.close()


def test_due_auto_schedule_holds_after_restart(tmp_path):
    store, controller, scheduler, ran = setup(tmp_path)
    try:
        item = create(store, "missed")
        store.set_schedule(item.id, due_at="2026-01-01T00:00:00+00:00",
                           timezone="UTC", mode="auto")
        scheduler.tick(
            datetime(2026, 1, 2, tzinfo=timezone.utc), startup=True,
            startup_cutoff="2026-01-01T12:00:00+00:00",
        )
        assert store.get(item.id).state == "waiting_user"
        assert store.pending_interactions(item.id)[0]["kind"] == "start_confirmation"
        assert ran == []
    finally:
        controller.close()
        scheduler.close()


def test_confirm_schedule_enters_wait_without_running(tmp_path):
    store, controller, scheduler, ran = setup(tmp_path)
    try:
        item = create(store, "confirm")
        store.set_schedule(item.id, due_at="2026-01-01T00:00:00+00:00",
                           timezone="UTC", mode="confirm")
        scheduler.tick(datetime(2026, 1, 2, tzinfo=timezone.utc))
        assert store.get(item.id).state == "waiting_user"
        assert store.pending_interactions(item.id)[0]["kind"] == "start_confirmation"
        assert ran == []
    finally:
        controller.close()
        scheduler.close()


def test_other_tasks_are_not_started_by_one_schedule(tmp_path):
    store, controller, scheduler, ran = setup(tmp_path)
    try:
        one, two = create(store, "one"), create(store, "two")
        store.set_schedule(one.id, due_at="2026-01-01T00:00:00+00:00", timezone="UTC", mode="auto")
        scheduler.tick(datetime(2026, 1, 2, tzinfo=timezone.utc))
        assert two.id not in store.due_schedules("2027-01-01T00:00:00+00:00")[0:]
        assert store.get(two.id).state == "pending"
    finally:
        controller.close()
        scheduler.close()


def test_failed_dispatch_can_retry_the_same_schedule(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    item = create(store, "retry dispatch")
    store.set_schedule(item.id, due_at="2026-01-01T00:00:00+00:00", timezone="UTC", mode="auto")
    calls = []

    class Controller:
        def submit(self, task_id, **kwargs):
            calls.append(task_id)
            if len(calls) == 1:
                raise RuntimeError("temporary dispatch failure")

    scheduler = TaskScheduler(store, Controller())
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    assert scheduler.tick(now) == []
    assert store.schedule(item.id)["status"] == "pending"
    assert scheduler.tick(now) == [item.id]
    assert store.schedule(item.id)["status"] == "consumed"
    assert scheduler.tick(now) == []
    assert calls == [item.id, item.id]
