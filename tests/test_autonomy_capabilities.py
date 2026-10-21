"""Autonomy scope wiring through real task dispatch and assembly."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import pytest

from nexus.autonomy.runtime import AutonomyRuntime
from nexus.autonomy.scope import task_binding
from nexus.core.config import ConfigStore
from nexus.core.permission import ALLOW, ASK, DENY, Policy, Rule, default_policy
from nexus.model import APIFamily, ModelProfile
from nexus.runtime.assembly import TaskAssembler, _is_ling_task
from nexus.runtime.callbacks import TaskCallbacks
from nexus.task import TaskStore
from nexus.tools import build
from nexus.tools.registry import Registry, ToolSpec


def text_frames(text: str) -> str:
    return "".join([
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {"content": text},
                                            "finish_reason": None}]}) + "\n\n",
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + "\n\n",
        "data: [DONE]\n\n",
    ])


def profile() -> ModelProfile:
    return ModelProfile(
        api_family=APIFamily.OPENAI_CHAT_COMPLETIONS, model="fake",
        base_url="https://fake.test/v1", context_window_tokens=32_000,
        max_output_tokens=128,
    )


def call_frames(call_id: str, name: str, arguments: dict) -> str:
    return "".join([
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]}, "finish_reason": None}]}) + "\n\n",
        "data: " + json.dumps({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}) + "\n\n",
        "data: [DONE]\n\n",
    ])


def configure(tmp_path: Path):
    config = ConfigStore(tmp_path / "home")
    provider = config.ensure().provider
    config.update_provider(provider.id, {
        "base_url": "https://fake.test/v1",
        "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
        "models": [{"id": "fake", "context_window_tokens": 32_000,
                    "max_output_tokens": 1024}],
    })
    config.select(provider.id, "fake", "off")
    config.write_credential(provider.id, "test-key")
    config.save_preferences(workspace=str(tmp_path), permission="yolo")
    return config, provider.id


def task(tmp_path: Path, *, autonomous: bool = False):
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="scope", goal="write approved output", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    if autonomous:
        tasks.add_message(item.id, kind="autonomy", text="Inspect the admitted material.",
                          data={"autonomy_goal_id": "goal-1", "autonomy_action_id": "action-1"})
    else:
        tasks.add_message(item.id, kind="user", text="ordinary request")
    tasks.command(item.id, "running", request_id="start")
    tasks.begin_run(item.id)
    return config, tasks, item


def test_delegated_model_receives_explicit_current_and_accepted_directories(tmp_path, monkeypatch):
    data, output = tmp_path / "authorized-data", tmp_path / "authorized-output"
    data.mkdir()
    output.mkdir()
    config, tasks, item, runtime, _scope = autonomy_scope(
        tmp_path, current={"read_roots": [str(data)], "output_root": str(output)},
    )
    seen = []
    install(monkeypatch, [text_frames("No new work until the scope is understood")], seen)
    TaskAssembler(tasks, config, autonomy=runtime)(tasks.get(item.id), threading.Event())
    system = str(seen[0]["messages"])
    assert "current_owner_scope" in system and "accepted_action_scope" in system
    assert "authorized-data" in system and "authorized-output" in system
    assert "common ancestor, not an authorized read root" in system


def install(monkeypatch, responses: list[str], seen: list[dict]):
    import nexus.core.agent as module

    def handler(request):
        seen.append(json.loads(request.content))
        reply = responses.pop(0) if responses else text_frames("done")
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=reply)
    monkeypatch.setattr(
        module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def drive(agent, messages: list[dict]) -> list:
    async def run():
        return [event async for event in agent.run(messages)]
    import asyncio
    return asyncio.run(run())


def autonomy_scope(tmp_path: Path, *, current: dict | None = None,
                   accepted: dict | None = None, enabled: bool = True):
    config, tasks, item = task(tmp_path, autonomous=True)
    runtime = AutonomyRuntime(
        {"store": config, "tasks": tasks, "task_controller": None},
        threading.Lock(), lambda: None,
    )
    settings = {
        "read_roots": [str(tmp_path)], "output_root": str(tmp_path),
        "allowed_mcp_tools": [], "token_limit": 200_000,
        "max_output_tokens": 128,
    }
    settings.update(current or {})
    runtime.configure(settings)
    if enabled:
        runtime.command("enable")
    with runtime.store.transaction() as db:
        runtime.store.put(
            "goal", "goal-1",
            {"title": "Keep the admitted work bounded", "status": "active"},
            "active", db,
        )
        runtime.store.put(
            "action", "action-1",
            {"goal_id": "goal-1", "task_id": item.id,
             "task_binding": task_binding(item),
             "scope": dict(accepted or runtime.store.settings())},
            "active", db,
        )
    return config, tasks, item, runtime, runtime.task_scope(item.id)


def test_autonomy_rows_and_action_ids_select_continuation_without_fake_user(tmp_path):
    from nexus.task.models import Task

    item = Task(id="t", title="x", goal="x", workspace=str(tmp_path))
    assert _is_ling_task(item, [{"kind": "autonomy"}])
    assert _is_ling_task(item, [{"kind": "notice", "autonomy_goal_id": "g"}])
    assert _is_ling_task(item, [{"kind": "user", "source_session_id": "session"}])
    assert not _is_ling_task(item, [{"kind": "user"}])


def test_autonomy_task_uses_constrained_profile_and_bounded_workspace(tmp_path, monkeypatch):
    config, tasks, item, runtime, _scope = autonomy_scope(tmp_path)
    seen = []
    install(monkeypatch, [text_frames("done")], seen)
    result = TaskAssembler(tasks, config, autonomy=runtime)(
        tasks.get(item.id), threading.Event(),
    )

    assert result.status == "stalled"
    assert "续跑" in (result.wait_reason or "")
    assert seen[0]["max_tokens"] == 128
    system = seen[0]["messages"][0]["content"]
    transcript = "\n".join(message.get("content", "") for message in seen[0]["messages"])
    assert "not a new user instruction" in transcript
    assert "Bounded autonomous work" in system
    assert "AGENTS.md" not in system
    assert runtime.store.budget()["spent"] > 0


def test_autonomy_skills_come_only_from_admitted_roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    ancestor = workspace.parent
    read_root = workspace / "read-root"
    output_root = workspace / "output-root"
    for root, name in ((ancestor, "ancestor-only"), (read_root, "admitted-read"),
                       (output_root, "admitted-output")):
        skill_dir = root / ".nexus" / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name}\n---\nbody", encoding="utf-8",
        )

    config, tasks, item, runtime, _scope = autonomy_scope(
        workspace, current={"read_roots": [str(read_root)], "output_root": str(output_root)},
    )
    seen = []
    install(monkeypatch, [text_frames("done")], seen)
    TaskAssembler(tasks, config, autonomy=runtime)(
        tasks.get(item.id), threading.Event(),
    )
    system = seen[0]["messages"][0]["content"]
    assert "admitted-read" in system
    assert "admitted-output" in system
    assert "ancestor-only" not in system


def test_autonomy_skills_do_not_fall_back_to_the_common_ancestor(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    ancestor = workspace.parent
    read_root = workspace / "read-root"
    read_root.mkdir(parents=True)
    skill_dir = ancestor / ".nexus" / "skills" / "ancestor-only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: ancestor-only\ndescription: ancestor only\n---\nbody",
        encoding="utf-8",
    )
    config, tasks, item, runtime, _scope = autonomy_scope(
        workspace, current={"read_roots": [str(read_root)],
                            "output_root": str(workspace / "output-root")},
    )
    seen = []
    install(monkeypatch, [text_frames("done")], seen)
    TaskAssembler(tasks, config, autonomy=runtime)(
        tasks.get(item.id), threading.Event(),
    )
    assert "ancestor-only" not in seen[0]["messages"][0]["content"]


def test_skills_follow_the_current_and_accepted_intersection(tmp_path):
    parent = tmp_path / "data"
    old = parent / "old"
    kept = parent / "kept"
    sibling = parent / "sibling"
    output = tmp_path / "output"
    for path in (old, kept, sibling, output):
        path.mkdir(parents=True)
    _config, _tasks, _item, _runtime, scope = autonomy_scope(
        tmp_path,
        current={"read_roots": [str(parent)], "output_root": str(output)},
        accepted={"read_roots": [str(old), str(kept)], "output_root": str(output),
                  "allowed_mcp_tools": []},
    )
    assert scope.skill_roots() == [
        old / ".nexus" / "skills", kept / ".nexus" / "skills",
        output / ".nexus" / "skills",
    ]
    assert sibling / ".nexus" / "skills" not in scope.skill_roots()
    assert scope._readable(kept / "note.md")
    assert scope._readable(old / "note.md")
    assert not scope._readable(sibling / "note.md")


def test_guard_runs_at_dispatch_and_denies_before_executor(tmp_path, monkeypatch):
    from nexus.core.agent import ApprovalRequired

    _config, tasks, item, _runtime, scope = autonomy_scope(tmp_path)
    seen, executed = [], []
    install(monkeypatch, [call_frames("c1", "write_file", {"path": "../outside.txt", "content": "x"}),
                          text_frames("stopped")], seen)

    registry, context = build(root=tmp_path, task_callbacks=TaskCallbacks(
        tasks, item.id, tasks.current_run(item.id), cancel=threading.Event(),
    ))
    context.autonomy_scope = scope
    prepared = registry.prepare("write_file", {"path": "../outside.txt", "content": "x"}, context)
    callbacks = context.task_callbacks
    callbacks.autonomy_scope = scope
    with pytest.raises(ApprovalRequired, match="等待工具审批"):
        callbacks.prepare_dispatch("c1", prepared, context)
    assert executed == []
    assert not (tmp_path.parent / "outside.txt").exists()


def test_approved_mcp_call_is_guarded_once_then_executes(tmp_path, monkeypatch):
    from nexus.core.agent import Agent
    from nexus.core.workspace import Workspace
    from nexus.model import RuntimeCredentials

    _config, tasks, item, _runtime, scope = autonomy_scope(tmp_path)
    approvals = []
    scope.guard_tool = lambda name, arguments, context: approvals.append(name)
    registry, context = build(root=tmp_path, task_callbacks=TaskCallbacks(
        tasks, item.id, tasks.current_run(item.id), cancel=threading.Event(),
    ))
    context.autonomy_scope = scope
    context.task_callbacks.autonomy_scope = scope
    seen = []
    install(monkeypatch, [call_frames("c1", "write_file", {"path": "out.txt", "content": "ok"}),
                          text_frames("done")], seen)
    agent = Agent(
        profile=profile(), credentials=RuntimeCredentials("k"), registry=registry,
        context=context, workspace=Workspace(tmp_path), policy=default_policy("yolo"),
        on_tool_intent=context.task_callbacks.prepare_dispatch,
        execute_prepared=lambda prepared: context.task_callbacks.wrap_execute(
            prepared, context, registry.execute_prepared,
        ),
    )
    drive(agent, [{"role": "user", "content": "go"}])
    assert approvals == ["write_file"]
    assert (tmp_path / "out.txt").read_text() == "ok"


def test_autonomy_context_keeps_fresh_task_evidence_and_steps(tmp_path, monkeypatch):
    config, tasks, item, runtime, _scope = autonomy_scope(tmp_path)
    seen = []
    install(monkeypatch, [
        call_frames("c1", "task_evidence", {
            "kind": "observation", "source": "test",
            "summary": "fresh autonomous evidence", "ok": True,
        }),
        text_frames("done"),
    ], seen)
    result = TaskAssembler(tasks, config, autonomy=runtime)(
        tasks.get(item.id), threading.Event(),
    )
    assert result.status == "stalled"
    second_system = seen[1]["messages"][0]["content"]
    assert "fresh autonomous evidence" in second_system
    assert "Autonomous work provenance" in second_system
    assert "not a new user command" in second_system


def test_delegated_model_refreshes_owning_concern_without_changing_task(tmp_path, monkeypatch):
    config, tasks, item, runtime, _scope = autonomy_scope(tmp_path)
    concern = {"title": "Keep the admitted work bounded", "status": "active",
               "understanding": "initial premise"}
    with runtime.store.transaction() as db:
        runtime.store.put("goal", "goal-1", concern, "active", db)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            with runtime.store.transaction() as db:
                goal = runtime.store.get("goal", "goal-1", db)
                runtime.store.put("goal", "goal-1", {
                    **goal, "understanding": "real counterexample invalidates the initial premise",
                }, "active", db)
            reply = call_frames("c1", "task_evidence", {
                "kind": "observation", "source": "fixture", "summary": "read result", "ok": True,
            })
        else:
            reply = text_frames("Report the remaining unverified behavior")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=reply)

    import nexus.core.agent as module
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    TaskAssembler(tasks, config, autonomy=runtime)(tasks.get(item.id), threading.Event())
    assert "initial premise" in str(seen[0]["messages"][0])
    assert "real counterexample invalidates" in str(seen[1]["messages"][0])
    assert "NOT a new owner request" in str(seen[1]["messages"][0])
    assert tasks.get(item.id).effective_goal == item.effective_goal


def test_subagent_dispatch_does_not_repeat_the_parent_approval(tmp_path):
    _config, tasks, item, _runtime, scope = autonomy_scope(tmp_path)
    guards = []
    original_guard = scope.guard_tool
    def guard(name, arguments, context):
        guards.append(name)
        return original_guard(name, arguments, context)
    scope.guard_tool = guard
    registry, context = build(root=tmp_path, task_callbacks=TaskCallbacks(
        tasks, item.id, tasks.current_run(item.id), cancel=threading.Event(),
    ))
    context.autonomy_scope = scope
    prepared = registry.prepare("subagent", {"task": "inspect", "max_steps": 1}, context)
    context.task_callbacks.autonomy_scope = scope
    context.task_callbacks.prepare_dispatch("c1", prepared, context)
    assert guards == ["subagent"]

    child = context.task_callbacks.child_scope()
    assert child.autonomy_scope is scope
    child_context = type("ChildContext", (), {"_task_tool_call_id": "c2"})()
    child.prepare_dispatch("c2", prepared, child_context)
    assert guards == ["subagent", "subagent"]


def test_scope_policy_preauthorizes_output_but_keeps_explicit_deny(tmp_path):
    base = Policy(default=ASK, rules=[
        Rule(tool="write_file", pattern="*", action=ASK),
        Rule(tool="write_file", pattern="protected.txt", action=DENY),
    ])
    _config, _tasks, _item, _runtime, scope = autonomy_scope(tmp_path)
    policy = scope.policy(base)
    assert policy.decide("write_file", str(tmp_path / "ok.txt")) == ALLOW
    assert policy.decide("write_file", "protected.txt") == DENY


def test_paused_scope_does_not_send_a_model_request(tmp_path, monkeypatch):
    config, tasks, item, runtime, _scope = autonomy_scope(tmp_path, enabled=False)
    seen = []
    install(monkeypatch, [text_frames("must not send")], seen)
    result = TaskAssembler(tasks, config, autonomy=runtime)(
        tasks.get(item.id), threading.Event(),
    )
    assert result.status in ("waiting_user", "paused")
    assert seen == []


def test_mcp_exposure_uses_current_and_accepted_intersection(tmp_path):
    from pydantic import BaseModel

    class Args(BaseModel):
        pass

    _config, _tasks, _item, _runtime, scope = autonomy_scope(
        tmp_path,
        current={"allowed_mcp_tools": ["mcp__one__read", "mcp__two__write"]},
        accepted={"read_roots": [str(tmp_path)], "output_root": str(tmp_path),
                  "allowed_mcp_tools": ["mcp__one__read"]},
    )
    registry = Registry()
    for name in ("mcp__one__read", "mcp__two__write", "read_file"):
        registry.add(ToolSpec(name, name, Args, lambda context, args: {"content": ""}))
    filtered = scope.filter_mcp(registry)
    assert filtered.get("mcp__one__read") is not None
    assert filtered.get("mcp__two__write") is None
    assert filtered.get("read_file") is not None


def test_mcp_start_is_empty_without_an_admitted_tool(tmp_path, monkeypatch):
    from nexus.runtime import assembly

    config, tasks, item, runtime, _scope = autonomy_scope(tmp_path)
    calls = []
    monkeypatch.setattr(assembly, "start_mcp", lambda entries, root: calls.append(entries) or None)
    install(monkeypatch, [text_frames("done")], [])
    TaskAssembler(tasks, config, autonomy=runtime)(tasks.get(item.id), threading.Event())
    assert calls == [[]]


def test_mcp_start_receives_only_the_admitted_server_view(tmp_path, monkeypatch):
    from nexus.runtime import assembly

    config, tasks, item, runtime, _scope = autonomy_scope(
        tmp_path,
        current={"allowed_mcp_tools": ["mcp__one__read", "mcp__two__write"]},
        accepted={"read_roots": [str(tmp_path)], "output_root": str(tmp_path),
                  "allowed_mcp_tools": ["mcp__one__read"]},
    )
    config.write_config({**config.read_config(), "mcp": {"servers": [
        {"name": "one", "command": "python", "enabled": False},
        {"name": "two", "command": "python", "enabled": False},
    ]}})
    calls = []
    monkeypatch.setattr(assembly, "start_mcp", lambda entries, root: calls.append(entries) or None)
    install(monkeypatch, [text_frames("done")], [])
    TaskAssembler(tasks, config, autonomy=runtime)(tasks.get(item.id), threading.Event())
    assert len(calls) == 1
    assert [entry["name"] for entry in calls[0]] == ["one"]
    assert len(config.load().mcp_servers) == 2


def test_no_autonomy_runtime_leaves_task_behavior_unchanged(tmp_path, monkeypatch):
    config, tasks, item = task(tmp_path)
    seen = []
    install(monkeypatch, [text_frames("one reply")], seen)
    result = TaskAssembler(tasks, config)(tasks.get(item.id), threading.Event())
    assert result.status == "complete"
    assert len(seen) == 1
    assert seen[0].get("max_tokens") == 1024
