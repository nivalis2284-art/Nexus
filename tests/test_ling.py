"""Continuing interaction admission, independent work and recoverable results."""

import io
import json
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from nexus.attachments import AttachmentStore
from nexus.core import agent as loop
from nexus.ling import LingInbox, WorkService
from nexus.model import APIFamily
from nexus.runtime import TaskController
from nexus.runtime.callbacks import TaskCallbacks
from nexus.session import Message
from nexus.task import TaskConflict, TaskStore
from nexus.web import server as web


def frames(text=None, calls=None):
    delta = {"content": text} if text is not None else {"tool_calls": [
        {"index": index, "id": f"c{index}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}
        for index, (name, args) in enumerate(calls)]}
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta,
        "finish_reason": "stop" if text is not None else "tool_calls"}]}) + "\n\ndata: [DONE]\n\n"


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition did not become true")


@pytest.fixture
def live(tmp_path, monkeypatch):
    config = web.ConfigStore(tmp_path / "home")
    settings = config.ensure()
    config.update_provider(settings.provider.id, {
        "base_url": "https://fake.test/v1", "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
        "models": [{"id": "fake", "context_window_tokens": 32000, "max_output_tokens": 1024,
                    "capabilities": {"images": True}}],
    })
    config.select(settings.provider.id, "fake", "off")
    config.write_credential(settings.provider.id, "fake-key")
    config.save_preferences(workspace=str(tmp_path), permission="ask")
    store = TaskStore(tmp_path / "home" / "tasks.sqlite3")
    release_task, task_started = threading.Event(), threading.Event()
    def run(task, cancel):
        task_started.set()
        while not cancel.is_set() and not release_task.wait(0.02):
            pass
        return "paused"
    controller = TaskController(store, run=run)
    controller.start()
    state = {"store": config, "sessions": web.SessionStore(config.sessions_dir),
             "tasks": store, "task_controller": controller, "skills": {},
             "closing": False}
    monkeypatch.setattr(web, "STATE", state)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=10)
    yield client, state, task_started, release_task
    release_task.set()
    agent = state.get("ling") and state["ling"].agent
    if agent:
        agent.stop()
    if state.get("ling"):
        state["ling"].close()
    controller.close()
    server.shutdown()
    server.server_close()
    client.close()
    for jobs in state.get("jobs", {}).values():
        jobs.close_all()


def mock_model(monkeypatch, handler):
    real = httpx.AsyncClient
    def reply(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=handler(json.loads(request.content)))
    monkeypatch.setattr(loop, "_client", lambda: real(transport=httpx.MockTransport(reply)))


def test_ling_delegates_then_converses_and_queries_running_work(live, monkeypatch):
    client, state, started, release = live
    seen = []
    def model(body):
        seen.append(body)
        last = body["messages"][-1]
        if last["role"] == "tool":
            return frames(text="Independent work accepted; I am here.")
        content = str(last.get("content"))
        if "start work" in content:
            return frames(calls=[("work_start", {"goal": "long independent work", "title": "Research"})])
        if "progress" in content:
            return frames(calls=[("work_list", {})])
        return frames(text="Unrelated answer while work continues.")
    mock_model(monkeypatch, model)
    first = client.get("/api/ling").json()
    session_id = first["session_id"]
    assert client.post("/api/ling/input", json={"text": "start work", "request_id": "one"}).status_code == 202
    assert started.wait(4)
    until(lambda: not client.get("/api/ling").json()["running"])
    owner = state["tasks"].list()[0]
    assert owner.state == "running"
    assert client.post("/api/ling/input", json={"text": "unrelated question", "request_id": "two"}).status_code == 202
    until(lambda: any(item["status"] == "done" and item["id"] == 2
                      for item in client.get("/api/ling").json()["inputs"]))
    assert state["tasks"].get(owner.id).state == "running"
    client.post("/api/ling/input", json={"text": "progress", "request_id": "three"}).raise_for_status()
    until(lambda: any(item["id"] == 3 and item["status"] == "done"
                      for item in client.get("/api/ling").json()["inputs"]))
    assert client.get("/api/ling").json()["session_id"] == session_id
    assert any("work_list" in json.dumps(body["messages"]) for body in seen)
    # No raw background execution transcript is added to Ling's history.
    transcript = client.get("/api/sessions/" + session_id).json()["items"]
    assert sum(item["role"] == "user" for item in transcript) == 3
    assert not any("task_finish" in item.get("text", "") for item in transcript)
    release.set()


