"""Queue ownership during shutdown and a resume racing final cleanup."""
import threading
import time
from nexus.runtime import TaskController
from nexus.task import TaskStore
from test_work_execution import until


def test_resume_during_cleanup_requeues_once(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    entered, release = threading.Event(), threading.Event()
    calls = []
    controller = None
    def run(task, cancel):
        calls.append(task.id)
        return "paused"
    controller = TaskController(store, run=run)
    real_stop = controller.stop_task_resources
    def stop(task_id):
        if len(calls) == 1:
            entered.set()
            release.wait(3)
        return real_stop(task_id)
    controller.stop_task_resources = stop
    task = store.create(title="one", goal="one", workspace=str(tmp_path))
    controller.start()
    try:
        controller.submit(task.id, request_id="start")
        assert entered.wait(3)
        assert store.get(task.id).state == "paused"
        controller.resume(task.id, request_id="resume")
        assert calls == [task.id]
        release.set()
        until(lambda: len(calls) == 2)
        until(lambda: not controller.is_active(task.id))
        assert controller.queued_ids() == []
        assert calls == [task.id, task.id]
    finally:
        release.set()
        controller.close()


def test_close_holds_queued_work_instead_of_orphaning_it(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    entered = threading.Event()
    def run(task, cancel):
        entered.set()
        cancel.wait(3)
        return "paused"
    controller = TaskController(store, run=run)
    controller.start()
    first = store.create(title="first", goal="first", workspace=str(tmp_path))
    queued = store.create(title="second", goal="second", workspace=str(tmp_path))
    controller.submit(first.id, request_id="first")
    assert entered.wait(3)
    controller.submit(queued.id, request_id="queued")
    controller.close()
    assert store.get(queued.id).state == "paused"
    assert not controller.queued_ids()
