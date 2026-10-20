"""A resume that races a still-active worker must not strand the task.

The single controller worker owns a task until it returns. A ``pause`` whose
wait expires leaves that worker running, so a ``resume`` arriving then cannot be
queued directly. These tests pin that the task is re-queued when the worker
releases it, without ever running one task twice at the same time.

Synchronisation uses events and a deliberately shortened pause wait, so no test
depends on a fragile wall-clock window.
"""

from __future__ import annotations

import threading
import time

import pytest

from nexus.runtime import TaskController
from nexus.task import TaskStore, TaskValidationError


def make(tmp_path, run):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    controller = TaskController(store, run=run)
    controller.start()
    return store, controller


def task(store, title):
    return store.create(title=title, goal=title, workspace=str(store.path.parent))


def wait_state(store, task_id, states, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = store.get(task_id).state
        if value in states:
            return value
        time.sleep(0.01)
    return store.get(task_id).state


def expire_pause_wait(controller, wait=0.2):
    """Shrink the pause wait so it expires while the fake run is blocked.

    The real ``_wait_inactive`` loop still runs; only its deadline is short,
    which makes the defect window deterministic instead of timing-dependent.
    """
    real = controller._wait_inactive
    controller._wait_inactive = lambda task_id: real(task_id, timeout=wait)


class Gate:
    """Counts overlapping invocations and records execution order."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current = 0
        self.max_concurrent = 0
        self.calls: list[str] = []

    def enter(self, task_id):
        with self._lock:
            self.current += 1
            self.max_concurrent = max(self.max_concurrent, self.current)
            self.calls.append(task_id)

    def leave(self):
        with self._lock:
            self.current -= 1

    @property
    def count(self):
        with self._lock:
            return len(self.calls)


def test_resume_after_pause_timeout_is_executed_when_worker_exits(tmp_path):
    """Core regression: worker outlives pause, resume must not be stranded."""
    gate = Gate()
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    invocations: list[str] = []
    invocations_lock = threading.Lock()

    def run(item, cancel):
        gate.enter(item.id)
        try:
            with invocations_lock:
                invocations.append(item.id)
                first = len(invocations) == 1
            if first:
                first_started.set()
                # A slow run that keeps going after cancellation: this is the
                # window in which pause() gives up waiting for the worker.
                release.wait(5)
                return "paused"
            second_started.set()
            return "complete"
        finally:
            gate.leave()

    store, controller = make(tmp_path, run)
    try:
        expire_pause_wait(controller)
        item = task(store, "stranded")
        controller.submit(item.id, request_id="strand-start")
        assert first_started.wait(5)

        controller.pause(item.id, request_id="strand-pause")
        # The wait expired, so the worker still owns the task.
        assert controller.active_task == item.id
        assert store.get(item.id).state == "paused"

        resumed = controller.resume(item.id, request_id="strand-resume")
        assert resumed.state == "queued"
        # Nothing may start while the original run is still in flight.
        assert not second_started.is_set()
        assert gate.max_concurrent == 1

        release.set()
        assert second_started.wait(5), "resumed task was never executed"
        assert invocations == [item.id, item.id]
        assert gate.max_concurrent == 1
        assert wait_state(store, item.id, {"paused"}) == "paused"
    finally:
        release.set()
        controller.close()


def test_pause_completing_within_timeout_then_resume_runs_normally(tmp_path):
    """The ordinary path must keep working: no regression."""
    gate = Gate()
    entered = threading.Event()
    resumed_run = threading.Event()
    invocations: list[str] = []
    invocations_lock = threading.Lock()

    def run(item, cancel):
        gate.enter(item.id)
        try:
            with invocations_lock:
                invocations.append(item.id)
                first = len(invocations) == 1
            if first:
                entered.set()
                cancel.wait(5)  # honors the pause request promptly
                return "paused"
            resumed_run.set()
            return "complete"
        finally:
            gate.leave()

    store, controller = make(tmp_path, run)
    try:
        item = task(store, "normal")
        controller.submit(item.id, request_id="normal-start")
        assert entered.wait(5)

        paused = controller.pause(item.id, request_id="normal-pause")
        assert paused.state == "pausing"
        assert wait_state(store, item.id, {"paused"}) == "paused"
        assert controller.active_task is None

        controller.resume(item.id, request_id="normal-resume")
        assert resumed_run.wait(5), "resumed task was never executed"
        assert gate.max_concurrent == 1
        assert invocations == [item.id, item.id]
        assert wait_state(store, item.id, {"paused"}) == "paused"
    finally:
        controller.close()


def test_resumed_task_never_runs_concurrently_with_its_previous_run(tmp_path):
    """Repeated start requests during the release window stay serial."""
    gate = Gate()
    release = threading.Event()
    second_started = threading.Event()
    invocations: list[str] = []
    invocations_lock = threading.Lock()

    def run(item, cancel):
        gate.enter(item.id)
        try:
            with invocations_lock:
                invocations.append(item.id)
                first = len(invocations) == 1
            if first:
                release.wait(5)
                return "paused"
            second_started.set()
            return "complete"
        finally:
            gate.leave()

    store, controller = make(tmp_path, run)
    try:
        expire_pause_wait(controller)
        item = task(store, "serial")
        controller.submit(item.id, request_id="serial-start")
        deadline = time.monotonic() + 5
        while gate.count == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gate.count == 1
        controller.pause(item.id, request_id="serial-pause")
        assert controller.active_task == item.id

        controller.resume(item.id, request_id="serial-resume")
        # More start requests while the first run is still unwinding may only
        # refresh the same re-queue intent; none may begin a second execution.
        for index in range(3):
            controller.resume(item.id, request_id=f"serial-extra-resume-{index}")
        assert not second_started.is_set()
        # Not queued yet on purpose: the owning worker re-queues it exactly once
        # when it exits, so a second worker can never pick it up concurrently.
        assert item.id not in controller.queued_ids()
        assert gate.max_concurrent == 1

        release.set()
        assert second_started.wait(5), "resumed task was never executed"
        assert invocations == [item.id, item.id]
        assert gate.max_concurrent == 1
    finally:
        release.set()
        controller.close()


def test_closed_controller_accepts_no_new_work(tmp_path):
    started = threading.Event()

    def run(item, cancel):
        started.set()
        return "complete"

    store, controller = make(tmp_path, run)
    controller.close()
    try:
        with pytest.raises(TaskValidationError):
            controller.start()

        late = task(store, "late")
        controller.submit(late.id, request_id="late-start")
        assert controller.queued_ids() == []
        assert controller.active_task is None
        assert started.wait(0.3) is False

        controller.resume(late.id, request_id="late-resume")
        assert controller.queued_ids() == []
        assert started.wait(0.3) is False
    finally:
        controller.close()


def test_duplicate_resume_request_does_not_enqueue_twice(tmp_path):
    gate = Gate()
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()

    def run(item, cancel):
        gate.enter(item.id)
        try:
            entered.set()
            if gate.count == 1:
                cancel.wait(5)
                stopped.set()
                return "paused"
            return "complete"
        finally:
            gate.leave()

    store, controller = make(tmp_path, run)
    try:
        item = task(store, "idempotent")
        controller.submit(item.id, request_id="idem-start")
        assert entered.wait(5)

        # A resume request made while the worker owns the task is recorded once,
        # not duplicated by a retry with the same request id.
        controller.pause(item.id, request_id="idem-pause")
        assert stopped.wait(5)
        assert wait_state(store, item.id, {"paused"}) == "paused"

        first = controller.resume(item.id, request_id="idem-resume")
        second = controller.resume(item.id, request_id="idem-resume")
        assert first.state == "queued" and second.state == "queued"
        deadline = time.monotonic() + 5
        while gate.count < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert gate.count == 2, "the resumed task must run exactly once"
        assert gate.max_concurrent == 1
        assert wait_state(store, item.id, {"paused"}) == "paused"
    finally:
        release.set()
        controller.close()
