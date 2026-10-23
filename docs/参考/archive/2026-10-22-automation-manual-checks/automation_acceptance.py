"""Manual real-model probe. It is not part of the automated test suite."""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from nexus.automation.runtime import AutomationRuntime, now_iso
from nexus.core.config import ConfigStore
from nexus.session import Message, SessionStore
from nexus.task import TaskStore
from nexus.web import server


def wait(runtime, timeout=240, before=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.tick()
        values = runtime.store.runs()
        value = values[0] if values else None
        if value and value["status"] in ("done", "failed"):
            if before is None or value["id"] != before:
                return value
        time.sleep(1)
    raise TimeoutError("Real-model wake did not finish")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    home = Path(args.home).resolve()
    config = ConfigStore(home)
    config.ensure()
    tasks = TaskStore(home / "tasks.sqlite3")
    sessions = SessionStore(config.sessions_dir)
    session = sessions.load("s_acceptance_ling")
    inbox = SimpleNamespace(store=tasks, session_id=session.id, running=False,
        current_input=0, interaction=None, live={}, _condition=threading.RLock(),
        emit=lambda event: None)
    state = {"store": config, "tasks": tasks, "sessions": sessions, "closing": False,
             "task_controller": SimpleNamespace(active_task=None, queued_ids=lambda: []),
             "jobs": {}, "mcp": None}
    def run(session, emit, inbox, wake):
        object.__new__(server.Handler)._run_turn(session, config.load(), emit, ling=inbox, wake=wake)
    runtime = AutomationRuntime(state, threading.Lock(), lambda: inbox, run)
    state["automation"] = runtime
    server.STATE.clear()
    server.STATE.update(state)
    result = {"steps": []}
    try:
        runtime.store.configure({"enabled": True, "interval_enabled": False,
            "planning_enabled": False, "watch_interval_minutes": 1})
        schedule = runtime.store.save_schedule({"title": "真实模型短提醒",
            "prompt": "这是一次明确提醒：请回复一句简短确认，并说明你看到了主会话中的偏好。",
            "kind": "once", "due_at": (datetime.now().astimezone()+timedelta(seconds=3)).isoformat(),
            "notify": True, "purpose": "task", "origin": "user"})
        result["steps"].append({"scheduled": schedule["id"]})
        time.sleep(4)
        reminder = wait(runtime)
        result["steps"].append({"reminder": reminder})
        assert reminder["status"] == "done" and reminder["reply"], reminder

        planning = runtime.store.save_schedule({"title": "真实模型规划", "prompt": "",
            "kind": "once", "due_at": (datetime.now().astimezone()+timedelta(seconds=3)).isoformat(),
            "notify": False, "purpose": "planning", "origin": "user"})
        time.sleep(4)
        planned = wait(runtime, before=reminder["id"])
        result["steps"].append({"planning": planned})
        assert planned["status"] == "done", planned
        wakes = [s for s in runtime.store.schedules() if s["purpose"] == "wake"
                 and s["id"] not in ("builtin-interval",) and s["id"] != planning["id"]]
        result["steps"].append({"planned_wakes": wakes,
                                "planning_silence_is_valid": not wakes})

        self_check = runtime.store.save_schedule({"title": "灵自行安排验证",
            "prompt": "这是一次测试唤醒：检查当前主会话上下文，判断是否需要告知我；若无必要请静默。",
            "kind": "once", "due_at": (datetime.now().astimezone()+timedelta(seconds=3)).isoformat(),
            "notify": False, "purpose": "wake", "origin": "agent"})
        result["steps"].append({"agent_created_wake": self_check["id"]})
        time.sleep(4)
        self_wake = wait(runtime, before=planned["id"])
        result["steps"].append({"agent_wake_result": self_wake})
        assert self_wake["status"] == "done", self_wake

        watch = runtime.store.save_watch({"title": "验收网页", "url": args.url, "interval_minutes": 1})
        runtime.request_check(watch["id"])
        deadline = time.monotonic()+90
        while time.monotonic()<deadline and runtime.store.watches()[0]["last_text"] is None:
            runtime.tick()
            time.sleep(1)
        current = runtime.store.watches()[0]
        result["steps"].append({"watch_baseline": {"checked": current["last_checked_at"], "error": current["error"]}})
        assert current["last_text"], current["error"] or "no rendered text"
        Path(home.parent / "page-baseline.json").write_text(json.dumps({"watch": watch["id"],
            "text": current["last_text"]}, ensure_ascii=False), encoding="utf-8")
        result["status"] = "partial"
        (home / "acceptance-result.json").write_text(
            json.dumps(result, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        print("REAL MODEL PARTIAL OK")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
