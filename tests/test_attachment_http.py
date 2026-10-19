"""Actual HTTP file lifecycle plus agent tool and task-history integration."""

import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from nexus.attachments import AttachmentStore, MAX_FILE_BYTES
from nexus.task import TaskStore
from nexus.web import server as web
from test_task_http import task_live as task_live, request
from test_web_runtime import runtime as runtime, model, stream


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


def test_session_upload_download_owner_and_deletion(task_live):
    base, _, _ = task_live
    _, session = request(base, "/api/sessions", "POST", {})
    _, other = request(base, "/api/sessions", "POST", {})
    data = "# 附件正文\nHTTP upload".encode()
    status, item = upload(base, "sessions", session["id"], "中文说明.md", data)
    assert status == 201 and item["status"] == "ready"
    with urllib.request.urlopen(base + item["url"]) as response:
        assert response.read() == data
        assert "filename*=UTF-8''" in response.headers["Content-Disposition"]
    status, _ = request(base, item["url"].replace(session["id"], other["id"]))
    assert status == 404
    status, _ = request(base, f"/api/sessions/{session['id']}", "DELETE")
    assert status == 200
    assert not Path(item["original_path"]).exists()


def test_task_attachment_only_input_survives_reopen_and_is_cleaned_on_delete(task_live):
    base, store, runs = task_live
    _, task = request(base, "/api/tasks", "POST", {"title": "files", "goal": "use later"})
    status, item = upload(base, "tasks", task["id"], "data.csv", b"item,count\nalpha,3")
    assert status == 201
    status, _ = request(base, f"/api/tasks/{task['id']}/messages", "POST", {
        "text": "", "attachments": [item["id"]], "request_id": "attachment-message",
    })
    assert status == 200 and runs == []
    reopened = TaskStore(store.path)
    assert reopened.messages(task["id"])[0]["attachments"][0]["id"] == item["id"]
    assert item["id"] in json.dumps(reopened.messages_for_model(task["id"]))
    assert "alpha,3" not in json.dumps(reopened.messages_for_model(task["id"]))
    status, detail = request(base, f"/api/tasks/{task['id']}")
    assert detail["messages"][0]["attachments"][0]["url"] == item["url"]
    status, _ = request(base, f"/api/tasks/{task['id']}", "DELETE", {
        "confirm": True, "expected_version": detail["control_version"],
    })
    assert status == 200 and not Path(item["original_path"]).exists()


def test_cross_task_reference_and_oversized_upload_are_rejected(task_live):
    base, store, _ = task_live
    _, one = request(base, "/api/tasks", "POST", {"title": "one", "goal": "one"})
    _, two = request(base, "/api/tasks", "POST", {"title": "two", "goal": "two"})
    _, item = upload(base, "tasks", one["id"], "private.txt", b"owner one")
    status, _ = request(base, f"/api/tasks/{two['id']}/messages", "POST", {
        "text": "read", "attachments": [item["id"]], "request_id": "wrong-owner",
    })
    assert status == 400
    assert not store.messages(two["id"])
    status, value = upload(base, "tasks", one["id"], "big.bin", b"", size=MAX_FILE_BYTES + 1)
    assert status == 400 and "25 MiB" in value["error"]


def test_agent_reads_attachment_body_through_real_tool(runtime, monkeypatch):
    handler, session, config = runtime
    store = AttachmentStore(config.home)
    body = b"evidence: 7319\nnext line"
    item = store.save("sessions", session.id, "evidence.txt", "text/plain", io.BytesIO(body), len(body))
    web.STATE["sessions"].append(session, web.Message(kind="user", text="read the attached evidence",
                                                    data={"attachments": [item]}))
    seen = model(monkeypatch, [stream(calls=[("read_attachment", {"attachment_id": item["id"]})]),
                               stream(text="the evidence is 7319")])
    events = []
    handler._run_turn(session, config.load(), events.append)
    assert len(seen) == 2
    tool = next(message for message in seen[1]["messages"] if message["role"] == "tool")
    assert "7319" in tool["content"]
    restored = web.STATE["sessions"].load(session.id)
    assert any(row.get("attachments") for row in web._transcript(restored))
