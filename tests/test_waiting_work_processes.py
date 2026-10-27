"""Waiting releases the model worker but retains authorized process ownership."""
import sys
import threading
from nexus.runtime.controller import TaskController
from nexus.task import TaskStore
from nexus.tools._jobs import JobRegistry
from test_work_execution import until


def test_main_turn_without_writer_does_not_wait_for_previous_turn_jobs():
    class PreviousTurnJobs:
        def list(self):
            raise AssertionError("unrelated turn must not wait on earlier jobs")

    controller = TaskController(None)
    controller._writers["project"] = "main:previous"
    controller.release_when_jobs_finish("main:current", PreviousTurnJobs())
    assert controller.owns_write("main:previous")


def test_waiting_process_keeps_writer_and_pause_waits_for_cleanup(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    registry = JobRegistry()
    job = registry.start([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path))
    entered, release = threading.Event(), threading.Event()
    real_close = registry.close_all
    def close():
        entered.set()
        release.wait(3)
        real_close()
    registry.close_all = close
    class Runner:
        _jobs = {}
        def __call__(self, task, cancel):
            if task.title == "waiting":
                self._jobs[task.id] = registry
                return "waiting_user"
            return "paused"
    runner = Runner()
    controller = TaskController(store, run=runner)
    controller.start()
    task = store.create(title="waiting", goal="wait", workspace=str(tmp_path))
    sibling = store.create(title="sibling", goal="write", workspace=str(tmp_path))
    pauser = None
    try:
        controller.submit(task.id, request_id="start")
        until(lambda: store.get(task.id).state == "waiting_user" and not controller.is_active(task.id))
        assert job.running
        controller.submit(sibling.id, request_id="second")
        assert sibling.id in controller.queued_ids()
        pauser = threading.Thread(target=lambda: controller.pause(task.id, request_id="pause"))
        pauser.start()
        assert entered.wait(3)
        assert store.get(task.id).state == "pausing"
        assert sibling.id in controller.queued_ids()
        release.set()
        pauser.join(3)
        assert not pauser.is_alive()
        until(lambda: store.get(task.id).state == "paused")
        assert not job.running
        until(lambda: store.get(sibling.id).state == "paused")
    finally:
        release.set()
        if pauser: pauser.join(3)
        controller.close()
        real_close()


def test_waiting_work_can_reclaim_its_own_running_process(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    jobs = JobRegistry()
    job = jobs.start([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path))
    calls = []
    class Runner:
        _jobs = {}
        def __call__(self, task, cancel):
            calls.append(task.id)
            if len(calls) == 1:
                self._jobs[task.id] = jobs
                return "waiting_user"
            assert job.running
            return "paused"
    controller = TaskController(store, run=Runner())
    controller.start()
    task = store.create(title="waiting", goal="wait", workspace=str(tmp_path))
    try:
        controller.submit(task.id, request_id="start")
        until(lambda: store.get(task.id).state == "waiting_user" and not controller.is_active(task.id))
        controller.resume(task.id, request_id="resume")
        until(lambda: len(calls) == 2)
        until(lambda: store.get(task.id).state == "paused" and not controller.is_active(task.id))
        assert not job.running
    finally:
        controller.close()
        jobs.close_all()
