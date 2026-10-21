"""A terminal task tool stops subsequent calls from the same model response."""
import json
import threading

import httpx
import pytest
from test_task_runtime import configure, frame

from nexus.core import agent as module
from nexus.runtime.assembly import TaskAssembler
from nexus.task import TaskStore


@pytest.mark.parametrize("name,arguments,state,status", [
    ("task_finish", {"summary": "Delivered the requested result."}, "completed", "complete"),
    ("task_pause", {"reason": "Waiting for source material."}, "paused", "paused"),
])
@pytest.mark.parametrize("permission", ["ask", "yolo"])
def test_task_end_prevents_later_side_effects_in_the_same_response(
    tmp_path, monkeypatch, name, arguments, state, status, permission,
):
    config, provider = configure(tmp_path)
    config.save_preferences(permission=permission)
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="terminal", goal="finish or pause", workspace=str(tmp_path),
                        provider_id=provider, model_id="fake")
    store.command(task.id, "running", request_id="start")
    store.begin_run(task.id)
    calls = [
        {"index": 0, "id": "end", "type": "function",
         "function": {"name": name, "arguments": json.dumps(arguments)}},
        {"index": 1, "id": "late", "type": "function",
         "function": {"name": "write_file", "arguments": json.dumps(
             {"path": "should-not-exist.txt", "content": "late side effect"})}},
    ]
    requests = []

    def reply(request):
        requests.append(request)
        assert len(requests) == 1, "a finished or paused task requested another model turn"
        text = frame({"tool_calls": calls}) + frame({}, "tool_calls") + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=text)

    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    result = TaskAssembler(store, config)(store.get(task.id), threading.Event())
    assert result.status == status
    assert store.get(task.id).state == state
    assert not (tmp_path / "should-not-exist.txt").exists()
    assert store.unfinished_operations(task.id) == []
    assert not store.pending_interactions(task.id)
    if state == "paused":
        assert store.get(task.id).wait_reason == arguments["reason"]
    assert len(requests) == 1
