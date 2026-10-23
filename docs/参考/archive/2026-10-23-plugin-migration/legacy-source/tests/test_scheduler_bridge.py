"""Local protocol checks: scheduler MCP -> Nexus HTTP queue -> wake outcome."""
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer

import httpx
import pytest
import test_automation_basics as wake_checks

from nexus.automation.service import SchedulerService
from nexus.mcp import MCPRegistry
from nexus.web import server

wake_runtime = wake_checks.runtime


@pytest.fixture
def bridge(wake_runtime):
    runtime = wake_runtime
    registry = MCPRegistry()
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    service = SchedulerService(registry, runtime.state["store"].home)
    runtime.scheduler = service
    runtime.state.update(mcp=registry, scheduler_service=service)
    base = f"http://127.0.0.1:{http.server_port}"
    try:
        service.start(base + "/api/automation/wake")
        assert not service.error, service.error
        yield runtime, service, base
    finally:
        registry.close()
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("reply,summary", [("A useful update.", ""),
                                         ("", "Checked the context; nothing useful to add.")])
def test_schedule_callback_and_wake_outcome(bridge, reply, summary):
    runtime, service, base = bridge
    # Use the existing HTTP settings route, which forwards rule operations over MCP.
    with httpx.Client(base_url=base) as client:
        response = client.post("/api/automation/schedules", json={
            "id": "bridge-check", "title": "Bridge check", "kind": "once",
            "due_at": (datetime.now().astimezone() + timedelta(hours=1)).isoformat(),
            "purpose": "wake", "notify": False,
        })
        assert response.status_code == 200, response.text
        assert any(item["id"] == "bridge-check" for item in response.json()["schedules"])
        response = client.post("/api/automation/schedules/bridge-check/run", json={})
        assert response.status_code == 200, response.text
    assert len(runtime.store.runs()) == 1
    assert runtime.store.next_run()["reasons"][0]["source"] == "scheduler"
    runtime.run = lambda *args: runtime.finish(None, {"reply": reply, "summary": summary})
    runtime._execute_pending()
    result = runtime.store.runs()[0]
    assert result["status"] == "done", result["error"]
    session = runtime.state["sessions"].load(runtime.inbox().session_id)
    assert session.items[-1].kind == ("assistant" if reply else "experience")
    assert session.items[-1].text == (reply or summary)
    # The same connection handles state and CRUD; no task-specific service spawn.
    connection = service.registry.servers[service.name]
    service.call("schedule_delete", id="bridge-check")
    assert service.registry.servers[service.name] is connection


def test_due_once_schedule_is_delivered_and_advanced(bridge):
    runtime, service, _base = bridge
    service.call("schedule_save", values={
        "id": "due-check", "kind": "once", "purpose": "wake",
        "due_at": (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(),
    })
    service.call("scheduler_configure", values={
        "enabled": True, "interval_enabled": False, "planning_enabled": False,
    })
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        item = next(s for s in service.snapshot()["schedules"] if s["id"] == "due-check")
        if runtime.store.runs() and not item["enabled"]:
            break
        time.sleep(.05)
    assert len(runtime.store.runs()) == 1
    assert runtime.store.runs()[0]["reasons"][0]["schedule_id"] == "due-check"
    assert not item["enabled"]


def test_legacy_migration_preserves_rules_and_local_runs(bridge):
    runtime, service, base = bridge
    from nexus_scheduler.store import SchedulerStore

    legacy = SchedulerStore(runtime.store.path)
    legacy.configure({"enabled": False, "interval_minutes": 17, "planning_time": "11:23"})
    schedule = legacy.save_schedule({"id": "existing", "kind": "once",
        "due_at": "2030-01-02T03:04:05+09:00", "purpose": "wake"})
    watch = legacy.save_watch({"id": "watched", "url": "https://example.com"})
    legacy.record_watch(watch["id"], "existing baseline", "", "2026-10-21T01:02:03+09:00")
    run = runtime.receive({"reasons": [{"type": "wake"}]})
    service.start(base + "/api/automation/wake")
    state = service.snapshot()
    assert state["settings"]["interval_minutes"] == 17
    assert state["settings"]["planning_time"] == "11:23"
    assert next(s for s in state["schedules"] if s["id"] == "existing")["next_run_at"] == schedule["next_run_at"]
    # Baseline stays private to the scheduler store; verify its persisted value.
    with sqlite3.connect(service.home / "scheduler" / "scheduler.sqlite3") as db:
        assert db.execute("SELECT last_text FROM watches WHERE id='watched'").fetchone()[0] == "existing baseline"
    assert runtime.store.runs()[0]["id"] == run["id"]
    service.call("schedule_delete", id="existing")
    service.start(base + "/api/automation/wake")
    assert not any(s["id"] == "existing" for s in service.snapshot()["schedules"])


def test_nexus_startup_owns_one_shared_scheduler_and_closes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "STATE", {})
    connected = []

    class ProbeServer(ThreadingHTTPServer):
        def serve_forever(self, poll_interval=.5):
            state = server.STATE
            service = state["scheduler_service"]
            assert not service.error, service.error
            connection = state["mcp"].servers[service.name]
            assert connection.connected
            assert state["task_runner"].mcp_registry is state["mcp"]
            assert state["automation"].scheduler is service
            assert len(state["mcp"].servers) == 1
            connected.append(connection)

    monkeypatch.setattr(server, "ThreadingHTTPServer", ProbeServer)
    server.serve(home=tmp_path / "startup-home", port=0, workspace=tmp_path)
    assert len(connected) == 1
    assert not connected[0].connected
    assert server.STATE["mcp"].servers == {}
