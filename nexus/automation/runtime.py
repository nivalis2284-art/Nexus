"""Receive wake requests and execute Ling; scheduling is owned by an MCP service."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta

from ..session import Message
from .store import RunStore


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class AutomationRuntime:
    def __init__(self, state, busy, inbox, run):
        self.state, self.busy, self.inbox, self.run = state, busy, inbox, run
        self.store = RunStore(state["store"].home / "automation.sqlite3")
        self.stop = threading.Event()
        self.thread = None
        self.agent = None
        self.active_run = None
        self.outcome = None
        self.error = ""
        self.scheduler = state.get("scheduler_service")

    def start(self):
        self.thread = threading.Thread(target=self._loop, name="ling-automation", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.agent:
            self.agent.stop()
        if self.thread:
            self.thread.join(timeout=5)

    def snapshot(self):
        value = (self.scheduler.snapshot() if self.scheduler else
                 {"settings": {}, "schedules": [], "watches": [], "error": "调度服务未连接"})
        value.update(runs=self.store.runs(), running=self.active_run is not None,
                     error=self.error or value.get("error", ""))
        return value

    def receive(self, payload):
        reasons = payload.get("reasons")
        if not isinstance(reasons, list) or not reasons or any(not isinstance(r, dict) for r in reasons):
            raise ValueError("唤醒请求需要 reasons 列表")
        kind = payload.get("kind", "wake")
        if kind not in ("wake", "task", "manual"):
            raise ValueError("未知唤醒类型")
        source = str(payload.get("source") or "external")
        return self.store.add_run([{**reason, "source": source} for reason in reasons],
            prompt=str(payload.get("prompt") or ""), notify=bool(payload.get("notify", False)), kind=kind)

    def finish(self, context, args):
        if self.active_run is None:
            raise ValueError("此工具只用于结束当前自动唤醒")
        reply = str(args.get("reply") or "").strip()
        summary = str(args.get("summary") or "").strip()
        if self.active_run["notify"] and not reply:
            raise ValueError("这是明确要求提醒或汇报的任务，请填写实际回复")
        if not reply and not summary:
            raise ValueError("选择沉默时，请用一句话记录本次经历")
        self.outcome = {"reply": reply, "summary": summary}
        if self.agent:
            self.agent.stop()
        return {"content": "本次唤醒已结束，回复或静默经历将保存。"}

    def _loop(self):
        while not self.stop.wait(.5):
            try:
                self.tick()
                self.error = ""
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"

    def tick(self):
        self._execute_pending()

    def _execute_pending(self):
        inbox = self.inbox()
        if inbox.running:
            return
        with inbox.store._transaction() as db:
            if db.execute("SELECT 1 FROM ling_inbox WHERE status='pending' LIMIT 1").fetchone():
                return
        if not self.busy.acquire(blocking=False):
            return
        try:
            item = self.store.next_run()
            if not item or self.stop.is_set():
                return
            self.active_run, self.outcome = item, None
            self.store.update_run(item["id"], status="running")
            inbox.running = True
            session = self.state["sessions"].load(inbox.session_id)
            errors, final_text, stopped = [], [], []
            finished = threading.Event()

            def emit(event):
                if event.get("type") == "error":
                    errors.append(str(event.get("message")))
                elif event.get("type") == "delta":
                    final_text.append(str(event.get("text", "")))
                elif event.get("type") == "tool_start":
                    final_text.clear()
                elif event.get("type") == "done":
                    finished.set()
                    stopped.append((event.get("data") or {}).get("stopped"))
                elif event.get("type") in ("approval", "question"):
                    inbox.emit(event)

            try:
                self.run(session, emit, inbox, item)
                if errors:
                    raise RuntimeError("; ".join(errors))
                outcome = self.outcome
                if outcome is None:
                    if not finished.is_set():
                        raise RuntimeError("自动唤醒已结束，但未调用 automation_finish")
                    if stopped and stopped[-1] in ("cancelled", "max_steps"):
                        raise RuntimeError("本次自动活动已停止，未发送未完成的回复")
                    text = "".join(final_text).strip()
                    if not text:
                        raise RuntimeError("本次运行未给出回复或静默经历")
                    outcome = {"reply": text, "summary": ""}
                reply, summary = outcome["reply"], outcome["summary"]
                if reply:
                    self.state["sessions"].append(session, Message(kind="assistant", text=reply,
                        data={"automation_run_id": item["id"]}))
                else:
                    self.state["sessions"].append(session, Message(kind="experience", text=summary,
                        data={"automation_run_id": item["id"]}))
                self.store.update_run(item["id"], status="done", reply=reply,
                                      summary=summary, finished_at=now_iso())
            except Exception as exc:
                self.store.update_run(item["id"], status="failed", error=str(exc), finished_at=now_iso())
            finally:
                inbox.running = False
                inbox.interaction = None
                inbox.live = {}
                self.active_run, self.agent = None, None
        finally:
            self.busy.release()

    def context(self, run):
        planning = any(r.get("type") == "planning" for r in run["reasons"])
        end = (datetime.now().astimezone() + timedelta(hours=24)).isoformat()
        return (
            "This is an automatic wake of the SAME Ling in her existing conversation, not a new owner message. "
            "Use your normal tools, skills, MCP and independent work when useful. "
            "Use the scheduling MCP tools to manage all schedules and watches, including owner-created ones. "
            "Decide whether there is a sufficient reason to contact the owner. Silence is valid. "
            "Ordinary assistant text and tool progress are private until this wake ends. "
            "Finish with automation_finish(reply=actual message) or "
            "automation_finish(summary=one sentence describing observations, actions and why you remain silent). "
            "A task explicitly requesting a reminder or report MUST receive the requested reply. "
            "Schedule/watch tools save changes immediately; do not claim unperformed actions.\n"
            + (f"This is the daily planning wake. Consider useful wake times from now until {end}. "
               "Use the MCP schedule_save tool with values containing kind='once', purpose='wake', notify=false; do not fill the day unnecessarily.\n" if planning else "")
            + "Current time: " + now_iso() + "\nWake material:\n" + json.dumps(run, ensure_ascii=False)
        )
