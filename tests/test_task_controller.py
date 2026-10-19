"""Task runner is decoupled from HTTP and never overlaps two tasks."""

from __future__ import annotations

import threading
import time

import pytest

from nexus.runtime import TaskController
from nexus.task import TaskConflict, TaskStore


def make(tmp_path, run):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    controller = TaskController(store, run=run)
    controller.start()
    return store, controller


def task(store, title):
    return store.create(title=title, goal=title, workspace=str(store.path.parent))


def wait_state(store, task_id, states, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = store.get(task_id).state
        if value in states:
            return value
        time.sleep(0.02)
    return store.get(task_id).state


def test_task_runs_after_submit_without_a_client(tmp_path):
    started = threading.Event()
    def run(task, cancel):
        started.set()
        return "complete"
    store, controller = make(tmp_path, run)
    try:
        item = task(store, "independent")
        controller.submit(item.id, request_id="startindependent")
        assert started.wait(3)
        assert wait_state(store, item.id, {"paused"}) == "paused"
        assert store.get(item.id).state != "completed"
    finally:
        controller.close()


def test_only_one_task_runs_and_other_waits(tmp_path):
    first_entered = threading.Event()
    release = threading.Event()
    order = []
    def run(item, cancel):
        order.append(item.id)
        first_entered.set()
        release.wait(3)
        return "complete"
    store, controller = make(tmp_path, run)
    try:
        first, second = task(store, "first"), task(store, "second")
        controller.submit(first.id, request_id="firststart")
        controller.submit(second.id, request_id="secondstart")
        assert first_entered.wait(3)
        time.sleep(0.1)
        assert controller.active_task == first.id
        assert second.id in controller.queued_ids()
        release.set()
        deadline = time.monotonic() + 3
        while len(order) < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert order == [first.id, second.id]
    finally:
        release.set()
        controller.close()


def test_pause_stops_worker_before_marking_paused(tmp_path):
    entered = threading.Event()
    stopped = threading.Event()
    finished = threading.Event()
    def run(item, cancel):
        entered.set()
        cancel.wait(3)
        stopped.set()
        time.sleep(0.1)
        finished.set()
        return "paused"
    store, controller = make(tmp_path, run)
    try:
        item = task(store, "pause")
        controller.submit(item.id, request_id="pausestart")
        assert entered.wait(3)
        result = controller.pause(item.id, request_id="pausestop")
        assert result.state == "pausing"
        assert stopped.wait(3)
        assert wait_state(store, item.id, {"paused"}) == "paused"
        assert finished.is_set()
    finally:
        controller.close()


def test_waiting_user_releases_worker_but_not_automatic_resume(tmp_path):
    def run(item, cancel):
        return "waiting_user"
    store, controller = make(tmp_path, run)
    try:
        item = task(store, "question")
        controller.submit(item.id, request_id="waitstart")
        assert wait_state(store, item.id, {"waiting_user"}) == "waiting_user"
        time.sleep(0.1)
        assert controller.active_task is None
        other = task(store, "other")
        controller.submit(other.id, request_id="otherstart")
        assert wait_state(store, other.id, {"waiting_user"}) == "waiting_user"
    finally:
        controller.close()


def test_runner_failure_is_visible_and_task_is_not_silently_completed(tmp_path):
    def run(item, cancel):
        raise RuntimeError("provider unavailable")
    store, controller = make(tmp_path, run)
    try:
        item = task(store, "failure")
        controller.submit(item.id, request_id="fairstart")
        assert wait_state(store, item.id, {"paused"}) == "paused"
        messages = store.messages(item.id)
        assert any("provider unavailable" in message["text"] for message in messages)
        assert store.get(item.id).state != "completed"
    finally:
        controller.close()


def test_rejected_cancel_does_not_stop_an_active_task(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    def run(item, cancel):
        entered.set()
        release.wait(3)
        return "paused"
    store, controller = make(tmp_path, run)
    try:
        item = task(store, "reject cancel")
        controller.submit(item.id, request_id="cancel-start")
        assert entered.wait(3)
        with pytest.raises(TaskConflict):
            controller.cancel(item.id, request_id="stale-cancel", expected_version=0)
        time.sleep(0.1)
        assert controller.active_task == item.id
        assert store.get(item.id).state == "running"
        release.set()
        assert wait_state(store, item.id, {"paused"}) == "paused"
    finally:
        release.set()
        controller.close()


def test_rejected_pause_keeps_a_queued_task(tmp_path):
    entered = threading.Event()
    release = threading.Event()
    def run(item, cancel):
        entered.set()
        release.wait(3)
        return "paused"
    store, controller = make(tmp_path, run)
    try:
        first, queued = task(store, "active"), task(store, "queued")
        controller.submit(first.id, request_id="first-start")
        controller.submit(queued.id, request_id="queued-start")
        assert entered.wait(3)
        with pytest.raises(TaskConflict):
            controller.pause(queued.id, request_id="stale-pause", expected_version=0)
        assert queued.id in controller.queued_ids()
        assert store.get(queued.id).state == "queued"
        release.set()
    finally:
        release.set()
        controller.close()
