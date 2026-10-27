"""Focused checks for retained model requests and the transport boundary."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from nexus.core.agent import Agent
from nexus.core.permission import default_policy
from nexus.core.retry import RetryPolicy
from nexus.core.workspace import inspect
from nexus.model import (
    APIFamily,
    ModelCapabilities,
    ModelProfile,
    RuntimeCredentials,
)
from nexus.model.gateway import ModelGateway
from nexus.model.request_history import RequestHistory
from nexus.tools import build

SSE = "text/event-stream"


def chat_reply(text: str = "done") -> str:
    return (
        "data: "
        + json.dumps({
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]
        })
        + "\n\ndata: [DONE]\n\n"
    )


def tool_reply(call_id: str = "c1") -> str:
    return (
        "data: "
        + json.dumps({
            "choices": [{
                "index": 0,
                "delta": {"tool_calls": [{
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": "glob", "arguments": '{"pattern":"*"}'},
                }]},
                "finish_reason": "tool_calls",
            }]
        })
        + "\n\ndata: [DONE]\n\n"
    )


def profile() -> ModelProfile:
    return ModelProfile(
        api_family=APIFamily.OPENAI_CHAT_COMPLETIONS,
        model="fake-model",
        base_url="https://fake.test/v1",
        capabilities=ModelCapabilities(tools=True),
        context_window_tokens=32_000,
        max_output_tokens=1024,
    )


def payload(text: str = "hello") -> dict:
    return {
        "model": "fake-model",
        "stream": True,
        "messages": [{"role": "user", "content": text}],
    }


def make_agent(tmp_path, history=None, scope=None, **kwargs) -> Agent:
    registry, context = build({})
    context.root = tmp_path
    return Agent(
        profile=profile(),
        credentials=RuntimeCredentials("test-key"),
        registry=registry,
        context=context,
        workspace=inspect(tmp_path),
        policy=default_policy("yolo"),
        request_history=history,
        request_scope=scope,
        **kwargs,
    )


def test_records_full_body_lists_summaries_and_pages(tmp_path):
    history = RequestHistory(tmp_path / "requests.sqlite3")
    body = payload("x" * 50_000)
    scope = {"kind": "ling", "owner_id": "ling-1", "source": "test"}
    first = history.record(body, scope, "fake-model", "openai_chat_completions")
    second = history.record(payload("two"), scope, "fake-model", "openai_chat_completions")
    third = history.record(payload("three"), scope, "fake-model", "openai_chat_completions")

    # The record is a detached copy of the values at send time.
    body["messages"][0]["content"] = "changed"
    scope["owner_id"] = "changed"
    detail = history.get(first)
    assert detail["body"]["messages"][0]["content"] == "x" * 50_000
    assert detail["owner_id"] == "ling-1"
    assert detail["status"] == "sent"
    assert detail["model"] == "fake-model"

    page = history.list(owner_id="ling-1", kind="ling", limit=2)
    assert [item["id"] for item in page["requests"]] == [third, second]
    assert page["next"] == second
    assert all("body" not in item for item in page["requests"])
    older = history.list(owner_id="ling-1", limit=2, before=str(page["next"]))
    assert [item["id"] for item in older["requests"]] == [first]
    assert older["next"] is None


def test_update_and_delete_owner_are_scoped(tmp_path):
    history = RequestHistory(tmp_path / "requests.sqlite3")
    mine = history.record(payload(), {"kind": "chat", "owner_id": "one"}, "m", "p")
    other = history.record(payload(), {"kind": "chat", "owner_id": "two"}, "m", "p")
    assert history.update(mine, "error", "http_error 503") is True
    assert history.update(999_999, "error", "missing") is False
    assert history.get(mine)["status"] == "error"
    assert history.get(mine)["error"] == "http_error 503"
    assert history.delete_owner("one") == 1
    assert history.get(mine) is None
    assert history.get(other) is not None


def test_expired_records_are_hidden_and_removed(tmp_path):
    path = tmp_path / "requests.sqlite3"
    history = RequestHistory(path)
    old = history.record(payload("old"), {"kind": "chat"}, "m", "p")
    old_time = (datetime.now(UTC) - timedelta(days=7, seconds=1)).isoformat()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE model_requests SET created_at = ? WHERE id = ?", (old_time, old))

    # Reopening deletes the expired row before the store is used.
    reopened = RequestHistory(path)
    assert reopened.get(old) is None
    assert reopened.list()["requests"] == []

    retained = reopened.record(payload("new"), {"kind": "chat"}, "m", "p")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE model_requests SET created_at = ? WHERE id = ?", (old_time, retained))
    assert reopened.list()["requests"] == []


def test_gateway_copies_the_wire_json_and_never_records_auth(tmp_path):
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-key"
        return httpx.Response(200, headers={"content-type": SSE}, text=chat_reply())

    history = RequestHistory(tmp_path / "requests.sqlite3")
    body = payload("wire body")
    body["client_header"] = "kept as data"

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = ModelGateway(
                client,
                request_history=history,
                request_scope={"kind": "chat", "owner_id": "chat-1"},
            )
            return [event async for event in gateway.stream(
                profile(), body, credentials=RuntimeCredentials("test-key")
            )]

    events = asyncio.run(run())
    assert events[-1].type == "complete"
    assert len(sent) == 1
    record = history.list()["requests"][0]
    detail = history.get(record["id"])
    assert detail["status"] == "complete"
    assert detail["body"] == sent[0]
    assert detail["scope"]["kind"] == "chat"
    assert detail["scope"]["owner_id"] == "chat-1"
    assert "test-key" not in json.dumps(detail, ensure_ascii=False)


def test_gateway_records_prepared_failure_without_claiming_it_was_sent(tmp_path):
    called = []
    history = RequestHistory(tmp_path / "requests.sqlite3")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: called.append(request) or httpx.Response(200)
        )) as client:
            gateway = ModelGateway(client, request_history=history)
            return [event async for event in gateway.stream(
                profile(),
                {"model": "fake-model", "stream": True},
                credentials=RuntimeCredentials("test-key"),
                scope={"kind": "task", "owner_id": "task-1"},
            )]

    events = asyncio.run(run())
    assert events[-1].error.code == "invalid_request"
    assert called == []
    record = history.list()["requests"][0]
    assert record["status"] == "prepared_failed"
    assert record["error"] == "invalid_request"


def test_unserializable_input_is_recorded_as_prepared_failed(tmp_path):
    called = []
    history = RequestHistory(tmp_path / "requests.sqlite3")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: called.append(request) or httpx.Response(200)
        )) as client:
            gateway = ModelGateway(client, request_history=history)
            body = payload()
            body["note"] = object()
            return [event async for event in gateway.stream(
                profile(), body, credentials=RuntimeCredentials("test-key")
            )]

    events = asyncio.run(run())
    assert events[-1].error.code == "invalid_request"
    assert called == []
    record = history.list()["requests"][0]
    assert record["status"] == "prepared_failed"
    assert history.get(record["id"])["body"] == {"unserializable_body": "dict"}


def test_agent_retries_record_each_http_attempt(tmp_path, monkeypatch):
    history = RequestHistory(tmp_path / "requests.sqlite3")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(503, headers={"content-type": SSE}, text="busy")
        return httpx.Response(200, headers={"content-type": SSE}, text=chat_reply("recovered"))

    monkeypatch.setattr(
        "nexus.core.agent._client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    agent = make_agent(
        tmp_path,
        history,
        {"kind": "ling", "owner_id": "ling-1"},
        retry=RetryPolicy(max_retries=1, initial_delay=0, max_delay=0, jitter_ratio=0),
    )

    async def run():
        return [event async for event in agent.run([{"role": "user", "content": "go"}])]

    events = asyncio.run(run())
    assert events[-1].data["stopped"] == "complete"
    records = history.list()["requests"]
    assert len(records) == 2
    assert [item["status"] for item in reversed(records)] == ["error", "complete"]
    assert [item["kind"] for item in records] == ["ling", "ling"]
    assert history.get(records[-1]["id"])["error"] == "http_error 503"
    assert history.get(records[0]["id"])["body"] == calls[1]


def test_agent_prepares_plugins_per_request_without_writing_history(tmp_path, monkeypatch):
    responses = [tool_reply(), chat_reply("finished")]
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": SSE}, text=responses.pop(0))

    monkeypatch.setattr(
        "nexus.core.agent._client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    history = RequestHistory(tmp_path / "requests.sqlite3")
    agent = make_agent(
        tmp_path,
        history,
        {"kind": "task", "owner_id": "task-42"},
    )
    original = dict(agent.registry._tools)

    class Plugins:
        def __init__(self):
            self.calls = 0

        def prepare_agent(self, target, messages):
            self.calls += 1
            target.plugin_context = f"plugin context {self.calls}"
            target.request_materials = [{"content": f"recall {self.calls}", "source": "mem"}]
            target.request_plugins = [{"id": "mem", "status": "used"}]
            # Tool schema is rebuilt by the plugin manager; add it only to the
            # disposable registry used by the first request.
            if self.calls == 1:
                target.registry._tools = dict(original)
            else:
                from pydantic import BaseModel

                from nexus.tools.registry import ToolSpec

                class Empty(BaseModel):
                    pass

                extra = ToolSpec("plugin_only", "temporary", Empty, lambda ctx, args: {})
                target.registry._tools = {**original, "plugin_only": extra}
            return [*deepcopy(messages), {"role": "user", "content": f"temporary {self.calls}"}]

    agent.plugins = Plugins()
    messages = [{"role": "user", "content": "go"}]

    async def run():
        return [event async for event in agent.run(messages)]

    asyncio.run(run())
    assert seen[0]["messages"][-1]["content"] == "temporary 1"
    assert seen[1]["messages"][-1]["content"] == "temporary 2"
    assert "plugin context 1" in seen[0]["messages"][0]["content"]
    assert "plugin context 2" in seen[1]["messages"][0]["content"]
    assert "plugin_only" not in {item["function"]["name"] for item in seen[0]["tools"]}
    assert "plugin_only" in {item["function"]["name"] for item in seen[1]["tools"]}
    assert all(message.get("content") != "temporary 1" for message in messages)
    records = history.list()["requests"]
    assert len(records) == 2
    for record in records:
        assert record["kind"] == "task"
        assert record["owner_id"] == "task-42"
        assert record["scope"]["request_materials"][0]["source"] == "mem"
        assert record["scope"]["request_plugins"][0]["id"] == "mem"
    assert agent.request_materials[0]["content"] == "recall 2"


def test_summary_requests_use_summary_kind_and_owner(tmp_path, monkeypatch):
    history = RequestHistory(tmp_path / "requests.sqlite3")
    monkeypatch.setattr(
        "nexus.core.agent._client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": SSE}, text=chat_reply("summary"))
        )),
    )
    agent = make_agent(
        tmp_path,
        history,
        {"kind": "task", "owner_id": "task-7"},
    )
    messages = [
        {"role": "user", "content": "u " * 8000},
        {"role": "assistant", "content": "a " * 8000},
        {"role": "user", "content": "latest"},
    ]

    async def fold():
        return await agent._fold(messages, messages, 32000)

    folded, report = asyncio.run(fold())
    assert report.summarised is True
    assert "latest" in folded[-1]["content"]
    records = history.list(kind="summary")["requests"]
    assert len(records) == 1
    assert records[0]["owner_id"] == "task-7"
    assert records[0]["status"] == "complete"
    assert history.get(records[0]["id"])["materials"] == []
    assert history.get(records[0]["id"])["plugins"] == []
    assert messages[-1]["content"] == "latest"


def test_plugin_prepare_failure_is_recorded_before_the_gateway(tmp_path, monkeypatch):
    history = RequestHistory(tmp_path / "requests.sqlite3")
    sent = []
    monkeypatch.setattr(
        "nexus.core.agent._client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: sent.append(request) or httpx.Response(200)
        )),
    )
    agent = make_agent(
        tmp_path,
        history,
        {"kind": "ling", "owner_id": "ling-9"},
    )

    class Plugins:
        def prepare_agent(self, target, messages):
            raise ValueError("必需的插件资料不可用: memory: offline")

    agent.plugins = Plugins()

    async def run():
        return [event async for event in agent.run([{"role": "user", "content": "go"}])]

    with pytest.raises(ValueError, match="memory: offline"):
        asyncio.run(run())
    assert sent == []
    records = history.list(owner_id="ling-9", kind="ling")["requests"]
    assert len(records) == 1
    assert records[0]["status"] == "prepared_failed"
    assert records[0]["error"] == "plugin_context: 必需的插件资料不可用: memory: offline"
    assert records[0]["scope"]["request_plugins"] == [{
        "status": "failed",
        "phase": "prepare",
        "error": "必需的插件资料不可用: memory: offline",
    }]
    assert history.get(records[0]["id"])["body"] is None
    assert history.get(records[0]["id"])["materials"] == []
    assert "test-key" not in json.dumps(history.get(records[0]["id"]), ensure_ascii=False)
