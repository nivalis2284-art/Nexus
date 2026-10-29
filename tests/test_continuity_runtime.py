"""Continuity boundaries across model requests, child work, and MCP scope."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
from pathlib import Path

import httpx
import pytest

from nexus.attachments import AttachmentStore
from nexus.core.config import ConfigStore
from nexus.mcp import MCPRegistry, ServerConfig
from nexus.model import APIFamily
from nexus.runtime.assembly import TaskAssembler
from nexus.runtime.callbacks import TaskCallbacks
from nexus.runtime.connections import start_mcp
from nexus.task import TaskStore
from nexus.tools import build
from nexus.tools.files import file_effect


def test_file_effect_records_the_actual_workspace(tmp_path):
    (tmp_path / "before.txt").write_text("before", encoding="utf-8")
    registry, context = build(root=tmp_path)
    context.file_effect = file_effect
    prepared = registry.prepare("write_file", {"path": "before.txt", "content": "after"}, context)
    callbacks = TaskCallbacks(None, "task", "run", cancel=threading.Event())
    effect = callbacks.effect_for(prepared, context)
    assert effect["path"] == str(tmp_path / "before.txt")
    assert effect["before_sha256"] and effect["expected_changed"]

STUB = Path(__file__).resolve().parent / "mcp_server_stub.py"


def frame(delta, finish=None):
    return "data: " + json.dumps(
        {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    ) + "\n\n"


def text_frames(text):
    return frame({"content": text}) + frame({}, "stop") + "data: [DONE]\n\n"


def call_frames(call_id, name, arguments):
    return (
        frame({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
               "function": {"name": name, "arguments": json.dumps(arguments)}}]})
        + frame({}, "tool_calls") + "data: [DONE]\n\n"
    )


def configure(tmp_path):
    store = ConfigStore(tmp_path / "home")
    settings = store.ensure()
    provider = settings.provider
    store.update_provider(provider.id, {
        "base_url": "https://fake.test/v1",
        "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
        "models": [{"id": "fake", "context_window_tokens": 32000,
                    "max_output_tokens": 1024}],
    })
    store.select(provider.id, "fake", "off")
    store.write_credential(provider.id, "test-key")
    store.save_preferences(workspace=str(tmp_path), permission="yolo")
    return store, provider.id


def started_task(tmp_path, *, ling=False):
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    task = tasks.create(
        title="continuity", goal="inspect attachments", workspace=str(tmp_path),
        provider_id=provider_id, model_id="fake",
    )
    tasks.add_message(
        task.id, kind="user", text="begin",
        data={"source_session_id": "session-one"} if ling else None,
    )
    tasks.command(task.id, "running", request_id="start")
    tasks.begin_run(task.id)
    return config, tasks, task


def install_stream(monkeypatch, responses, seen):
    import nexus.core.agent as module

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            text=responses.pop(0),
        )
    monkeypatch.setattr(
        module, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def test_new_input_and_attachment_are_applied_once_at_the_next_request(tmp_path, monkeypatch):
    config, tasks, task = started_task(tmp_path)
    attachment = AttachmentStore(config.home).save(
        "tasks", task.id, "new.txt", "text/plain", io.BytesIO(b"new attachment"), 14,
    )
    responses = [call_frames("c1", "read_attachment", {"attachment_id": attachment["id"]}),
                 call_frames("finish", "task_finish", {"summary": "finished"})]
    seen = []
    install_stream(monkeypatch, responses, seen)
    # New input arrives only after the first request has actually gone out.
    # The stream helper's handler records after receiving, so enqueue through
    # the before-model boundary hook on the first completed response.
    callbacks_hook = TaskCallbacks.before_model
    first = {"done": False}
    def before_model(self, history, **kwargs):
        if not first["done"]:
            first["done"] = True
            self.store.accept_user_message(
                self.task_id, text="apply this once", request_id="new-input",
                data={"attachments": [attachment]},
            )
        return callbacks_hook(self, history, **kwargs)
    monkeypatch.setattr(TaskCallbacks, "before_model", before_model)
    result = TaskAssembler(tasks, config)(tasks.get(task.id), threading.Event())
    assert result.status == "complete"
    second = json.dumps(seen[1], ensure_ascii=False)
    assert second.count("apply this once") == 1
    assert attachment["id"] in second
    tool_messages = [m for m in tasks.messages(task.id) if m.get("kind") == "tool"]
    assert any(m.get("name") == "read_attachment" for m in tool_messages)
    assert tasks.input_status(task.id)["pending_count"] == 0


def test_child_uses_a_distinct_operation_scope_for_reused_call_id(tmp_path):
    _config, tasks, task = started_task(tmp_path)
    store = tasks
    callbacks = TaskCallbacks(store, task.id, store.current_run(task.id), cancel=threading.Event())
    child = callbacks.child_scope()
    callback_by_context = {}

    def invoke(owner, call_id, path):
        registry, context = build(root=tmp_path, task_callbacks=owner)
        prepared = registry.prepare(
            "write_file", {"path": path, "content": path}, context,
        )
        owner.prepare_dispatch(call_id, prepared, context)
        callback_by_context[path] = context._task_tool_call_id
        outcome = owner.wrap_execute(prepared, context, registry.execute_prepared)
        assert outcome.ok

    invoke(callbacks, "c0", "parent.txt")
    invoke(child, "c0", "child.txt")
    assert callback_by_context["parent.txt"] == "c0"
    assert callback_by_context["child.txt"].startswith("child-")
    assert not store.unfinished_operations(task.id)


def test_child_does_not_consume_parent_input_cursor(tmp_path):
    _config, tasks, task = started_task(tmp_path)
    callbacks = TaskCallbacks(
        tasks, task.id, tasks.current_run(task.id), cancel=threading.Event(),
    )
    child = callbacks.child_scope()
    history = callbacks.snapshot_history()
    tasks.accept_user_message(
        task.id, text="parent only", request_id="parent-input",
    )
    child_agent = type("Child", (), {"task_context": ""})()
    child.refresh_child_context(history, agent=child_agent)
    assert child_agent.task_context
    assert tasks.input_status(task.id)["pending_count"] == 1
    callbacks.before_model(history, agent=child_agent)
    assert history[-1]["role"] == "user"
    assert history[-1]["content"] == "parent only"
    assert history[-1]["_source_id"]
    assert tasks.input_status(task.id)["pending_count"] == 0


def test_completion_can_preserve_optional_verification_references(tmp_path):
    _config, tasks, task = started_task(tmp_path)
    callbacks = TaskCallbacks(
        tasks, task.id, tasks.current_run(task.id), cancel=threading.Event(),
    )
    callbacks.write_todos([{"content": "implement", "status": "completed"}])
    callbacks.add_evidence({
        "kind": "verification", "source": "test", "summary": "actual test output",
        "ok": True,
    })
    current = tasks.get(task.id)
    tasks.update_progress(
        task.id, expected_version=current.progress_version,
        changes={"steps": [{
            "content": current.steps[0].content, "status": "completed",
            "verification": "verified", "evidence": ["1"],
        }]},
    )
    completed = callbacks.finish({
        "summary": "done", "evidence_ids": ["1"], "unresolved": [],
    })
    assert completed["state"] == "completed"
    assert tasks.get(task.id).decisions[-1]["evidence_ids"] == ["1"]


@pytest.mark.parametrize("ling", [False, True])
def test_continuation_exceeds_old_limit_without_mandatory_progress_records(tmp_path, monkeypatch, ling):
    config, tasks, task = started_task(tmp_path, ling=ling)
    seen = []
    install_stream(monkeypatch, [text_frames("still working")] * 8 + [
        call_frames("finish", "task_finish", {"summary": "Finished the investigation."})], seen)
    result = TaskAssembler(tasks, config)(tasks.get(task.id), threading.Event())
    assert result.status == "complete"
    assert tasks.get(task.id).state == "completed"
    assert not tasks.get(task.id).blocked
    assert len(seen) == 9
    for request in seen:
        assert sum(m.get("role") == "user" and m.get("content") == "begin"
                   for m in request["messages"]) == 1


def test_manually_created_task_continues_until_explicit_pause(tmp_path, monkeypatch):
    config, tasks, task = started_task(tmp_path)
    seen = []
    install_stream(monkeypatch, [text_frames("one reply"),
        call_frames("pause", "task_pause", {"reason": "Waiting for more source material."})], seen)
    result = TaskAssembler(tasks, config)(tasks.get(task.id), threading.Event())
    assert result.status == "paused"
    assert tasks.get(task.id).state == "paused"
    assert len(seen) == 2


def test_mcp_startup_returns_no_shared_registry_when_disabled(tmp_path):
    config, _ = configure(tmp_path)
    assert start_mcp(config.load().mcp_servers, tmp_path) is None


def test_task_borrows_a_shared_mcp_registry_without_closing_it(tmp_path, monkeypatch):
    config, tasks, task = started_task(tmp_path)
    seen = []
    install_stream(monkeypatch, [call_frames("finish", "task_finish", {"summary": "done"})], seen)
    shared = MCPRegistry()
    shared.add(ServerConfig.from_dict({
        "name": "stub", "command": sys.executable, "args": [str(STUB)],
    }))
    server = shared.servers["stub"]
    try:
        result = TaskAssembler(tasks, config, mcp_registry=shared)(
            tasks.get(task.id), threading.Event()
        )
        assert result.status == "complete"
        assert shared.servers["stub"] is server
        assert server.connected
        assert "mcp__stub__echo" in [tool["function"]["name"] for tool in seen[0]["tools"]]
    finally:
        shared.close()


def test_task_failure_leaves_the_shared_mcp_registry_running(tmp_path, monkeypatch):
    config, tasks, task = started_task(tmp_path)
    install_stream(monkeypatch, [
        frame({"content": "cut off"}) + frame({}, "length") + "data: [DONE]\n\n",
    ], [])
    shared = MCPRegistry()
    shared.add(ServerConfig.from_dict({
        "name": "stub", "command": sys.executable, "args": [str(STUB)],
    }))
    server = shared.servers["stub"]
    try:
        result = TaskAssembler(tasks, config, mcp_registry=shared)(
            tasks.get(task.id), threading.Event()
        )
        assert result.status == "paused"
        assert shared.servers["stub"] is server
        assert server.connected
    finally:
        shared.close()
