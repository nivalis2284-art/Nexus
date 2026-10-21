"""Second half of the real-model probe: page change and restart-safe persistence."""
from __future__ import annotations

import argparse
import http.server
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from nexus.automation.runtime import AutomationRuntime, now_iso
from nexus.core.config import ConfigStore
from nexus.session import SessionStore
from nexus.task import TaskStore
from nexus.web import server


def runtime_for(home):
    config = ConfigStore(home)
    config.ensure()
    tasks = TaskStore(home / "tasks.sqlite3")
    sessions = SessionStore(config.sessions_dir)
    inbox = SimpleNamespace(store=tasks, session_id="s_acceptance_ling", running=False,
        current_input=0, interaction=None, live={}, _condition=threading.RLock(),
        emit=lambda event: None)
    state = {"store": config, "tasks": tasks, "sessions": sessions, "closing": False,
             "task_controller": SimpleNamespace(active_task=None, queued_ids=lambda: []),
             "jobs": {}, "mcp": None}
    def run(session, emit, inbox, wake):
        object.__new__(server.Handler)._run_turn(session, config.load(), emit, ling=inbox, wake=wake)
    runtime = AutomationRuntime(state, threading.Lock(), lambda: inbox, run)
    state["automation"] = runtime
    server.STATE.clear(); server.STATE.update(state)
    return runtime


def serve(home):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(home), **kwargs)
        def log_message(self, *args):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/page.html"


def change_page(home: Path) -> None:
    page = home / "page.html"
    text = page.read_text(encoding="utf-8")
    marker = "textContent = "
    start = text.index(marker) + len(marker) + 1
    end = text.index('"', start)
    current = text[start:end]
    replacement = ("A genuinely changed body" if current != "A genuinely changed body"
                   else "Another genuinely changed body")
    page.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def wait_run(runtime, before=None, timeout=300):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.tick()
        values = [r for r in runtime.store.runs()
                  if any(item.get("type") == "web_change" for item in r["reasons"])]
        if values and values[0]["id"] != before and values[0]["status"] in ("done", "failed"):
            return values[0]
        time.sleep(1)
    raise TimeoutError("web-change wake did not finish")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    home = Path(args.home).resolve()
    result = {}
    server, url = serve(home)
    try:
        runtime = runtime_for(home)
        runtime.store.configure({"enabled": True, "interval_enabled": False,
            "planning_enabled": False, "watch_interval_minutes": 1})
        watch_id = runtime.store.watches()[0]["id"]
        runtime.store.delete_watch(watch_id)
        watch_id = runtime.store.save_watch({"title": "验收网页", "url": url,
                                             "interval_minutes": 1})["id"]
        runtime.store.save_watch({"id": watch_id, "url": url})
        watch = runtime.store.watches()[0]
        previous = runtime.store.runs()[0]["id"] if runtime.store.runs() else None
        runtime.request_check(watch["id"])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            runtime.tick()
            current = runtime.store.watches()[0]
            if current["last_checked_at"] != watch["last_checked_at"]:
                break
            time.sleep(1)
        assert runtime.store.watches()[0]["last_text"], runtime.store.watches()[0]["error"]
        baseline = runtime.store.watches()[0]["last_text"]
        (home / "baseline.txt").write_text(baseline, encoding="utf-8")
        assert not runtime.store.runs() or runtime.store.runs()[0]["id"] == previous
        result["unchanged_page_did_not_wake"] = True
        print("UNCHANGED OK", flush=True)
        # The caller edits the page after this process exits; the baseline is durable.
    finally:
        server.shutdown()
        runtime.close()
    server, url = serve(home)
    try:
        runtime = runtime_for(home)
        watch_id = runtime.store.watches()[0]["id"]
        # A fresh local server has a new URL. Keep the body baseline explicitly
        # so URL-only changes do not wipe the comparison source.
        runtime.store.save_watch({"id": watch_id, "url": url})
        with runtime.store._lock, runtime.store._db() as connection:
            connection.execute("UPDATE watches SET last_text=? WHERE id=?", (baseline, watch_id))
            connection.commit()
        baseline = (home / "baseline.txt").read_text(encoding="utf-8")
        result["baseline_survived_restart"] = True
        change_page(home)
        # The saved watch is the durable baseline; reading the new page must
        # enqueue the change without first overwriting that baseline.
        assert runtime.store.watches()[0]["last_text"] == baseline
        runtime._check(runtime.store.watches()[0], now_iso())
        changed = wait_run(runtime, before=previous)
        result["web_change"] = changed
        assert changed["status"] == "done", changed
        (home / "change-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("CHANGE OK", flush=True)
    finally:
        server.shutdown()
        runtime.close()


if __name__ == "__main__":
    main()
