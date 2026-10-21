"""Development checks for the new wake -> Agent -> reply/experience loop."""
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from nexus.automation.runtime import AutomationRuntime, now_iso
from nexus.core.config import ConfigStore
from nexus.session import Message, SessionStore
from nexus.task import TaskStore
from nexus.web import server


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    config = ConfigStore(tmp_path / "home")
    settings = config.ensure()
    config.update_provider(settings.provider.id, {
        "base_url": "https://fake.test/v1", "family": "openai_chat_completions",
        "models": [{"id": "fake", "context_window_tokens": 32000, "max_output_tokens": 1024}],
    })
    config.select(settings.provider.id, "fake", "off")
    config.write_credential(settings.provider.id, "test-key")
    config.save_preferences(workspace=str(tmp_path), permission="yolo")
    tasks = TaskStore(config.home / "tasks.sqlite3")
    sessions = SessionStore(config.sessions_dir)
    session = sessions.create("Ling")
    sessions.append(session, Message(kind="user", text="Remember that I prefer useful updates."))
    with tasks._transaction() as db:
        db.execute("CREATE TABLE ling_inbox(status TEXT)")
    inbox = SimpleNamespace(store=tasks, session_id=session.id, running=False,
        current_input=0, interaction=None, live={}, _condition=threading.RLock(), emit=lambda event: None)
    state = {"store": config, "tasks": tasks, "sessions": sessions, "closing": False,
             "task_controller": SimpleNamespace(active_task=None, queued_ids=list),
             "jobs": {}, "mcp": None}
    def run(session, emit, inbox, wake):
        object.__new__(server.Handler)._run_turn(session, config.load(), emit, ling=inbox, wake=wake)
    runtime = AutomationRuntime(state, threading.Lock(), lambda: inbox, run)
    state["automation"] = runtime
    monkeypatch.setattr(server, "STATE", state)
    monkeypatch.setattr(server, "RUNNING", {"agent": None})
    yield runtime
    runtime.close()


@pytest.mark.parametrize("reply,summary", [("Here is a useful update.", ""),
                                         ("", "Reviewed the existing context; no new reason to interrupt.")])
def test_real_agent_finishes_reply_or_silent_experience(runtime, monkeypatch, reply, summary):
    import nexus.core.agent as module
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        delta = {"tool_calls": [{"index": 0, "id": "finish", "type": "function",
                 "function": {"name": "automation_finish", "arguments": json.dumps({"reply": reply, "summary": summary})}}]}
        frames = [json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}),
                  json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text="".join("data: " + frame + "\n\n" for frame in frames) + "data: [DONE]\n\n")
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    item = runtime.store.add_run([{"type": "wake", "at": now_iso()}])
    runtime._execute_pending()
    result = runtime.store.runs()[0]
    assert result["status"] == "done", result["error"]
    assert result["id"] == item["id"]
    session = runtime.state["sessions"].load(runtime.inbox().session_id)
    assert session.items[-1].kind == ("assistant" if reply else "experience")
    assert session.items[-1].text == (reply or summary)
    assert "Remember that I prefer useful updates." in json.dumps(seen)
    assert (reply or summary) in json.dumps(server._internal(session))
    if not reply:
        assert summary not in json.dumps(server._transcript(session))
    assert len(session.items) == 2  # No private drafts or tool chatter in chat history.


def test_external_requests_merge_into_one_pending_wake(runtime):
    runtime.receive({"source": "replacement-service", "reasons": [
        {"type": "web_change", "url": "https://example.com", "after": "changed body"}]})
    runtime.receive({"source": "scheduler", "reasons": [{"type": "planning"}]})
    assert len(runtime.store.runs()) == 1
    assert len(runtime.store.runs()[0]["reasons"]) == 2
    assert runtime.store.runs()[0]["reasons"][0]["source"] == "replacement-service"


def test_pending_owner_input_leaves_wake_waiting(runtime):
    runtime.store.add_run([{"type": "wake"}])
    with runtime.inbox().store._transaction() as db:
        db.execute("INSERT INTO ling_inbox VALUES('pending')")
    runtime._execute_pending()
    assert runtime.store.next_run()["status"] == "pending"
