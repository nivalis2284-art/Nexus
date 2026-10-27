"""Concurrency, write ownership and stop semantics of the converged runtime."""
import threading
import time
import pytest
from nexus.runtime.controller import TaskController, RunFnResult
from nexus.task import TaskStore, TaskConflict


def until(fn):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if fn():
            return
        time.sleep(.01)
    assert fn()


def test_three_readers_fourth_queues_and_pause_is_isolated(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    entered, release = [], threading.Event()
    def run(task, cancel):
        entered.append(task.id)
        while not cancel.is_set() and not release.wait(.01):
            pass
        return RunFnResult(status="paused")
    controller = TaskController(store, run=run)
    controller.start()
    tasks = [store.create(title=str(i), goal=str(i), workspace=str(tmp_path), read_only=True) for i in range(4)]
    try:
        for i, task in enumerate(tasks):
            controller.submit(task.id, request_id=str(i))
        until(lambda: len(entered) == 3)
        assert tasks[3].id in controller.queued_ids()
        controller.pause(tasks[0].id, request_id="stop-one")
        until(lambda: len(entered) == 4)
        assert store.get(tasks[1].id).state == "running"
        assert store.get(tasks[2].id).state == "running"
        assert store.get(tasks[0].id).state == "paused"
    finally:
        release.set()
        controller.close()


def test_project_writers_serialize_but_reader_runs(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    release = threading.Event()
    entered = []
    def run(task, cancel):
        entered.append(task.id)
        while not cancel.is_set() and not release.wait(.01):
            pass
        return "paused"
    controller = TaskController(store, run=run)
    controller.start()
    first = store.create(title="write1", goal="write", workspace=str(tmp_path))
    second = store.create(title="write2", goal="write", workspace=str(tmp_path))
    reader = store.create(title="read", goal="read", workspace=str(tmp_path), read_only=True)
    try:
        for task in (first, second, reader):
            controller.submit(task.id, request_id=task.id)
        until(lambda: len(entered) == 2)
        assert set(entered) == {first.id, reader.id}
        assert second.id in controller.queued_ids()
        controller.pause(first.id, request_id="pause-writer")
        until(lambda: second.id in entered)
    finally:
        release.set()
        controller.close()


def test_closed_runtime_never_persists_unowned_queue(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    controller = TaskController(store)
    task = store.create(title="work", goal="work", workspace=str(tmp_path))
    controller.close()
    with pytest.raises(TaskConflict, match="closed"):
        controller.submit(task.id, request_id="late")
    assert store.get(task.id).state == "pending"


def test_reader_upgrade_waits_before_effect_and_cancels_without_lock_leak(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    controller = TaskController(store)
    cancel = threading.Event()
    controller.acquire_write("first", str(tmp_path), threading.Event())
    observed = []
    def upgrade():
        try:
            controller.acquire_write("second", str(tmp_path), cancel)
            observed.append("effect")
        except TaskConflict:
            observed.append("cancelled")
    thread = threading.Thread(target=upgrade)
    thread.start()
    cancel.set()
    thread.join(2)
    assert observed == ["cancelled"]
    controller.release_write("first")
    controller.acquire_write("third", str(tmp_path), threading.Event())
    controller.release_write("third")
