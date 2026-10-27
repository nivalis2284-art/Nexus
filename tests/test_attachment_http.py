"""Actual HTTP file lifecycle plus real agent tool and task-history integration."""

import io
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest
import test_web_runtime

from nexus.attachments import MAX_FILE_BYTES, AttachmentStore
from nexus.core import agent as loop
from nexus.runtime import TaskController
from nexus.session import Message, SessionStore
from nexus.task import TaskStore
from nexus.web import server as web


def request(base, path, method="GET", body=None):
    payload = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        base + path, data=payload, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def upload(base, kind, owner, name, data, size=None):
    req = urllib.request.Request(
        f"{base}/api/{kind}/{owner}/attachments?name={quote(name)}", data=data,
        headers={"Content-Type": "application/octet-stream",
                 "Content-Length": str(len(data) if size is None else size)}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def attachment_live(tmp_path, monkeypatch):
    config = web.ConfigStore(tmp_path / "home")
    config.ensure()
    config.save_preferences(workspace=str(tmp_path), permission="ask")
    tasks = TaskStore(config.home / "tasks.sqlite3")
    runs = []
    controller = TaskController(tasks, run=lambda item, cancel: runs.append(item.id) or "paused")
    state = {
        "store": config, "sessions": SessionStore(config.sessions_dir),
        "tasks": tasks, "task_controller": controller, "skills": {}, "closing": False,
    }
    monkeypatch.setattr(web, "STATE", state)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield SimpleNamespace(
            base=f"http://127.0.0.1:{httpd.server_address[1]}",
            store=tasks, state=state, config=config, runs=runs,
        )
    finally:
        controller.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def stream(*, text=None, calls=None):
    delta = {"content": text} if text is not None else {"tool_calls": [
        {"index": index, "id": f"c{index}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(arguments)}}
        for index, (name, arguments) in enumerate(calls)
    ]}

    def frame(value, finish=None):
        return "data: " + json.dumps({"id": "fake", "choices": [
            {"index": 0, "delta": value, "finish_reason": finish}
        ]}) + "\n\n"

    return frame(delta) + frame({}, "stop" if text is not None else "tool_calls") + "data: [DONE]\n\n"


def install_model(monkeypatch, responses, seen):
    def reply(request):
        seen.append(json.loads(request.content))
        assert responses, "unexpected extra model request"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=responses.pop(0)
        )
    monkeypatch.setattr(
        loop, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply))
    )


def test_session_upload_download_owner_and_deletion(attachment_live):
    base = attachment_live.base
    sessions = attachment_live.state["sessions"]
    session = sessions.create("fake")
    other = sessions.create("fake")
    data = "# 附件正文\nHTTP upload".encode()
    status, item = upload(base, "sessions", session.id, "中文说明.md", data)
    assert status == 201 and item["status"] == "ready"
    with urllib.request.urlopen(base + item["url"]) as response:
        assert response.read() == data
        assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
    status, _ = request(base, item["url"].replace(session.id, other.id))
    assert status == 404
    status, _ = request(base, f"/api/sessions/{session.id}", "DELETE")
    assert status == 200
    assert not Path(item["original_path"]).exists()


def test_task_attachment_only_input_survives_reopen_and_is_cleaned_on_delete(attachment_live):
    base = attachment_live.base
    task = attachment_live.store.create(
        title="files", goal="use later", workspace=str(attachment_live.config.home)
    )
    status, item = upload(base, "tasks", task.id, "data.csv", b"item,count\nalpha,3")
    assert status == 201
    status, _ = request(base, f"/api/tasks/{task.id}/messages", "POST", {
        "text": "", "attachments": [item["id"]], "request_id": "attachment-message",
    })
    assert status == 200 and attachment_live.runs == []
    reopened = TaskStore(attachment_live.store.path)
    assert reopened.messages(task.id)[0]["attachments"][0]["id"] == item["id"]
    assert item["id"] in json.dumps(reopened.messages_for_model(task.id))
    assert "alpha,3" not in json.dumps(reopened.messages_for_model(task.id))
    status, detail = request(base, f"/api/tasks/{task.id}")
    assert detail["messages"][0]["attachments"][0]["url"] == item["url"]
    status, _ = request(base, f"/api/tasks/{task.id}", "DELETE", {
        "confirm": True, "expected_version": detail["control_version"],
    })
    assert status == 200 and not Path(item["original_path"]).exists()


def test_cross_task_reference_and_oversized_upload_are_rejected(attachment_live):
    base = attachment_live.base
    store = attachment_live.store
    one = store.create(title="one", goal="one", workspace=str(attachment_live.config.home))
    two = store.create(title="two", goal="two", workspace=str(attachment_live.config.home))
    _, item = upload(base, "tasks", one.id, "private.txt", b"owner one")
    status, _ = request(base, f"/api/tasks/{two.id}/messages", "POST", {
        "text": "read", "attachments": [item["id"]], "request_id": "wrong-owner",
    })
    assert status == 400
    assert not store.messages(two.id)
    status, value = upload(base, "tasks", one.id, "big.bin", b"", size=MAX_FILE_BYTES + 1)
    assert status == 400 and "25 MiB" in value["error"]


def test_agent_reads_attachment_body_through_real_tool(tmp_path, monkeypatch):
    fixture = test_web_runtime.runtime.__wrapped__(tmp_path, monkeypatch)
    handler, session, config, ling = next(fixture)
    sessions = web.STATE["sessions"]
    store = AttachmentStore(config.home)
    body = b"evidence: 7319\nnext line"
    item = store.save("sessions", session.id, "evidence.txt", "text/plain", io.BytesIO(body), len(body))
    sessions.append(session, Message(
        kind="user", text="read the attached evidence", data={"attachments": [item]}
    ))
    seen = []
    install_model(monkeypatch, [
        stream(calls=[("read_attachment", {"attachment_id": item["id"]})]),
        stream(text="the evidence is 7319"),
    ], seen)
    web._application().run_turn(session, config.load(), lambda event: None, ling=ling)
    assert len(seen) == 2
    tool = next(message for message in seen[1]["messages"] if message["role"] == "tool")
    assert "7319" in tool["content"]
    restored = sessions.load(session.id)
    assert any(row.get("attachments") for row in web._transcript(restored))
    assert ling.session_id == session.id
    with pytest.raises(StopIteration):
        next(fixture)
