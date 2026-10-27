"""Live stream state and task projection wiring."""

from __future__ import annotations

import threading
import time

from nexus.runtime.controller import TaskController
from nexus.runtime.stream import RuntimeUpdates, runtime_updates
from nexus.task import TaskStore
from nexus.web import server as web
from nexus.web.task_api import TaskHTTP


def test_ling_live_state_clears_at_checkpoint_but_keeps_context():
    feed = RuntimeUpdates()
    feed.publish("ling", "s1", {"type": "thinking", "text": "check", "message_id": "m1"})
    feed.publish("ling", "s1", {"type": "delta", "text": "answer"})
    feed.publish("ling", "s1", {"type": "context", "data": {"window": 100}})
    assert feed.live("ling", "s1")["text"] == "answer"

    feed.refresh("ling", "s1", clear=True)
    assert feed.live("ling", "s1") == {
        "context": {"window": 100}, "message_id": "m1", "revision": 4,
    }


def test_task_live_state_and_detail_read_the_same_feed(tmp_path):
    config = web.ConfigStore(tmp_path / "home")
    config.ensure()
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    controller = TaskController(tasks)
    state = {"store": config, "tasks": tasks, "task_controller": controller}
    task = tasks.create(title="live", goal="live", workspace=str(tmp_path))
    feed = runtime_updates(state)
    feed.publish("task", task.id, {"type": "thinking", "text": "first"})
    feed.publish("task", task.id, {"type": "tool_start", "tool": {"name": "read_file"}})
    feed.publish("task", task.id, {"type": "delta", "text": "working"})

    detail = TaskHTTP(state, lambda *args: None).task_detail(task.id)
    assert detail["live"]["text"] == "working"
    assert detail["live"]["tool"]["name"] == "read_file"
    assert "first" not in detail["live"].get("thinking", "")

    feed.refresh("task", task.id, clear=True)
    assert TaskHTTP(state, lambda *args: None).task_detail(task.id)["live"] == {"revision": 4}


def test_ling_projection_includes_live_tool_and_saved_usage(tmp_path, monkeypatch):
    config = web.ConfigStore(tmp_path / "home")
    config.ensure()
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    controller = TaskController(tasks)
    state = {"store": config, "sessions": web.SessionStore(config.sessions_dir),
             "tasks": tasks, "task_controller": controller, "closing": False}
    monkeypatch.setattr(web, "STATE", state)
    ling = web._ling()
    try:
        ling.running = True
        ling.emit({"type": "tool_start", "tool": {"name": "read_file", "arguments": {"path": "a"}}})
        items = ling.projection(web._transcript)["items"]
        assert items[-1]["role"] == "tool" and items[-1]["name"] == "read_file"
        assert ling.projection(web._transcript)["usage"]["total"] == 0
    finally:
        ling.running = False
        ling.close()
        controller.close()


def test_controller_on_change_covers_queue_and_final_state(tmp_path):
    entered, release = threading.Event(), threading.Event()
    changes = []

    def run(task, cancel):
        entered.set()
        release.wait(3)
        return "paused"

    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    controller = TaskController(tasks, run=run, on_change=changes.append)
    task = tasks.create(title="work", goal="work", workspace=str(tmp_path))
    controller.start()
    try:
        controller.submit(task.id, request_id="start")
        assert entered.wait(3)
        release.set()
        for _ in range(200):
            if tasks.get(task.id).state == "paused":
                break
            time.sleep(0.01)
        for _ in range(200):
            if len(changes) >= 2:
                break
            time.sleep(0.01)
        assert changes.count(task.id) >= 2
    finally:
        release.set()
        controller.close()


def test_publish_returns_json_ready_event():
    feed = RuntimeUpdates()
    queue_ = feed.subscribe()
    try:
        feed.refresh("task", "t1", clear=True)
        value = queue_.get(timeout=1)
        assert value["revision"] == 1
        assert value["live"] == {"revision": 1}
    finally:
        feed.unsubscribe(queue_)
