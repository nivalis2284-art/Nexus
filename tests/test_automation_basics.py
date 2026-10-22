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


@pytest.mark.parametrize("reason", [{"type": "wake"},
                                    {"type": "work_result", "task_id": "task-1"}])
def test_every_wake_prompts_review_of_current_state_and_existing_arrangements(runtime, reason):
    prompt = runtime.context({"id": "run-1", "reasons": [reason]})

    assert "what is happening now" in prompt and "existing schedules" in prompt
    for choice in ("kept", "changed", "cancelled"):
        assert choice in prompt
    assert "optional" in prompt and "no tool call is required" in prompt
    assert "autonomous wake you saved" in prompt and "past self" in prompt
    assert "owner's explicit reminder" in prompt
    for tool in ("schedule_wakeup", "schedule_delete", "schedule_save"):
        assert f"tool ending in {tool}" in prompt
    assert "immediately" in prompt


def test_daily_wake_is_an_overall_review_without_a_fixed_24_hour_plan(runtime):
    prompt = runtime.context({"id": "daily-1", "reasons": [{"type": "planning"}]})

    assert "daily review wake" in prompt
    assert "empty plan is valid" in prompt
    assert "24 hours" not in prompt
    assert "fill the coming day" in prompt


def test_daily_wake_can_finish_without_planning_any_new_wake(runtime, monkeypatch):
    import nexus.core.agent as module

    def handler(request):
        delta = {"tool_calls": [{"index": 0, "id": "finish", "type": "function",
                 "function": {"name": "automation_finish",
                               "arguments": json.dumps({"summary": "Reviewed the situation; existing plans still fit."})}}]}
        frames = [json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}),
                  json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text="".join("data: " + frame + "\n\n" for frame in frames) + "data: [DONE]\n\n")

    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    item = runtime.store.add_run([{"type": "planning"}])
    runtime._execute_pending()

    result = runtime.store.runs()[0]
    assert result["id"] == item["id"]
    assert result["status"] == "done", result["error"]
    assert result["reply"] == ""
    assert result["summary"] == "Reviewed the situation; existing plans still fit."


def test_explicit_reminder_cannot_finish_with_silence(runtime):
    runtime.active_run = runtime.store.add_run([{"type": "task"}], notify=True)

    with pytest.raises(ValueError, match="实际回复"):
        runtime.finish(None, {"summary": "Saw the reminder but stayed silent."})

    assert runtime.outcome is None
