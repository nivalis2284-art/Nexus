"""Repeated tool failures remain in durable history until the task stops."""

from __future__ import annotations

import json
import threading

import httpx

from nexus.core.config import ConfigStore
from nexus.model import APIFamily
from nexus.runtime.assembly import TaskAssembler
from nexus.task import TaskStore


def frame(delta, finish=None):
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


def call_stream(name, arguments, call_id="f1"):
    return (frame({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}]})
        + frame({}, "tool_calls") + "data: [DONE]\n\n")


def text_stream(text="not done"):
    return frame({"content": text}) + frame({}, "stop") + "data: [DONE]\n\n"


def configure(tmp_path):
    store = ConfigStore(tmp_path / "home")
    settings = store.ensure()
    provider = settings.provider
    store.update_provider(provider.id, {
        "base_url": "https://fake.test/v1",
        "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
        "models": [{"id": "fake", "context_window_tokens": 32000, "max_output_tokens": 1024}],
    })
    store.select(provider.id, "fake", "off")
    store.write_credential(provider.id, "test-key")
    store.save_preferences(workspace=str(tmp_path), permission="yolo")
    return store, provider.id


def test_repeated_identical_failure_is_persisted_for_resume(tmp_path, monkeypatch):
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="stop", goal="retry the broken command", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    tasks.command(item.id, "running", request_id="run")
    tasks.begin_run(item.id)
    responses = [call_stream("read_file", {"path": "missing-file"}, str(n)) for n in range(3)]
    responses.append(call_stream("task_pause", {"reason": "The source file is missing."}, "pause"))
    import nexus.core.agent as module
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=responses.pop(0))
    )))
    TaskAssembler(tasks, config)(tasks.get(item.id), threading.Event())
    messages = tasks.messages(item.id)
    assert any(message.get("kind") == "tool" and "missing-file" in str(message.get("arguments")) for message in messages)
    assert sum(message.get("kind") == "tool" and message.get("name") == "read_file"
               for message in messages) == 3
    assert tasks.get(item.id).state == "paused"
    assert tasks.get(item.id).wait_reason == "The source file is missing."
    assert tasks.get(item.id).work_summary != "completed"
