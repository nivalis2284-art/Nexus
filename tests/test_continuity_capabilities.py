"""Real stdio MCP remains usable by tasks and their isolated child agents."""

import json
import sys
import threading
from pathlib import Path

import httpx

from nexus.core import agent as loop
from nexus.runtime.assembly import TaskAssembler
from nexus.task import TaskStore
from test_task_runtime import configure, call_frames, text_frames


def setup(tmp_path, monkeypatch, responses):
    config, provider = configure(tmp_path)
    raw = config.read_config()
    raw["mcp"] = {"servers": [{"name": "stub", "command": sys.executable,
        "args": [str(Path(__file__).with_name("mcp_server_stub.py"))]}]}
    config.write_config(raw)
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="MCP work", goal="use the configured tool", workspace=str(tmp_path),
                        provider_id=provider, model_id="fake")
    store.command(task.id, "running", request_id="start")
    store.begin_run(task.id)
    seen = []
    def reply(request):
        seen.append(json.loads(request.content))
        assert responses, "unexpected extra model request"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=responses.pop(0))
    monkeypatch.setattr(loop, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    return config, store, task, seen


def test_task_calls_real_mcp_server_with_preserved_schema(tmp_path, monkeypatch):
    config, store, task, seen = setup(tmp_path, monkeypatch, [
        call_frames("mcp-one", "mcp__stub__echo", {"text": "task evidence 7319"}),
        text_frames("MCP checked"),
    ])
    result = TaskAssembler(store, config)(store.get(task.id), threading.Event())
    assert result.status == "complete"
    spec = next(tool["function"] for tool in seen[0]["tools"] if tool["function"]["name"] == "mcp__stub__echo")
    assert spec["parameters"]["required"] == ["text"]
    assert any(item.get("name") == "mcp__stub__echo" and item["text"] == "task evidence 7319"
               for item in store.messages(task.id))
    assert store.unfinished_operations(task.id) == []


def test_task_child_keeps_mcp_and_separate_operation_identity(tmp_path, monkeypatch):
    config, store, task, seen = setup(tmp_path, monkeypatch, [
        call_frames("same-id", "subagent", {"task": "echo the evidence", "max_steps": 4}),
        call_frames("same-id", "mcp__stub__echo", {"text": "child evidence 7319"}),
        text_frames("child completed: 7319"),
        text_frames("parent report"),
    ])
    result = TaskAssembler(store, config)(store.get(task.id), threading.Event())
    assert result.status == "complete"
    child_tools = {item["function"]["name"] for item in seen[1]["tools"]}
    assert "mcp__stub__echo" in child_tools
    assert "task_finish" not in child_tools
    assert "task_update" not in child_tools
    reports = [item for item in store.messages(task.id) if item.get("subagent")]
    assert reports and "7319" in reports[0]["text"]
    assert any(event["type"] == "tool_result" and "7319" in str(event)
               for event in reports[0]["trace"])
    assert store.unfinished_operations(task.id) == []
