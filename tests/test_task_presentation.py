"""Display projections derived from existing task facts."""

from nexus.runtime import TaskController
from nexus.task import TaskStore
from nexus.task.presentation import task_presentation


def _task(store, tmp_path, title="task"):
    return store.create(title=title, goal=title, workspace=str(tmp_path))


def test_idle_task_has_stable_display_fields(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    controller = TaskController(store)
    task = _task(store, tmp_path)
    value = task_presentation(store, controller, task)
    assert value == {
        "state_label": "待执行",
        "status_detail": "尚未开始；点击开始后进入执行队列。",
        "progress_text": "暂无进展说明",
    }


def test_progress_is_the_saved_work_summary(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = _task(store, tmp_path)
    store.update_progress(task.id, expected_version=task.progress_version,
                          changes={"work_summary": "界面已接好，等待实测。"})
    assert task_presentation(store, TaskController(store), store.get(task.id))["progress_text"] == "界面已接好，等待实测。"


def test_waiting_label_comes_from_the_pending_action(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    controller = TaskController(store)
    task = _task(store, tmp_path)
    controller.submit(task.id, request_id="wait-start")
    task = store.get(task.id)
    store.interaction_create(task_id=task.id, run_id=None, kind="question",
                             payload={"questions": [{"question": "继续吗？"}]},
                             control_version=task.control_version)
    store.command(task.id, "waiting_user", request_id="wait-question")
    value = task_presentation(store, controller, store.get(task.id))
    assert value["state_label"] == "等待你回答问题"
    assert value["status_detail"].startswith("等待你回答问题")


def test_running_detail_uses_per_task_activity(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = _task(store, tmp_path)
    store.command(task.id, "running", request_id="start")
    running = store.get(task.id)

    class OnlySecondActive:
        active_task = "another"

        def is_active(self, task_id):
            return task_id == running.id

        def queued_ids(self):
            return []

    value = task_presentation(store, OnlySecondActive(), running)
    assert "正在使用当前执行通道。" in value["status_detail"]
    assert "schedule_label" not in value