def test_inputs_are_durable_idempotent_and_do_not_abort_current_reply(live, monkeypatch):
    client, state, _, _ = live
    entered, release = threading.Event(), threading.Event()
    def model(body):
        if str(body["messages"][-1].get("content")) == "hold":
            entered.set()
            assert release.wait(5)
        return frames(text="answer")
    mock_model(monkeypatch, model)
    client.get("/api/ling").raise_for_status()
    first = client.post("/api/ling/input", json={"text": "hold", "request_id": "hold"}).json()
    try:
        assert entered.wait(4)
        second = client.post("/api/ling/input", json={"text": "next", "request_id": "next"})
        assert second.status_code == 202
        retry = client.post("/api/ling/input", json={"text": "next", "request_id": "next"})
        assert retry.json()["input_id"] == second.json()["input_id"]
        assert client.post("/api/ling/input", json={"text": "different", "request_id": "next"}).status_code == 409
        assert not state["ling"].agent.stopped
    finally:
        release.set()
    until(lambda: len([item for item in client.get("/api/ling").json()["inputs"] if item["status"] == "done"]) == 2)
    session = state["sessions"].load(first["session_id"])
    assert [item.text for item in session.items if item.kind == "user"] == ["hold", "next"]


def test_delegated_attachments_get_independent_originals(live):
    _, state, _, _ = live
    session = state["sessions"].create("fake")
    attachment = AttachmentStore(state["store"].home).save(
        "sessions", session.id, "evidence.txt", "text/plain", io.BytesIO(b"evidence 7319"), 13)
    state["sessions"].append(session, Message(kind="user", text="use file", data={"attachments": [attachment]}))
    service = WorkService(state, session, state["store"].load(), 7)
    task = service.start(goal="read the file")
    retry = service.start(goal="read the file")
    assert retry["id"] == task["id"]
    message = state["tasks"].messages(task["id"])[0]
    forwarded = message["attachments"][0]
    assert forwarded["id"] != attachment["id"]
    assert "tasks" in forwarded["original_path"]
    AttachmentStore(state["store"].home).delete_owner("sessions", session.id)
    assert AttachmentStore(state["store"].home).get("tasks", task["id"], forwarded["id"])["size"] == 13


def test_restart_holds_unstarted_inputs_without_replaying_interrupted_work(live):
    _, state, _, _ = live
    lock = threading.Lock()
    lock.acquire()
    calls = []
    first = LingInbox(state, lambda *args: calls.append(True), lock)
    first.enqueue("saved", [], [], "pending")
    first.close()
    lock.release()
    with state["tasks"]._transaction() as connection:
        connection.execute("INSERT INTO ling_inbox(request_id,session_id,text,data_json,status,created_at) VALUES(?,?,?,?,?,?)",
                           ("interrupted", first.session_id, "unknown effects", "{}", "running", "now"))
    second = LingInbox(state, lambda *args: calls.append(True), lock)
    try:
        assert second.session_id == first.session_id
        projection = second.projection(web._transcript)
        assert {item["status"] for item in projection["inputs"]} == {"held", "interrupted"}
        assert calls == []
        second.resume()
        until(lambda: calls)
        assert len(calls) == 1
    finally:
        second.close()


def test_amendment_does_not_resume_paused_work_or_duplicate_forwarded_files(live):
    _, state, _, _ = live
    task = state["tasks"].create(title="paused", goal="hold", workspace=state["store"].load().workspace)
    state["tasks"].command(task.id, "paused", request_id="pause")
    session = state["sessions"].create("fake")
    attachment = AttachmentStore(state["store"].home).save(
        "sessions", session.id, "note.txt", "text/plain", io.BytesIO(b"note"), 4)
    state["sessions"].append(session, Message(kind="user", text="amend", data={"attachments": [attachment]}))
    service = WorkService(state, session, state["store"].load(), 10)
    first = service.message(task.id, "preserve this change", [attachment["id"]])
    second = service.message(task.id, "preserve this change", [attachment["id"]])
    assert first["seq"] == second["seq"]
    assert state["tasks"].get(task.id).state == "paused"
    assert state["task_controller"].queued_ids() == []
    assert len(state["tasks"].messages(task.id)) == 1
    folder = AttachmentStore(state["store"].home).owner_path("tasks", task.id)
    assert len(list(folder.iterdir())) == 1


