"""Real HTTP task creation, controls, schedules, and restart-facing reads."""

from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
import pytest

from nexus.runtime import TaskController
from nexus.task import TaskStore
from nexus.web import server as web


def request(base, path, method="GET", body=None):
    payload = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=payload, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def task_live(tmp_path, monkeypatch):
    home = tmp_path / "home"
    store = web.ConfigStore(home)
    store.ensure()
    store.save_preferences(workspace=str(tmp_path), permission="ask")
    task_store = TaskStore(home / "tasks.sqlite3")
    runs = []
    def runner(item, cancel):
        runs.append(item.id)
        return "paused"
    controller = TaskController(task_store, run=runner)
    controller.start()
    monkeypatch.setattr(web, "STATE", {
        "store": store, "sessions": web.SessionStore(store.sessions_dir),
        "tasks": task_store, "task_controller": controller,
        "skills": {},
    })
    monkeypatch.setattr(web, "APPROVALS", web.Approvals())
    monkeypatch.setattr(web, "RUNNING", {"agent": None})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", task_store, runs
    controller.close()
    httpd.shutdown()
    httpd.server_close()


def test_create_without_model_runs_and_survives_reload(task_live):
    base, store, runs = task_live
    status, created = request(base, "/api/tasks", "POST", {
        "title": "draft", "goal": "complete later", "request_id": "create-http-1",
    })
    assert status == 201
    assert created["state"] == "pending"
    status, state = request(base, "/api/tasks")
    assert status == 200
    assert state["tasks"][0]["id"] == created["id"]
    assert TaskStore(store.path).get(created["id"]).goal == "complete later"
    assert runs == []


def test_commands_use_versions_and_do_not_run_automatically(task_live):
    base, store, runs = task_live
    _, task = request(base, "/api/tasks", "POST", {
        "title": "manual", "goal": "manual", "request_id": "create-http-2",
    })
    status, wrong = request(base, f"/api/tasks/{task['id']}/commands", "POST", {
        "operation": "start", "request_id": "bad-version", "expected_version": 9,
    })
    assert status == 409
    assert not runs
    status, started = request(base, f"/api/tasks/{task['id']}/commands", "POST", {
        "operation": "start", "request_id": "start-http-1",
        "expected_version": task["control_version"],
    })
    assert status == 200
    assert started["state"] == "queued"


def test_schedule_and_detail_are_visible(task_live):
    base, store, runs = task_live
    _, task = request(base, "/api/tasks", "POST", {
        "title": "scheduled", "goal": "scheduled", "request_id": "create-http-3",
    })
    status, result = request(base, f"/api/tasks/{task['id']}/schedule", "PUT", {
        "due_at": "2030-01-01T10:00:00+09:00", "timezone": "UTC", "mode": "confirm",
    })
    assert status == 200
    assert result["mode"] == "confirm"
    status, detail = request(base, f"/api/tasks/{task['id']}")
    assert detail["schedule"]["timezone"] == "UTC"
    assert detail["messages"] == []


def test_task_events_are_readable_and_task_sessions_stay_separate(task_live):
    base, store, runs = task_live
    _, task = request(base, "/api/tasks", "POST", {
        "title": "events", "goal": "events", "request_id": "create-http-4",
    })
    status, events = request(base, f"/api/tasks/{task['id']}/events?after=0")
    assert status == 200
    assert events["events"]
    status, sessions = request(base, "/api/state")
    assert status == 200
    assert not any(item["title"] == "events" for item in sessions["sessions"])


def test_language_create_does_not_launch_a_task(task_live):
    base, store, runs = task_live
    status, value = request(base, "/api/task-control", "POST", {
        "text": "创建任务：为发布前补齐验证", "request_id": "language-create",
    })
    assert status == 200
    assert value["operation"] == "create"
    assert value["task"]["state"] == "pending"
    assert runs == []
