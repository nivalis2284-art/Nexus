"""Autonomy through its real stdio MCP service, HTTP callback and activity queue."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core.config import ConfigStore
from nexus.mcp import MCPRegistry
from nexus.plugins.activity import ActivityRuntime
from nexus.plugins.manager import PluginManager
from nexus.session import Message, SessionStore
from nexus.task import TaskStore
from nexus.tools import build
from nexus.web import server as web

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins"


class FakeInbox:
    def __init__(self, session_id: str, store: TaskStore):
        self.session_id = session_id
        self.store = store
        self.running = False
        self.interaction = None
        self.live = {}
        self.emitted = []

    def emit(self, value):
        self.emitted.append(value)


@pytest.fixture
def autonomy(tmp_path, monkeypatch):
    """Temporary home, real plugin source, real MCP and a real local callback."""
    home = tmp_path / "home"
    config = ConfigStore(home)
    settings = config.ensure()
    config.save_preferences(workspace=str(tmp_path), permission="yolo")
    sessions = SessionStore(config.sessions_dir)
    session = sessions.create("model")
    sessions.append(session, Message(kind="user", text="autonomy integration"))
    tasks = TaskStore(home / "tasks.sqlite3")
    with tasks._transaction() as db:
        db.execute("CREATE TABLE IF NOT EXISTS ling_inbox (status TEXT)")

    mcp = MCPRegistry()
    state = {
        "store": config,
        "sessions": sessions,
        "tasks": tasks,
        "mcp": mcp,
        "jobs": {},
        "closing": False,
        "task_controller": SimpleNamespace(active_task=None, queued_ids=list),
    }
    manager = PluginManager(state, root=PLUGIN_ROOT)
    state["plugins"] = manager
    inbox = FakeInbox(session.id, tasks)
    runs = []

    def run(session, emit, inbox, event):
        runs.append((session, event))
        if event["text"] == "ONE-REPLY":
            runtime.finish("autonomy", reply="Reply from real MCP")
        else:
            runtime.finish("autonomy", summary="Silent but recorded.")

    runtime = ActivityRuntime(manager, threading.Lock(), lambda: inbox, run)
    manager.runtime = runtime

    # Reuse the already-installed interpreter instead of creating a venv or
    # installing the manifest's dependencies over the network.
    monkeypatch.setattr(
        "nexus.plugins.environment.prepare",
        lambda *args, **kwargs: Path(sys.executable),
    )

    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager.base_url = f"http://127.0.0.1:{server.server_port}"

    monkeypatch.setattr(web, "STATE", state)
    monkeypatch.setattr(web, "RUNNING", {"agent": None})
    monkeypatch.setattr(web, "APPROVALS", web.Approvals())

    entry = manager.entries["autonomy"]
    entry.enabled, entry.status, entry.error = True, "loading", ""
    manager._load(entry)
    assert entry.status == "ready", entry.error

    yield SimpleNamespace(
        manager=manager,
        mcp=mcp,
        home=home,
        config=config,
        settings=settings,
        sessions=sessions,
        session=session,
        tasks=tasks,
        inbox=inbox,
        runtime=runtime,
        runs=runs,
        server=server,
        state=state,
    )

    try:
        manager.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        mcp.close()


def http_json(base, path, method="GET", value=None):
    data = json.dumps(value).encode() if value is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        try:
            return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())


def test_real_scheduler_http_callback_persists_then_finishes_reply_and_silence(autonomy):
    manager = autonomy.manager
    plugin = manager.entries["autonomy"].instance
    assert plugin is not None
    assert plugin.runtime.scheduler_snapshot()["error"] == ""

    saved = manager.call_service(
        "autonomy",
        "scheduler",
        "schedule_save",
        {"values": {
            "id": "mcp-reply",
            "title": "Reply from local MCP",
            "prompt": "ONE-REPLY",
            "kind": "once",
            "due_at": "2030-01-01T09:00:00+09:00",
            "purpose": "wake",
            "enabled": True,
            "notify": True,
            "origin": "user",
        }},
    )
    assert saved["id"] == "mcp-reply"
    assert manager.call_service(
        "autonomy", "scheduler", "schedule_run", {"id": "mcp-reply"}
    ) == {"accepted": True}

    event = manager.events.pending(["autonomy"])
    assert event is not None
    assert event["text"] == "ONE-REPLY"
    assert event["reply_required"] is True
    assert event["data"]["reasons"][0]["schedule_id"] == "mcp-reply"

    # The scheduler's real HTTP callback has persisted the event; run the
    # common activity queue synchronously so the test need not wait a clock.
    manager.runtime.tick()
    assert manager.events.get(event["id"])["status"] == "done"
    assert manager.runtime.outcome == {"reply": "Reply from real MCP", "summary": ""}
    assert autonomy.sessions.load(autonomy.session.id).items[-1].kind == "assistant"
    assert manager.events.get(event["id"])["result"]["reply"] == "Reply from real MCP"

    assert manager.call_service(
        "autonomy", "scheduler", "schedule_save",
        {"values": {
            "id": "mcp-silent",
            "title": "Silent local MCP",
            "prompt": "ONE-SILENT",
            "kind": "once",
            "due_at": "2030-01-02T09:00:00+09:00",
            "purpose": "wake",
            "enabled": True,
            "notify": False,
            "origin": "agent",
        }},
    )["id"] == "mcp-silent"
    assert manager.call_service(
        "autonomy", "scheduler", "schedule_run", {"id": "mcp-silent"}
    )["accepted"] is True
    manager.runtime.tick()
    silent = next(
        (
            value for value in manager.events.list("autonomy")
            if value["text"] == "ONE-SILENT" and value["status"] == "done"
        ),
        None,
    )
    assert silent is not None
    assert silent["result"] == {"reply": "", "summary": "Silent but recorded."}
    assert autonomy.sessions.load(autonomy.session.id).items[-1].text == "Silent but recorded."
    assert autonomy.sessions.load(autonomy.session.id).items[-1].kind == "experience"

    # The service persists both the arrangement and the callback event.
    snapshot = plugin.runtime.scheduler_snapshot()
    assert {value["id"] for value in snapshot["schedules"]} >= {"mcp-reply", "mcp-silent"}
    assert {value["id"] for value in manager.events.list("autonomy")} >= {event["id"], silent["id"]}



def test_mcp_tool_catalog_hides_internal_scheduler_operations(autonomy):
    manager = autonomy.manager
    raw = set(autonomy.mcp.tools)
    assert "mcp__plugin_autonomy_scheduler__schedule_save" in raw
    assert "mcp__plugin_autonomy_scheduler__schedule_run" in raw
    assert "mcp__plugin_autonomy_scheduler__watch_save" in raw
    # The service still offers its internal control operations. The model-facing
    # bridge is what must keep connect/import out of the catalog.
    assert "mcp__plugin_autonomy_scheduler__scheduler_connect" in raw
    assert "mcp__plugin_autonomy_scheduler__scheduler_import" in raw

    registry, context = build(
        root=autonomy.home.parent,
        mcp=autonomy.mcp,
    )
    manager.decorate(registry, context, {"kind": "ling", "owner_id": "ling"})
    visible = {
        spec.name for spec in registry.specs()
        if spec.name.startswith("mcp__plugin_autonomy_scheduler__")
    }
    assert visible == {
        "mcp__plugin_autonomy_scheduler__scheduler_state",
        "mcp__plugin_autonomy_scheduler__scheduler_configure",
        "mcp__plugin_autonomy_scheduler__schedule_save",
        "mcp__plugin_autonomy_scheduler__schedule_wakeup",
        "mcp__plugin_autonomy_scheduler__schedule_delete",
        "mcp__plugin_autonomy_scheduler__schedule_run",
        "mcp__plugin_autonomy_scheduler__watch_save",
        "mcp__plugin_autonomy_scheduler__watch_delete",
        "mcp__plugin_autonomy_scheduler__watch_check",
    }
    assert "mcp__plugin_autonomy_scheduler__scheduler_connect" not in visible
    assert "mcp__plugin_autonomy_scheduler__scheduler_import" not in visible


def test_disabled_autonomy_leaves_normal_state_and_chat_tools_buildable(autonomy):
    manager = autonomy.manager
    item = manager.entries["autonomy"]
    item.enabled = False
    manager.events.pause("autonomy", True)

    registry, _context = build(
        root=autonomy.home.parent,
        mcp=autonomy.mcp,
    )
    names = {spec.name for spec in registry.specs()}
    assert "read_file" in names
    assert "write_file" in names
    assert "bash" in names
    assert "automation_list" not in names
    # The real HTTP host still serves general state; only the disabled
    # autonomy capabilities disappear from the model registry.
    status, value = http_json(manager.base_url, "/api/state")
    assert status == 200, value
    assert value["sessions"]
