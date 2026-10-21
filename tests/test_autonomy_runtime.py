"""Integration mechanics with a deterministic provider; not product acceptance."""
import asyncio
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from nexus.autonomy.models import Decision
from nexus.autonomy.runtime import AutonomyRuntime
from nexus.autonomy.scope import Scope
from nexus.autonomy.store import iso
from nexus.core import agent as loop
from nexus.core.config import ConfigStore
from nexus.model import APIFamily
from nexus.runtime import TaskController
from nexus.session import Message, SessionStore
from nexus.task import TaskStore
from nexus.web import server as web


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    config = ConfigStore(tmp_path / "home")
    settings = config.ensure()
    config.update_provider(settings.provider.id, {"family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
        "base_url": "https://fake.test/v1", "models": [{"id": "fake", "max_output_tokens": 512,
        "context_window_tokens": 32000, "capabilities": {"tools": True, "streaming": True}}]})
    config.select(settings.provider.id, "fake", "off")
    config.write_credential(settings.provider.id, "fake-key")
    config.save_preferences(workspace=str(tmp_path))
    tasks = TaskStore(config.home / "tasks.sqlite3")
    controller = TaskController(tasks, run=lambda task, cancel: "paused")
    controller.start()
    state = {"store": config, "sessions": SessionStore(config.sessions_dir), "tasks": tasks,
             "task_controller": controller, "skills": {}}
    monkeypatch.setattr(web, "STATE", state)
    monkeypatch.setattr(web, "BUSY", threading.Lock())
    monkeypatch.setattr(web, "RUNNING", {"agent": None})
    result = AutonomyRuntime(state, web.BUSY, web._ling)
    state["autonomy"] = result
    data = tmp_path / "data"
    data.mkdir()
    (data / "note.md").write_text("Actual source 7319", encoding="utf-8")
    result.configure({"read_roots": [str(data)], "output_root": str(tmp_path / "output"),
                      "token_limit": 200000, "observer_interval": .1,
                      "contact_after_seconds": 0, "fallback_seconds": 1, "min_review_seconds": 1})
    result.command("enable")
    yield result
    result.close()
    if state.get("ling"):
        state["ling"].close()
    controller.close()


def provider(monkeypatch, decision):
    real = httpx.AsyncClient
    requests = []
    def reply(request):
        requests.append(json.loads(request.content))
        args = decision(requests[-1])
        delta = {"tool_calls": [{"index": 0, "id": "decision", "type": "function",
            "function": {"name": "autonomy_decide", "arguments": json.dumps(args)}}]}
        text = "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200}}) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)
    monkeypatch.setattr(loop, "_client", lambda: real(transport=httpx.MockTransport(reply)))
    return requests