def test_saved_long_records_can_be_read_beyond_preview(live):
    _, state, _, _ = live
    task = state["tasks"].create(title="history", goal="read", workspace=state["store"].load().workspace)
    state["tasks"].add_message(task.id, kind="tool", text="x" * 7000 + "final-evidence")
    service = WorkService(state, state["sessions"].create("fake"), state["store"].load())
    first = service.read(task.id, "messages", limit=1)
    assert first["items"][0]["text_offsets"]["text"]["next_offset"] == 6000
    second = service.read(task.id, "messages", limit=1, text_offset=6000)
    assert "final-evidence" in second["items"][0]["text"]


def test_ling_identity_is_separate_from_deletable_ordinary_sessions(live):
    client, state, _, _ = live
    identity = client.get("/api/ling").json()["session_id"]
    normal = state["sessions"].create("fake")
    listed = client.get("/api/state").json()["sessions"]
    assert identity not in {item["id"] for item in listed}
    assert client.delete("/api/sessions/" + identity).status_code == 409
    assert client.delete("/api/sessions/" + normal.id).status_code == 200


def test_stopping_ling_interaction_does_not_cancel_independent_work(live, monkeypatch):
    client, state, task_started, _ = live
    task = state["tasks"].create(title="keep running", goal="independent work", workspace=state["store"].load().workspace)
    state["task_controller"].submit(task.id, request_id="start-independent")
    assert task_started.wait(3)
    entered, release = threading.Event(), threading.Event()
    def model(body):
        entered.set()
        assert release.wait(5)
        return frames(text="partial response")
    mock_model(monkeypatch, model)
    client.get("/api/ling").raise_for_status()
    client.post("/api/ling/input", json={"text": "hold interaction", "request_id": "stop-me"}).raise_for_status()
    try:
        assert entered.wait(3)
        assert client.post("/api/ling/stop").json()["stopped"] is True
        assert state["tasks"].get(task.id).state == "running"
        assert state["task_controller"].active_task == task.id
    finally:
        release.set()
    until(lambda: client.get("/api/ling").json()["inputs"][0]["status"] == "cancelled")
    assert state["tasks"].get(task.id).state == "running"


@pytest.mark.parametrize("phrase", ["停一下", "先停一下", "暂停", "停止", "stop", "stop now"])
def test_bare_stop_stops_main_only_without_a_model_call(live, monkeypatch, phrase):
    client, state, started, _ = live
    task = state["tasks"].create(
        title="background", goal="background",
        workspace=state["store"].load().workspace, read_only=True,
    )
    state["task_controller"].submit(task.id, request_id="bare-stop-work")
    assert started.wait(3)
    calls = []
    mock_model(monkeypatch, lambda body: calls.append(body) or frames(text="unexpected model call"))

    accepted = client.post("/api/ling/input", json={
        "text": phrase, "request_id": "bare-stop-" + phrase,
    })
    assert accepted.status_code == 202
    until(lambda: any(item["id"] == accepted.json()["input_id"] and item["status"] == "done"
                      for item in client.get("/api/ling").json()["inputs"]))

    assert calls == []
    assert state["tasks"].get(task.id).state == "running"
    assert state["task_controller"].is_active(task.id)
    transcript = client.get("/api/ling").json()["items"]
    assert any(item["role"] == "user" and item["text"] == phrase for item in transcript)
    assert any(item["role"] == "notice" and "后台工作继续" in item["text"] for item in transcript)


