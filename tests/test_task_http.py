"""Real HTTP task commands, controls, events, and restart-facing reads."""

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
        "skills": {}, "closing": False,
    })
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
    created = store.create(title="draft", goal="complete later",
                           workspace=str(store.path.parent))
    status, state = request(base, "/api/tasks")
    assert status == 200
    assert state["tasks"][0]["id"] == created.id
    persisted = TaskStore(store.path).get(created.id)
    assert persisted.goal == "complete later"
    assert persisted.state == "pending"
    assert runs == []


def test_commands_use_versions_and_do_not_run_automatically(task_live):
    base, store, runs = task_live
    task = store.create(title="manual", goal="manual", workspace=str(store.path.parent))
    status, _wrong = request(base, f"/api/tasks/{task.id}/commands", "POST", {
        "operation": "start", "request_id": "bad-version", "expected_version": 9,
    })
    assert status == 409
    assert not runs
    status, started = request(base, f"/api/tasks/{task.id}/commands", "POST", {
        "operation": "start", "request_id": "start-http-1",
        "expected_version": task.control_version,
    })
    assert status == 200
    assert started["state"] == "queued"


def test_detail_and_events_are_visible(task_live):
    base, store, _runs = task_live
    task = store.create(title="visible", goal="visible", workspace=str(store.path.parent))
    status, detail = request(base, f"/api/tasks/{task.id}")
    assert status == 200
    assert detail["id"] == task.id
    assert detail["messages"] == []
    status, events = request(base, f"/api/tasks/{task.id}/events?after=0")
    assert status == 200
    assert events["events"]


def test_task_events_are_readable_and_task_sessions_stay_separate(task_live):
    base, store, _runs = task_live
    task = store.create(title="events", goal="events", workspace=str(store.path.parent))
    status, events = request(base, f"/api/tasks/{task.id}/events?after=0")
    assert status == 200
    assert events["events"]
    status, sessions = request(base, "/api/state")
    assert status == 200
    assert not any(item["title"] == "events" for item in sessions["sessions"])
def test_message_does_not_start_pending_work(task_live):
    base, store, runs = task_live
    task = store.create(title="pending", goal="pending", workspace=str(store.path.parent))
    status, value = request(base, f"/api/tasks/{task.id}/messages", "POST", {
        "text": "additional detail", "request_id": "message-http-1", "resume": False,
    })
    assert status == 200
    assert value["delivery"] == "pending"
    assert store.get(task.id).state == "pending"
    assert runs == []
