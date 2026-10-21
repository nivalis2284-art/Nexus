from __future__ import annotations

import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from nexus_scheduler.runtime import SchedulerRuntime
from nexus_scheduler.store import now_iso, now_local


class Callback:
    def __init__(self):
        self.items = []
        self.status = 200
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                owner.items.append(json.loads(self.rfile.read(length)))
                self.send_response(owner.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/automation/wake"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def runtime(tmp_path):
    callback = Callback()
    instance = SchedulerRuntime(tmp_path, transport=httpx.HTTPTransport())
    instance.store.configure({"enabled": True, "interval_enabled": False,
                              "planning_enabled": False})
    instance.connect(callback.url)
    yield instance, callback
    instance.close()
    callback.close()


def test_first_settings_are_disabled_and_builtins_exist(tmp_path):
    fresh = SchedulerRuntime(tmp_path)
    try:
        state = fresh.state()
        assert state["settings"]["enabled"] is False
    finally:
        fresh.close()
    assert {item["id"] for item in state["schedules"]} == {
        "builtin-interval", "builtin-planning"
    }
    assert all("last_text" not in item for item in state["watches"])


def test_due_schedule_advances_only_after_callback(runtime):
    instance, callback = runtime
    item = instance.store.save_schedule({
        "title": "Reminder", "kind": "once", "purpose": "task",
        "due_at": (now_local() - timedelta(minutes=1)).isoformat(timespec="seconds"),
        "prompt": "Tell me", "notify": True,
    })
    instance.tick()
    assert len(callback.items) == 1
    payload = callback.items[0]
    assert payload["source"] == "scheduler"
    assert payload["kind"] == "task"
    assert payload["reasons"][0]["type"] == "task"
    assert payload["notify"] is True
    assert instance.store.get_schedule(item["id"])["next_run_at"] is None


def test_failed_callback_keeps_due_schedule(runtime):
    instance, callback = runtime
    callback.status = 503
    item = instance.store.save_schedule({
        "title": "Retry", "kind": "once", "purpose": "wake",
        "due_at": (now_local() - timedelta(minutes=1)).isoformat(timespec="seconds"),
    })
    instance.tick()
    assert instance.store.get_schedule(item["id"])["next_run_at"] is not None
    assert "Callback failed" in instance.state()["error"]
    callback.status = 200
    instance.tick()
    assert instance.store.get_schedule(item["id"])["next_run_at"] is None


def test_manual_run_works_when_disabled_and_does_not_advance(runtime):
    instance, callback = runtime
    item = instance.store.save_schedule({
        "title": "Manual", "kind": "daily", "time": "12:00", "purpose": "wake",
    })
    before = item["next_run_at"]
    assert instance.schedule_run(item["id"]) == {"accepted": True}
    assert callback.items[0]["kind"] == "manual"
    assert instance.store.get_schedule(item["id"])["next_run_at"] == before


def test_web_baseline_and_change_wake(runtime):
    instance, callback = runtime
    text = {"value": "first body"}
    instance._read_page = lambda url: text["value"]
    watch = instance.store.save_watch({"title": "Page", "url": "https://example.com"})
    instance.tick()
    assert callback.items == []
    assert instance.store.get_watch(watch["id"])["last_text"] == "first body"
    text["value"] = "changed body"
    assert instance.watch_check(watch["id"]) == {"checked": True, "error": ""}
    reason = callback.items[0]["reasons"][0]
    assert reason["type"] == "web_change"
    assert reason["url"] == "https://example.com"
    assert reason["title"] == "Page"
    assert reason["before"] == "first body"
    assert reason["after"] == "changed body"
    assert reason["at"]
    assert callback.items[0]["kind"] == "manual"
    assert instance.store.get_watch(watch["id"])["last_text"] == "changed body"


def test_failed_change_callback_keeps_baseline(runtime):
    instance, callback = runtime
    text = {"value": "old"}
    instance._read_page = lambda url: text["value"]
    watch = instance.store.save_watch({"title": "Page", "url": "https://example.com"})
    instance.tick()
    callback.status = 500
    text["value"] = "new"
    instance.tick()
    assert instance.store.get_watch(watch["id"])["last_text"] == "old"


def test_import_preserves_ids_times_and_baseline_once(tmp_path):
    instance = SchedulerRuntime(tmp_path)
    try:
        values = {
            "settings": {"enabled": True, "interval_minutes": 30},
            "schedules": [{
                "id": "keep-me", "title": "Imported", "prompt": "", "kind": "interval",
                "interval_minutes": 15, "weekdays": [], "enabled": True, "notify": False,
                "origin": "user", "purpose": "wake", "next_run_at": "2026-01-02T03:04:05+09:00",
            }],
            "watches": [{
                "id": "watch-me", "title": "Old", "url": "https://example.com",
                "interval_minutes": 5, "enabled": True, "origin": "user",
                "last_text": "baseline", "last_checked_at": "2026-01-02T03:04:05+09:00",
                "next_check_at": "2026-01-02T03:09:05+09:00", "error": None,
            }],
        }
        assert instance.store.import_state(values) is True
        assert instance.store.get_schedule("keep-me")["next_run_at"] == "2026-01-02T03:04:05+09:00"
        assert instance.store.get_watch("watch-me")["last_text"] == "baseline"
        assert instance.store.import_state({"watches": []}) is False
        assert instance.store.get_watch("watch-me")["last_text"] == "baseline"
    finally:
        instance.close()


def test_reader_is_created_read_and_closed_on_pages_thread(tmp_path):
    calls = []
    instance = SchedulerRuntime(tmp_path, reader_factory=lambda: Reader(calls))
    try:
        instance._read_page("https://example.com")
        instance._read_page("https://example.com")
    finally:
        instance.close()
    assert [name for name, _ in calls] == ["new", "read", "read", "close"]
    assert len({thread for _, thread in calls}) == 1
    assert calls[0][1] != threading.get_ident()


class Reader:
    def __init__(self, calls):
        self.calls = calls
        self.calls.append(("new", threading.get_ident()))

    def read(self, url):
        self.calls.append(("read", threading.get_ident()))
        return url

    def close(self):
        self.calls.append(("close", threading.get_ident()))