def test_task_targeted_stop_pauses_only_that_work(live, monkeypatch):
    client, state, started, _ = live
    workspace = state["store"].load().workspace
    first = state["tasks"].create(title="first", goal="first", workspace=workspace, read_only=True)
    second = state["tasks"].create(title="second", goal="second", workspace=workspace, read_only=True)
    state["task_controller"].submit(first.id, request_id="target-first")
    state["task_controller"].submit(second.id, request_id="target-second")
    assert started.wait(3)
    until(lambda: state["task_controller"].is_active(first.id)
          and state["task_controller"].is_active(second.id))
    calls = []
    mock_model(monkeypatch, lambda body: calls.append(body) or frames(text="unexpected model call"))

    accepted = client.post("/api/ling/input", json={
        "text": "暂停", "task_id": second.id, "request_id": "target-stop-second",
    })
    assert accepted.status_code == 202
    until(lambda: state["tasks"].get(second.id).state == "paused")
    until(lambda: any(item["id"] == accepted.json()["input_id"] and item["status"] == "done"
                      for item in client.get("/api/ling").json()["inputs"]))

    assert calls == []
    assert state["tasks"].get(first.id).state == "running"
    assert state["task_controller"].is_active(first.id)
    transcript = client.get("/api/ling").json()["items"]
    assert any(item["role"] == "notice" and "所选工作" in item["text"] for item in transcript)


@pytest.mark.parametrize("phrase", ["暂停全部工作", "暂停所有工作"])
def test_bare_pause_all_never_calls_the_model(live, monkeypatch, phrase):
    client, state, started, _ = live
    workspace = state["store"].load().workspace
    tasks = [state["tasks"].create(title=name, goal=name, workspace=workspace, read_only=True)
             for name in ("one", "two")]
    state["task_controller"].submit(tasks[0].id, request_id=phrase + "-one")
    state["task_controller"].submit(tasks[1].id, request_id=phrase + "-two")
    assert started.wait(3)
    calls = []
    mock_model(monkeypatch, lambda body: calls.append(body) or frames(text="unexpected model call"))

    accepted = client.post("/api/ling/input", json={"text": phrase, "request_id": phrase})
    assert accepted.status_code == 202
    until(lambda: any(item["id"] == accepted.json()["input_id"] and item["status"] == "done"
                      for item in client.get("/api/ling").json()["inputs"]))
    until(lambda: all(state["tasks"].get(task.id).state in ("paused", "pausing") for task in tasks))

    assert calls == []
    transcript = client.get("/api/ling").json()["items"]
    assert any(item["role"] == "notice" and "全部工作" in item["text"] for item in transcript)


def test_explicit_start_cannot_supersede_a_pending_decision(live):
    _, state, _, _ = live
    store = state["tasks"]
    task = store.create(title="decision", goal="hold", workspace=state["store"].load().workspace)
    current = store.command(task.id, "running", request_id="begin-decision")
    interaction = store.interaction_create(task_id=task.id, run_id=None, kind="approval",
        payload={"tool": "write_file", "arguments": {"path": "file.txt", "content": "x"}},
        control_version=current.control_version)
    current = store.command(task.id, "waiting_user", request_id="wait-decision")
    with pytest.raises(TaskConflict, match="pending interaction"):
        state["task_controller"].submit(task.id, request_id="wrong-start", expected_version=current.control_version)
    assert store.get(task.id).control_version == current.control_version
    store.interaction_answer(interaction, answer=True, expected_version=current.control_version)


def test_agent_completion_delivers_a_ling_work_report(live):
    _, state, _, _ = live
    ling = web._ling()
    store = state["tasks"]
    task = store.create(title="report", goal="report", workspace=state["store"].load().workspace)
    store.command(task.id, "running", request_id="report-running")
    TaskCallbacks(store, task.id, "report-run", cancel=threading.Event()).finish(
        {"summary": "verified result 7319"}
    )
    report = next(item for item in store.notifications(unread_only=True)
                  if item["task_id"] == task.id and item["kind"] == "completed")
    assert report["source"] == "agent"
    ling.collect_reports()
    with store._transaction() as connection:
        row = connection.execute("SELECT * FROM ling_inbox WHERE request_id=?",
                                 ("work-report-" + str(report["id"]),)).fetchone()
    assert row is not None
    assert "verified result 7319" in row["text"]
