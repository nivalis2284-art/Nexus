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
        "schedule_label": "未安排定时。",
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


def test_start_confirmation_after_consumed_schedule_still_waits(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = _task(store, tmp_path)
    store.set_schedule(task.id, due_at="2030-01-01T00:00:00+00:00",
                       timezone="UTC", mode="confirm")
    store.request_confirmation(task.id, request_id="confirm-1", payload={})
    store.complete_schedule(task.id)
    value = task_presentation(store, TaskController(store), store.get(task.id))
    assert "等待确认" in value["schedule_label"]
    assert "已执行" not in value["schedule_label"]


def test_schedule_is_shown_in_its_zone_not_utc_clock(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = _task(store, tmp_path)
    store.set_schedule(task.id, due_at="2030-01-01T09:00:00+09:00",
                       timezone="Asia/Tokyo", mode="auto")
    value = task_presentation(store, TaskController(store), store.get(task.id))
    assert "2030-01-01 09:00（Asia/Tokyo）" in value["schedule_label"]
    assert "2030-01-01 00:00" not in value["schedule_label"]


def test_utc_schedule_keeps_the_utc_clock(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = _task(store, tmp_path)
    store.set_schedule(task.id, due_at="2030-01-01T00:00:00+00:00",
                       timezone="UTC", mode="confirm")
    value = task_presentation(store, TaskController(store), store.get(task.id))
    assert "2030-01-01 00:00（UTC）" in value["schedule_label"]


def test_fixed_offset_schedule_keeps_its_labeled_clock(tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = _task(store, tmp_path)
    store.set_schedule(task.id, due_at="2030-01-01T09:00:00+09:00",
                       timezone="UTC+09:00", mode="auto")
    value = task_presentation(store, TaskController(store), store.get(task.id))
    assert "2030-01-01 09:00（UTC+09:00）" in value["schedule_label"]