def test_real_agent_loop_commits_silence_without_physical_user_message(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_observe", lambda: None)
    requests = provider(monkeypatch, lambda body: {"reason": "No worthwhile new content; remain silent"})
    runtime.tick()
    assert len(requests) == 1
    assert runtime.store.snapshot()["decisions"][0]["status"] == "done"
    session = runtime.state["sessions"].load(runtime.inbox().session_id)
    assert session.items == []
    assert runtime.store.messages() == []
    assert runtime.store.budget()["spent"] == 200


def test_three_sources_coalesce_and_new_events_after_claim_survive(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_observe", lambda: None)
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", {"title": "Open issue", "question": "Why?", "sources": []}, "waiting", db)
        runtime.store.put("plan", "p", {"goal_id": "g", "due_at": iso(time.time()-5), "reason": "check again"}, "pending", db)
    source = runtime.store.add_event("changed", "source_change", {"content": "actual changed text"})
    def decide(body):
        runtime.store.add_event("arrived-during-review", "source_change", {"content": "later"})
        return {"reason": "All three opportunities considered; still no action justified"}
    requests = provider(monkeypatch, decide)
    runtime.tick()
    assert len(requests) == 1
    kinds = {e["kind"] for e in runtime.store.events() if e["consumed_by"] == runtime.store.snapshot()["decisions"][0]["id"]}
    assert kinds == {"time_floor", "planned_review", "source_change"}
    assert runtime.store.get("plan", "p")["status"] == "done"
    pending = runtime.store.events(pending=True)
    assert len(pending) == 1 and pending[0]["source_key"] == "arrived-during-review"
    assert runtime.store.event(source["seq"])["consumed_by"]


def test_observer_import_and_goal_withdrawal_keep_evidence(runtime):
    source = runtime.store.add_event("old", "dialogue", {"text": "An open issue"})
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", {"title": "Issue", "sources": [source["id"]]}, "waiting", db)
        runtime.store.put("watch", "w", {"goal_id": "g", "path": runtime.store.settings()["read_roots"][0]}, "active", db)
    runtime.tick(run_model=False)
    assert not runtime.observer_error
    time.sleep(.2)
    runtime.tick(run_model=False)
    note = runtime.config.home.parent / "data" / "note.md"
    note.write_text("Actual changed source 9467", encoding="utf-8")
    time.sleep(.25)
    runtime.tick(run_model=False)
    changes = [e for e in runtime.store.events() if e["kind"] == "source_change" and e["data"]["observation"]["kind"] == "modified"]
    assert changes and "9467" in changes[-1]["data"]["observation"]["content"]
    runtime.withdraw("g", 1)
    assert runtime.store.get("goal", "g")["status"] == "withdrawn"
    assert runtime.store.get("watch", "w")["status"] == "stopped"
    assert runtime.observer is None
    assert runtime.store.event(changes[-1]["seq"])["data"]["observation"]["content"]


def test_agent_watch_status_versions_converge_without_resurrecting_on_restart(runtime):
    root = runtime.store.settings()["read_roots"][0]
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", {"title": "Open issue"}, "waiting", db)
        runtime.store.put("watch", "w", {"goal_id": "g", "path": root}, "active", db)
    runtime.tick(run_model=False)
    time.sleep(.15)
    with runtime.store.transaction() as db:
        watch = runtime.store.get("watch", "w", db)
        runtime.store.put("watch", "w", watch, "paused", db)
    runtime.tick(run_model=False)
    assert not runtime.observer_error
    assert runtime.observer.call("watches_list", {})["watches"][0]["status"] == "paused"
    runtime._close_observer()
    runtime.tick(run_model=False)
    assert not runtime.observer_error
    assert runtime.observer.call("watches_list", {})["watches"][0]["status"] == "paused"
    with runtime.store.transaction() as db:
        watch = runtime.store.get("watch", "w", db)
        runtime.store.put("watch", "w", watch, "active", db)
    runtime.tick(run_model=False)
    assert not runtime.observer_error
    assert runtime.observer.call("watches_list", {})["watches"][0]["status"] == "active"
    with runtime.store.transaction() as db:
        watch = runtime.store.get("watch", "w", db)
        runtime.store.put("watch", "w", watch, "stopped", db)
    runtime.tick(run_model=False)
    assert not runtime.observer_error
    assert runtime.observer.call("watches_list", {})["watches"][0]["status"] == "stopped"


def test_authorized_scope_is_checked_against_current_and_accepted_paths(runtime, tmp_path):
    goal = {"id": "g", "status": "active", "version": 1}
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", goal, "active", db)
    scope = Scope(runtime, {"goal_id": "g", "goal_version": 1, "scope": runtime.store.settings()})
    context = type("Context", (), {"root": scope.workspace_root, "task_callbacks": None})()
    scope.guard_tool("read_file", {"path": str(tmp_path / "data" / "note.md")}, context)
    scope.guard_tool("write_file", {"path": str(tmp_path / "output" / "result.md")}, context)
    with pytest.raises(PermissionError):
        scope.guard_tool("write_file", {"path": str(tmp_path / "data" / "note.md")}, context)
    with pytest.raises(PermissionError):
        scope.guard_tool("bash", {"command": "anything"}, context)
    runtime.store.command("pause")
    with pytest.raises(Exception, match="暂停"):
        scope.check()


def test_formal_message_is_in_ui_and_next_model_history_and_survives_restart(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_observe", lambda: None)
    fact = runtime.store.add_event("source", "source_change", {"content": "Actual observation"})
    provider(monkeypatch, lambda body: {"reason": "Worth sharing", "message": {
        "text": "A verified detail is worth sharing", "reason": "relevant result", "sources": [fact["id"]]}})
    runtime.tick()
    session = runtime.state["sessions"].load(runtime.inbox().session_id)
    assert web._transcript(session)[-1]["autonomy_message_id"]
    assert "worth sharing" in web._internal(session)[-1]["content"]
    runtime.store.recover()
    assert len(web._transcript(session)) == 1
    assert runtime.store.messages()[0]["read_at"] is None


def test_atomic_work_result_survives_task_deletion(runtime):
    task = runtime.tasks.create(title="work", goal="read", workspace=str(runtime.config.home.parent))
    with runtime.store.transaction() as db:
        runtime.store.put("action", "a", {"goal_id": "g", "task_id": task.id}, "active", db)
    task = runtime.tasks.command(task.id, "completed", request_id="complete")
    assert any(e["kind"] == "work_result" for e in runtime.store.events())
    runtime.tasks.delete(task.id, expected_version=task.control_version)
    assert any(e["data"].get("task_id") == task.id for e in runtime.store.events())


def test_terminate_cancels_already_paused_owned_work(runtime):
    task = runtime.tasks.create(title="paused work", goal="read", workspace=str(runtime.config.home.parent))
    task = runtime.tasks.command(task.id, "paused", request_id="pause-work")
    with runtime.store.transaction() as db:
        runtime.store.put("action", "a", {"goal_id": "g", "task_id": task.id}, "active", db)
    runtime.command("terminate")
    assert runtime.tasks.get(task.id).state == "cancelled"
    assert runtime.store.get("action", "a")["status"] == "cancelled"


def test_deleted_pending_work_is_not_recreated_and_cannot_block_next_review(runtime):
    task = runtime.tasks.create(title="work", goal="read", workspace=str(runtime.config.home.parent))
    with runtime.store.transaction() as db:
        runtime.store.put("action", "a", {"goal_id": "g", "task_id": task.id}, "active", db)
    runtime.tasks.delete(task.id, expected_version=task.control_version)
    runtime._collect_results()
    runtime._dispatch()
    runtime._collect_results()
    assert runtime.tasks.list() == []
    assert runtime.store.get("action", "a")["status"] == "done"
    results = [e for e in runtime.store.events() if e["source_key"] == "deleted-work:a"]
    assert len(results) == 1 and results[0]["data"]["state"] == "deleted"


def test_dialogue_import_deduplicates_owner_input_and_does_not_self_wake(runtime):
    inbox = runtime.inbox()
    session = runtime.state["sessions"].load(inbox.session_id)
    runtime.on_user("An actual open question", 42)
    runtime.state["sessions"].append(session, Message(kind="user", text="An actual open question", data={"ling_input_id": 42}))
    runtime.state["sessions"].append(session, Message(kind="assistant", text="I understand the open question"))
    runtime._sync_history()
    runtime._sync_history()
    facts = runtime.store.events()
    assert len(facts) == 2
    assert len(runtime.store.events(pending=True)) == 1
    assert facts[1]["data"]["role"] == "assistant" and facts[1]["consumed_by"] == "dialogue_context"


def test_goal_completion_retires_action_and_allows_reconsideration(runtime):
    task = runtime.tasks.create(title="old work", goal="old premise", workspace=str(runtime.config.home.parent))
    runtime.tasks.command(task.id, "paused", request_id="old-paused")
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", {"title": "An actual uncertainty"}, "waiting", db)
        runtime.store.put("action", "old", {"goal_id": "g", "goal_version": 1, "task_id": task.id}, "active", db)
        goal = runtime.store.get("goal", "g", db)
        runtime.store.put("goal", "g", {**goal, "understanding": "The question is settled"}, "completed", db)
    runtime._dispatch()
    runtime._dispatch()
    assert runtime.tasks.get(task.id).state == "cancelled"
    assert runtime.store.get("action", "old")["status"] == "cancelled"
    assert len([e for e in runtime.store.events() if e["source_key"] == "retired-work:old"]) == 1
    assert runtime.store.begin_review() is not None


def test_understanding_revision_preserves_valid_work_until_explicit_stop(runtime):
    task = runtime.tasks.create(title="work", goal="Read actual evidence", workspace=str(runtime.config.home.parent))
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", {"title": "An actual question"}, "waiting", db)
        runtime.store.put("action", "a", {"goal_id": "g", "goal_version": 1, "task_id": task.id}, "active", db)
        goal = runtime.store.get("goal", "g", db)
        runtime.store.put("goal", "g", {**goal, "understanding": "The evidence is clearer, work is still valid"}, "active", db)
    runtime.tasks.command(task.id, "paused", request_id="owner-pause")
    runtime._dispatch()
    original_scope = runtime.task_scope(task.id)
    original_scope.check()
    assert runtime.tasks.get(task.id).state == "paused"
    assert runtime.store.get("action", "a")["status"] == "active"
    runtime.store.add_event("actual-feedback", "work_result", {"state": "paused"})
    review = runtime.store.begin_review()
    decision = Decision.model_validate({"reason": "This attempt is no longer useful",
        "cancel_actions": [{"id": "a", "expected_version": 1, "reason": "New evidence invalidates this attempt"}]}).model_dump()
    runtime._validate(decision, review)
    runtime.store.commit_review(review, decision)
    with pytest.raises(Exception, match="行动已停止"):
        original_scope.check()
    runtime._dispatch()
    assert runtime.tasks.get(task.id).state == "cancelled"
    assert runtime.store.get("action", "a")["stop_reason"] == "New evidence invalidates this attempt"


def _pause_work(runtime, *, state="running"):
    task = runtime.tasks.create(title="work", goal="Read actual evidence",
                                workspace=str(runtime.store.settings()["read_roots"][0]))
    if state == "running":
        task = runtime.tasks.command(task.id, "running", request_id=f"run-{task.id}")
    elif state == "paused":
        task = runtime.tasks.command(task.id, "paused", request_id=f"pause-{task.id}")
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", {"title": "question", "sources": []}, "active", db)
        runtime.store.put("action", "a", {"goal_id": "g", "task_id": task.id,
            "task_binding": {"goal": task.goal, "effective_goal": task.effective_goal,
                             "workspace": str(task.workspace)}}, "active", db)
    return task


class _PauseFixtureController:
    """A controller test double with the real pause transitions only."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.pause_calls = []
        self.resume_calls = []

    def pause(self, task_id, *, request_id):
        self.pause_calls.append(request_id)
        task = self.runtime.tasks.get(task_id)
        if task.state == "running":
            task = self.runtime.tasks.command(task_id, "pausing", request_id=request_id)
            return self.runtime.tasks.command(task_id, "paused", request_id=f"stop-{task_id}-fixture")
        return self.runtime.tasks.command(task_id, "paused", request_id=request_id)

    def resume(self, task_id, *, request_id, expected_version=None):
        self.resume_calls.append((task_id, request_id, expected_version))
        return self.runtime.tasks.command(task_id, "queued", request_id=request_id,
                                          expected_version=expected_version)


def test_global_pause_records_exact_task_generation_and_resume_uses_it(runtime, monkeypatch):
    task = _pause_work(runtime)
    controller = _PauseFixtureController(runtime)
    monkeypatch.setattr(runtime, "controller", controller)
    runtime.command("pause")
    paused = runtime.tasks.get(task.id)
    action = runtime.store.get("action", "a")
    assert paused.state == "paused"
    if action["autonomy_pause"]["task_version"] is None:
        runtime._reconcile_pauses()
        action = runtime.store.get("action", "a")
    assert action["autonomy_pause"]["task_version"] == paused.control_version
    assert action["autonomy_pause"]["control_version"] == runtime.store.control()["version"]
    assert f"-{paused.control_version - 2}" in controller.pause_calls[0]
    runtime.command("resume")
    assert runtime.tasks.get(task.id).state == "queued"
    assert runtime.store.get("action", "a")["autonomy_pause"] is None


def test_pause_timeout_reconciles_when_worker_finally_lands_paused(runtime, monkeypatch):
    task = _pause_work(runtime)
    def pause(task_id, *, request_id):
        return runtime.tasks.command(task_id, "pausing", request_id=request_id)
    monkeypatch.setattr(runtime.controller, "pause", pause)
    runtime.command("pause")
    intent = runtime.store.get("action", "a")["autonomy_pause"]
    assert intent["task_version"] is None and intent["pending"] is True
    # The worker's late stop is the first evidence of the completed pause.
    runtime.tasks.command(task.id, "paused", request_id=f"stop-{task.id}-late")
    runtime._reconcile_pauses()
    action = runtime.store.get("action", "a")
    assert action["autonomy_pause"]["task_version"] == runtime.tasks.get(task.id).control_version
    after = runtime.tasks.events(task.id)[-1]["seq"]
    runtime.command("resume")
    # The real worker can already have claimed and paused the resumed task.
    # Check the durable transition instead of sampling that transient state.
    queued = [event for event in runtime.tasks.events(task.id, after=after)
              if event["type"] == "task.control" and event["data"]["operation"] == "queued"]
    assert len(queued) == 1 and queued[0]["data"]["state"] == "queued"
    assert runtime.store.get("action", "a")["autonomy_pause"] is None


def test_pause_timeout_then_new_owner_control_generation_is_not_resumed(runtime, monkeypatch):
    task = _pause_work(runtime)
    def pause(task_id, *, request_id):
        return runtime.tasks.command(task_id, "pausing", request_id=request_id)
    monkeypatch.setattr(runtime.controller, "pause", pause)
    runtime.command("pause")
    runtime.command("resume")
    runtime.store.command("pause")
    runtime.tasks.command(task.id, "paused", request_id=f"stop-{task.id}-late")
    runtime._reconcile_pauses()
    assert runtime.store.get("action", "a")["autonomy_pause"]["task_version"] is None
    runtime.store.command("resume", runtime.store.control()["version"])
    runtime._resume_work()
    assert runtime.tasks.get(task.id).state == "paused"


def test_resume_without_confirmed_generation_retains_unresolved_intent(runtime, monkeypatch):
    task = _pause_work(runtime)
    monkeypatch.setattr(runtime.controller, "pause", lambda task_id, **kw:
        runtime.tasks.command(task_id, "pausing", request_id=kw["request_id"]))
    runtime.command("pause")
    runtime.store.command("resume", runtime.store.control()["version"])
    runtime._resume_work()
    assert runtime.tasks.get(task.id).state == "pausing"
    assert runtime.store.get("action", "a")["autonomy_pause"]["pending"] is True


def test_resume_before_tick_completes_after_delayed_stop(runtime, monkeypatch):
    task = _pause_work(runtime)
    monkeypatch.setattr(runtime.controller, "pause", lambda task_id, **kw:
        runtime.tasks.command(task_id, "pausing", request_id=kw["request_id"]))
    resume_calls = []
    monkeypatch.setattr(runtime.controller, "resume", lambda task_id, **kw:
        resume_calls.append((task_id, kw)) or runtime.tasks.command(
            task_id, "queued", request_id="late-resume", expected_version=kw.get("expected_version")))
    runtime.command("pause")
    runtime.command("resume")
    pending = runtime.store.get("action", "a")["autonomy_pause"]
    assert pending["task_version"] is None
    assert runtime.tasks.get(task.id).state == "pausing"
    # Delayed worker stop; no tick and no second explicit resume.
    runtime.tasks.command(task.id, "paused", request_id=f"stop-{task.id}-late")
    runtime.tick(run_model=False)
    assert runtime.tasks.get(task.id).state == "queued"
    assert resume_calls and resume_calls[0][0] == task.id
    assert runtime.store.get("action", "a")["autonomy_pause"] is None


def test_owner_separate_pause_is_not_mistaken_for_delayed_worker_stop(runtime, monkeypatch):
    task = _pause_work(runtime)
    monkeypatch.setattr(runtime.controller, "pause", lambda task_id, **kw:
        runtime.tasks.command(task_id, "pausing", request_id=kw["request_id"]))
    resumed = []
    monkeypatch.setattr(runtime.controller, "resume", lambda *args, **kw: resumed.append(args))
    runtime.command("pause")
    # Same version delta as the worker's stop, but explicitly owner-caused.
    runtime.tasks.command(task.id, "paused", request_id="owner-pause-separately")
    runtime.command("resume")
    runtime.tick(run_model=False)
    assert resumed == []
    assert runtime.tasks.get(task.id).state == "paused"


def test_deleted_work_with_unconfirmed_pause_does_not_break_resume(runtime, monkeypatch):
    task = _pause_work(runtime)
    monkeypatch.setattr(runtime.controller, "pause", lambda task_id, **kw:
        runtime.tasks.command(task_id, "pausing", request_id=kw["request_id"]))
    runtime.command("pause")
    runtime.tasks.command(task.id, "paused", request_id=f"stop-{task.id}-late")
    runtime.tasks.delete(task.id, expected_version=runtime.tasks.get(task.id).control_version)
    runtime.command("resume")
    assert runtime.store.control()["state"] == "enabled"


def test_real_controller_resume_before_old_worker_exit_is_not_lost(runtime, monkeypatch):
    task = _pause_work(runtime, state="pending")
    started, release, continued = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def worker(item, cancel):
        calls.append(item.id)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        else:
            continued.set()
        return "paused"
    runtime.controller.run = worker
    monkeypatch.setattr(runtime.controller, "_wait_inactive", lambda task_id: None)
    try:
        runtime.controller.submit(task.id, request_id="real-submit")
        assert started.wait(2)
        runtime.command("pause")
        assert runtime.tasks.get(task.id).state == "paused"  # resource stop persisted, worker still unwinding
        runtime.command("resume")
        assert not continued.is_set()
        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not continued.is_set():
            runtime.tick(run_model=False)
            time.sleep(.02)
        assert continued.is_set() and calls == [task.id, task.id]
        assert runtime.store.get("action", "a")["autonomy_pause"] is None
    finally:
        release.set()


def test_resume_before_tick_does_not_claim_a_failure_to_reach_paused(runtime, monkeypatch):
    task = _pause_work(runtime)
    monkeypatch.setattr(runtime.controller, "pause", lambda task_id, **kw:
        runtime.tasks.command(task_id, "pausing", request_id=kw["request_id"]))
    resume_calls = []
    monkeypatch.setattr(runtime.controller, "resume", lambda *args, **kw: resume_calls.append(args))
    runtime.command("pause")
    runtime.command("resume")
    # The owner acts again while the old worker is still unwinding.
    runtime.tasks.update_task(task.id, expected_version=runtime.tasks.get(task.id).control_version,
                              changes={"goal": "Owner edited during pause"})
    runtime.tasks.command(task.id, "paused", request_id=f"stop-{task.id}-late")
    runtime.tick(run_model=False)
    assert resume_calls == []
    assert runtime.tasks.get(task.id).state == "paused"
    assert runtime.store.get("action", "a")["autonomy_pause"]


def test_resume_before_tick_honors_approval_or_unknown_effect(runtime, monkeypatch):
    task = _pause_work(runtime)
    monkeypatch.setattr(runtime.controller, "pause", lambda task_id, **kw:
        runtime.tasks.command(task_id, "pausing", request_id=kw["request_id"]))
    resume_calls = []
    monkeypatch.setattr(runtime.controller, "resume", lambda *args, **kw: resume_calls.append(args))
    runtime.command("pause")
    runtime.command("resume")
    runtime.tasks.interaction_create(task_id=task.id, run_id=None, kind="approval",
        payload={"tool": "bash", "arguments": {"command": "x"}, "reason": "owner"},
        control_version=runtime.tasks.get(task.id).control_version)
    runtime.tasks.command(task.id, "paused", request_id=f"stop-{task.id}-late")
    runtime.tick(run_model=False)
    assert resume_calls == []
    assert runtime.tasks.get(task.id).state == "paused"


def test_owner_paused_and_waiting_tasks_are_not_claimed_by_global_pause(runtime, monkeypatch):
    task = _pause_work(runtime, state="paused")
    calls = []
    monkeypatch.setattr(runtime.controller, "pause", lambda task_id, **kw: calls.append(task_id))
    runtime.command("pause")
    assert calls == []
    assert runtime.store.get("action", "a").get("autonomy_pause") is None
    assert runtime.tasks.get(task.id).state == "paused"
    runtime.command("pause")
    assert runtime.store.get("action", "a").get("autonomy_pause") is None


def test_resume_skips_pending_approval_or_unknown_effect(runtime):
    task = _pause_work(runtime)
    task = runtime.tasks.command(task.id, "paused", request_id="owner-pause")
    control_version = runtime.store.control()["version"]
    runtime.tasks.interaction_create(task_id=task.id, run_id=None, kind="approval",
        payload={"tool": "bash", "arguments": {"command": "x"}, "reason": "requires owner"},
        control_version=task.control_version)
    with runtime.store.transaction() as db:
        action = runtime.store.get("action", "a", db)
        runtime.store.put("action", "a", {**action, "autonomy_pause": {
            "control_version": control_version,
            "task_control_version": task.control_version,
            "task_version": runtime.tasks.get(task.id).control_version,
            "pending": False}}, "active", db)
    runtime.store.command("pause")
    runtime.store.command("resume", runtime.store.control()["version"])
    runtime._resume_work()
    assert runtime.tasks.get(task.id).state == "paused"
    assert runtime.store.get("action", "a")["autonomy_pause"]


def test_resume_rejects_owner_edit_of_a_paused_generation(runtime):
    task = _pause_work(runtime)
    controller = _PauseFixtureController(runtime)
    runtime.controller = controller
    runtime.command("pause")
    paused = runtime.tasks.get(task.id)
    assert paused.state == "paused"
    runtime.tasks.update_task(task.id, expected_version=paused.control_version,
                              changes={"goal": "A different goal chosen by the owner"})
    runtime.command("resume")
    assert runtime.tasks.get(task.id).state == "paused"
    assert runtime.store.get("action", "a")["autonomy_pause"]


def test_pause_of_queued_work_records_its_single_transition(runtime, monkeypatch):
    task = _pause_work(runtime, state="pending")
    controller = _PauseFixtureController(runtime)
    monkeypatch.setattr(runtime, "controller", controller)
    runtime.command("pause")
    paused = runtime.tasks.get(task.id)
    assert paused.state == "paused"
    assert runtime.store.get("action", "a")["autonomy_pause"]["task_version"] == paused.control_version
    runtime.command("resume")
    assert runtime.tasks.get(task.id).state == "queued"


def test_resume_rejects_unfinished_external_effect(runtime):
    task = _pause_work(runtime)
    run = runtime.tasks.begin_run(task.id)
    runtime.tasks.operation_begin(run_id=run["id"], tool_call_id="call",
        tool_name="bash", arguments={"command": "external-write"})
    runtime.tasks.mark_dispatching_unknown(run["id"])
    runtime.command("pause")
    assert runtime.tasks.unfinished_operations(task.id)
    runtime.command("resume")
    assert runtime.tasks.get(task.id).state == "paused"
    assert runtime.store.get("action", "a")["autonomy_pause"]


def test_dispatch_recovers_committed_intent_and_submit_gap_without_duplicate_task(runtime, monkeypatch):
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", {"title": "Actual uncertainty"}, "waiting", db)
        runtime.store.put("action", "a", {"goal_id": "g", "goal_version": 1,
            "title": "Read evidence", "goal": "Read the actual note", "sources": [],
            "scope": runtime.store.settings()}, "pending", db)
    attempts = []
    def interrupted_submit(task_id, **kwargs):
        attempts.append(task_id)
        raise RuntimeError("Process stopped before durable submit")
    monkeypatch.setattr(runtime.controller, "submit", interrupted_submit)
    with pytest.raises(RuntimeError, match="before durable submit"):
        runtime._dispatch()
    assert len(runtime.tasks.list()) == 1
    assert runtime.store.get("action", "a")["status"] == "active"
    with pytest.raises(RuntimeError):
        runtime._dispatch()
    assert attempts[0] == attempts[1]
    assert len(runtime.tasks.list()) == 1
    task = runtime.tasks.get(attempts[0])
    assert runtime.tasks.messages(task.id)[0]["kind"] == "autonomy"


def test_task_goal_edit_cannot_reuse_original_autonomy_authorization(runtime, monkeypatch):
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "g", {"title": "Actual uncertainty"}, "waiting", db)
        runtime.store.put("action", "a", {"goal_id": "g", "goal_version": 1,
            "title": "Read evidence", "goal": "Read the original note", "sources": [],
            "scope": runtime.store.settings()}, "pending", db)
    monkeypatch.setattr(runtime.controller, "submit", lambda *args, **kwargs: None)
    runtime._dispatch()
    action = runtime.store.get("action", "a")
    scope = runtime.task_scope(action["task_id"])
    scope.check()
    task = runtime.tasks.get(action["task_id"])
    runtime.tasks.update_task(task.id, expected_version=task.control_version, changes={"goal": "A different work objective"})
    with pytest.raises(Exception, match="旧自主授权需要重新审议"):
        scope.check()
    runtime._collect_results()
    runtime._dispatch()
    assert runtime.store.get("action", "a")["status"] == "changed"
    assert runtime.tasks.get(task.id).state == "paused"
    assert len(runtime.tasks.list()) == 1
    facts = [e for e in runtime.store.events() if e["source_key"] == "changed-work:a"]
    assert facts[0]["data"]["current_binding"]["goal"] == "A different work objective"


def test_elapsed_time_alone_is_not_evidence_for_a_goal_or_contact(runtime):
    fact = runtime.store.add_event("timer", "time_floor", {"due_at": iso()})
    decision = Decision.model_validate({"reason": "Time passed", "message": {
        "text": "Hello again", "reason": "Timer expired", "sources": [fact["id"]]}}).model_dump()
    with pytest.raises(ValueError, match="真实内容依据"):
        runtime._validate(decision, {"events": [fact]})
    decision = Decision.model_validate({"reason": "Time passed", "goals": [{
        "id": "g", "expected_version": 0, "title": "Invent a project", "question": "What to do?",
        "status": "candidate", "reason": "Timer expired", "understanding": "No actual experience",
        "sources": [fact["id"]]}]}).model_dump()
    with pytest.raises(ValueError, match="真实经历"):
        runtime._validate(decision, {"events": [fact]})


def test_observer_startup_failure_is_durable_gap_not_silent_no_change(runtime):
    runtime.configure({"observer_command": [sys.executable, "-c", "raise SystemExit(3)"]})
    runtime.tick(run_model=False)
    assert runtime.observer_error and runtime.observer is None
    gaps = [e for e in runtime.store.events() if e["kind"] == "observation_gap"]
    assert len(gaps) == 1
    assert "不能把没有收到事件" in gaps[0]["data"]["limitation"]
    runtime.observer_retry_at = 0
    runtime.tick(run_model=False)
    assert len([e for e in runtime.store.events() if e["kind"] == "observation_gap"]) == 1


def test_owner_pause_during_observer_query_is_not_reported_as_failure(runtime, monkeypatch):
    runtime._observe()
    observer = runtime.observer
    original = observer.call
    entered, release = threading.Event(), threading.Event()
    def held_call(name, args):
        if name == "status":
            entered.set()
            assert release.wait(5)
        return original(name, args)
    monkeypatch.setattr(observer, "call", held_call)
    worker = threading.Thread(target=runtime._observe)
    worker.start()
    assert entered.wait(2)
    runtime.command("pause")
    release.set()
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert runtime.observer is None and runtime.observer_error == ""
    assert not [e for e in runtime.store.events() if e["source_key"].startswith("observer-failure:")]


def test_real_observer_replacement_keeps_nexus_goals_evidence_messages_and_pause(runtime):
    fact = runtime.store.add_event("actual", "dialogue", {"text": "An unresolved actual question"})
    review = runtime.store.begin_review()
    runtime.store.commit_review(review, {"reason": "Unresolved question", "goals": [{
        "id": "g", "expected_version": 0, "title": "Open question", "sources": [fact["id"]], "status": "waiting"}],
        "watches": [{"id": "w", "expected_version": 0, "goal_id": "g", "path": runtime.store.settings()["read_roots"][0], "status": "active"}],
        "message": {"text": "Actual observation worth preserving", "sources": [fact["id"]]}})
    runtime.tick(run_model=False)
    first_epoch = runtime.store.meta("observer_epoch")
    message_id = runtime.store.messages()[0]["id"]
    runtime.configure({"observer_command": [sys.executable, "-m", "nexus.autonomy.observer"]})
    runtime.tick(run_model=False)
    assert not runtime.observer_error
    assert runtime.store.meta("observer_epoch") != first_epoch
    assert runtime.store.get("goal", "g")["version"] == 1
    assert runtime.store.messages()[0]["id"] == message_id
    assert runtime.store.event(fact["seq"])["data"]["text"] == "An unresolved actual question"
    assert any(e["source_key"].startswith("observer-replaced:") for e in runtime.store.events())
    runtime.withdraw("g", 1)
    runtime.command("pause")
    runtime.configure({"observer_command": []})
    runtime.store.recover()
    runtime.tick(run_model=False)
    assert runtime.observer is None
    assert runtime.store.control()["state"] == "paused"
    assert runtime.store.get("goal", "g")["status"] == "withdrawn"
    assert runtime.store.get("watch", "w")["status"] == "stopped"
    assert runtime.store.messages()[0]["id"] == message_id


def test_autonomy_http_controls_and_regular_history_are_separate(runtime):
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}") as client:
            snapshot = client.get("/api/autonomy").json()
            version = snapshot["control"]["version"]
            response = client.post("/api/autonomy/control", json={"operation": "pause", "expected_version": version})
            assert response.status_code == 200
            assert response.json()["control"]["state"] == "paused"
            assert client.post("/api/autonomy/control", json={"operation": "resume", "expected_version": version}).status_code == 409
            assert client.get("/api/autonomy").json()["control"]["state"] == "paused"
    finally:
        server.shutdown()
        server.server_close()


def test_owner_chat_interrupts_inflight_private_model_without_background_409(runtime, monkeypatch):
    started, closed = threading.Event(), threading.Event()
    real = httpx.AsyncClient
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.sleep(60)
            yield b"data: [DONE]\n\n"
        async def aclose(self):
            closed.set()
    def reply(request):
        body = json.loads(request.content)
        if "bounded private deliberation" in str(body):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=SlowStream())
        response = {"choices": [{"index": 0, "delta": {"content": "Owner interaction takes priority"}, "finish_reason": "stop"}]}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text="data: "+json.dumps(response)+"\n\ndata: [DONE]\n\n")
    monkeypatch.setattr(loop, "_client", lambda: real(transport=httpx.MockTransport(reply)))
    monkeypatch.setattr(runtime, "_observe", lambda: None)
    worker = threading.Thread(target=runtime.tick)
    worker.start()
    assert started.wait(5)
    session = runtime.state["sessions"].create("ordinary")
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        start = time.monotonic()
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=5) as client:
            response = client.post("/api/chat", json={"session_id": session.id, "text": "Owner input now"})
        assert response.status_code == 200
        assert "Owner interaction takes priority" in response.text
        assert time.monotonic()-start < 3
        assert closed.wait(1)
        assert not runtime.store.messages()
        assert runtime.store.budget()["unknown"] == 1
        assert runtime.store.control()["error"] == ""
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
