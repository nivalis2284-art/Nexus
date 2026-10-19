"""Agent hooks preserve validated dispatch and legal suspension boundaries."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from nexus.core.agent import Agent, AgentSuspended
from nexus.core.permission import default_policy
from nexus.core.workspace import inspect
from nexus.model import APIFamily, ModelCapabilities, ModelProfile, RuntimeCredentials
from nexus.tools import build


def profile():
    return ModelProfile(
        api_family=APIFamily.OPENAI_CHAT_COMPLETIONS,
        model="fake", base_url="https://fake.test/v1",
        capabilities=ModelCapabilities(tools=True),
        context_window_tokens=32000, max_output_tokens=1024,
    )


def frame(delta, finish=None):
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


def call_stream(call_id, name, arguments):
    return (
        frame({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
               "function": {"name": name, "arguments": json.dumps(arguments)}}]})
        + frame({}, "tool_calls") + "data: [DONE]\n\n"
    )


def test_task_context_and_message_hook_are_used(tmp_path, monkeypatch):
    import nexus.core.agent as module
    import httpx

    seen = []
    texts = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=frame({"content": "done"}) + frame({}, "stop") + "data: [DONE]\n\n")
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    class Callbacks:
        def update_work(self, changes):
            return {"progress_version": 2}
    registry, context = build(root=tmp_path, task_callbacks=Callbacks())
    agent = Agent(profile=profile(), credentials=RuntimeCredentials("k"), registry=registry,
                  context=context, workspace=inspect(tmp_path), task_context="GOAL: persist me",
                  on_message=lambda message: texts.append(message.get("content", "")))
    async def drive():
        return [event async for event in agent.run([{"role": "user", "content": "go"}])]
    asyncio.run(drive())
    assert "GOAL: persist me" in seen[0]["messages"][0]["content"]
    assert texts == ["done"]


def test_suspension_leaves_current_tool_call_unresolved(tmp_path, monkeypatch):
    import nexus.core.agent as module
    import httpx
    responses = [call_stream("call1", "write_file", {"path": "x", "content": "x"})]
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=responses.pop(0))
    )))
    registry, context = build(root=tmp_path)
    async def approve(request):
        raise AgentSuspended("approval")
    agent = Agent(profile=profile(), credentials=RuntimeCredentials("k"), registry=registry,
                  context=context, workspace=inspect(tmp_path), policy=default_policy("ask"), approve=approve)
    messages = [{"role": "user", "content": "go"}]
    async def drive():
        return [event async for event in agent.run(messages)]
    try:
        asyncio.run(drive())
    except AgentSuspended:
        pass
    assert not (tmp_path / "x").exists()
    assert [message["role"] for message in messages] == ["user", "assistant"]


def test_tool_intent_hook_runs_before_dispatch(tmp_path, monkeypatch):
    import nexus.core.agent as module
    import httpx
    responses = [
        call_stream("call1", "task_update", {"next_step": "x"}),
        frame({"content": "done"}) + frame({}, "stop") + "data: [DONE]\n\n",
    ]
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=responses.pop(0))
    )))
    class Callbacks:
        def update_work(self, changes):
            return {"progress_version": 2}
    registry, context = build(root=tmp_path, task_callbacks=Callbacks())
    order = []
    def intent(call_id, prepared, ctx):
        order.append(("intent", call_id, prepared.spec.name))
    def execute(prepared):
        order.append(("execute", prepared.spec.name))
        from nexus.tools.registry import ToolResult
        return ToolResult(True, "ok")
    agent = Agent(profile=profile(), credentials=RuntimeCredentials("k"), registry=registry,
                  context=context, workspace=inspect(tmp_path),
                  on_tool_intent=intent, execute_prepared=execute)
    async def drive():
        return [event async for event in agent.run([{"role": "user", "content": "go"}])]
    asyncio.run(drive())
    assert order == [("intent", "call1", "task_update"), ("execute", "task_update")]
