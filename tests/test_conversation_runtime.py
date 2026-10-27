"""Main conversation: durable decisions, process ownership, reporting and live output."""
import sys
import threading
import pytest
from test_web_runtime import runtime as runtime
from test_web_runtime import model, stream, until
from test_ling import live as live
from test_ling import mock_model, frames
from nexus.task import TaskConflict
from nexus.session import Message
from nexus.web import server as web

def test_main_elapsed_is_saved(runtime, monkeypatch):
    handler, session, config, ling = runtime
    model(monkeypatch, [stream(text="done")])
    events = []
    web._application().run_turn(session, config.load(), events.append, ling=ling)
    done = next(event for event in events if event["type"] == "done")
    assert done["data"]["elapsed"] >= 0
    saved = web.STATE["sessions"].load(session.id)
    assert saved.items[-1].data["elapsed"] >= 0
    assert web._transcript(saved)[-1]["elapsed"] == saved.items[-1].data["elapsed"]


def test_main_job_does_not_release_project_before_process_exit(runtime, monkeypatch):
    handler, session, config, ling = runtime
    command = f'"{sys.executable}" -c "import time; time.sleep(1)"'
    model(monkeypatch, [stream(calls=[("bash_background", {"command": command})]), stream(text="started")])
    web._application().run_turn(session, config.load(), lambda _: None, ling=ling)
    controller = web.STATE["task_controller"]
    with pytest.raises(TaskConflict, match="project"):
        controller.acquire_write("sibling", config.load().workspace, threading.Event(), wait=False)
    def released():
        try:
            controller.acquire_write("sibling", config.load().workspace, threading.Event(), wait=False)
            return True
        except TaskConflict:
            return False
    until(released)
    controller.release_write("sibling")


def test_pending_main_approval_allows_chat_then_executes_once(live, monkeypatch):
    client, state, _, _ = live
    seen = []
    arguments = {"path": "approved.txt", "content": "7319"}
    def model(body):
        seen.append(body)
        last = body["messages"][-1]
        if last["role"] == "tool":
            return frames(text="operation complete")
        if "write now" in str(last.get("content")) or "[Owner answer to main request" in str(last.get("content")):
            return frames(calls=[("write_file", arguments)])
        return frames(text="unrelated conversation remains available")
    mock_model(monkeypatch, model)
    client.get("/api/ling")
    target = state["store"].home.parent / "approved.txt"
    result = client.post("/api/ling/input", json={"text": "write now", "request_id": "write"})
    assert result.status_code == 202
    pending = until(lambda: client.get("/api/ling").json().get("main_requests"))
    token = pending[0]["id"]
    until(lambda: not client.get("/api/ling").json()["running"])
    assert not target.exists()
    result = client.post("/api/ling/input", json={"text": "unrelated topic", "request_id": "other"})
    input_id = result.json()["input_id"]
    until(lambda: any(x["id"] == input_id and x["status"] == "done" for x in client.get("/api/ling").json()["inputs"]))
    assert client.get("/api/ling").json()["main_requests"][0]["id"] == token
    assert client.post("/api/approval", json={"token": token, "allowed": True}).status_code == 200
    until(lambda: target.exists())
    until(lambda: not client.get("/api/ling").json()["running"])
    assert target.read_text(encoding="utf-8") == "7319"
    snapshot = client.get("/api/ling").json()
    tools = [x for x in snapshot["items"] if x.get("name") == "write_file" and x.get("ok")]
    assert len(tools) == 1
    assert snapshot["main_requests"] == []
    requests_before = len(seen)
    assert client.post("/api/approval", json={"token": token, "allowed": True}).status_code == 200
    assert client.get("/api/ling").json()["queue_count"] == 0
    assert len(seen) == requests_before


def test_report_does_not_start_new_work_or_edit_owner_text(runtime, monkeypatch):
    handler, session, config, ling = runtime
    ling.current_report = True
    model(monkeypatch, [stream(calls=[("work_start", {"goal": "unrequested"}),
                                     ("save_instructions", {"text": "unrequested preference"})]),
                        stream(text="saved result explained")])
    web._application().run_turn(session, config.load(), lambda _: None, ling=ling)
    assert web.STATE["tasks"].list() == []
    assert not (config.home / "owner-instructions.txt").exists()
    saved = web.STATE["sessions"].load(session.id)
    denied = [m for m in saved.items if m.kind == "tool" and m.data.get("error") == "report_only"]
    assert len(denied) == 2


def test_workspace_switch_requires_approval_for_the_new_target(runtime, monkeypatch, tmp_path):
    handler, session, config, ling = runtime
    config.save_preferences(permission="ask")
    arguments = {"path": "approved.txt", "content": "7319"}
    model(monkeypatch, [stream(calls=[("write_file", arguments)])])
    web._application().run_turn(session, config.load(), lambda _: None, ling=ling)
    original = ling.decisions.pending()[0]
    ling.decisions.answer(original["id"], True)

    other = tmp_path / "other"
    other.mkdir()
    config.save_preferences(workspace=str(other))
    model(monkeypatch, [stream(calls=[("write_file", arguments)])])
    web._application().run_turn(session, config.load(), lambda _: None, ling=ling)
    pending = ling.decisions.pending()[0]
    assert pending["id"] != original["id"]
    assert pending["payload"]["workspace"] == str(other.resolve())
    assert not (other / "approved.txt").exists()
    assert not (tmp_path / "approved.txt").exists()

    ling.decisions.answer(pending["id"], True)
    model(monkeypatch, [stream(calls=[("write_file", arguments)]), stream(text="done")])
    web._application().run_turn(session, config.load(), lambda _: None, ling=ling)
    assert (other / "approved.txt").read_text() == "7319"
    assert not (tmp_path / "approved.txt").exists()


def test_thinking_only_live_output_is_visible_and_checkpoint_deduplicates(runtime):
    _, session, _, ling = runtime
    ling.running = True
    ling.live = {"thinking": "checking the saved facts"}
    snapshot = ling.projection(web._transcript)
    assert snapshot["items"][-1]["thinking"] == "checking the saved facts"
    assert snapshot["items"][-1]["text"] == ""
    assert snapshot["items"][-1]["live"] is True
    web.STATE["sessions"].append(session, Message(kind="assistant", text="", data={"thinking": "checking the saved facts"}))
    snapshot = ling.projection(web._transcript)
    assert len([m for m in snapshot["items"] if m.get("thinking") == "checking the saved facts"]) == 1
    ling.running = False


def test_answered_work_request_is_not_reported_as_pending_again(runtime):
    _, _, config, ling = runtime
    store = web.STATE["tasks"]
    task = store.create(title="work", goal="work", workspace=config.load().workspace)
    request = store.interaction_create(task_id=task.id, run_id=None, kind="approval",
                                       payload={"tool": "write_file", "arguments": {}},
                                       control_version=task.control_version)
    store.interaction_answer(request, answer=True, expected_version=task.control_version)
    assert not ling._report_current({"kind": "interaction", "interaction_id": request})
    ling.collect_reports()
    with store._transaction() as db:
        assert db.execute("SELECT count(*) FROM ling_inbox WHERE request_id LIKE 'work-report-%'").fetchone()[0] == 0
    assert store.notification_count() == 0
