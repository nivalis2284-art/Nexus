"""Exercise web assembly, real agent/tool execution and JSONL reload together."""

from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from nexus.core import agent as loop
from nexus.core import compaction
from nexus.model import APIFamily
from nexus.web import server as web


def stream(*, text=None, calls=None):
    delta = {"content": text} if text is not None else {"tool_calls": [
        {"index": i, "id": f"c{i}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(calls)
    ]}
    def frame(value, finish=None):
        return "data: " + json.dumps({"id": "fake", "choices": [
            {"index": 0, "delta": value, "finish_reason": finish}
        ]}) + "\n\n"
    return frame(delta) + frame({}, "stop" if text is not None else "tool_calls") + "data: [DONE]\n\n"


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    store = web.ConfigStore(tmp_path / "home")
    settings = store.ensure()
    provider = settings.provider
    store.update_provider(provider.id, {
        "base_url": "https://fake.test/v1", "family": APIFamily.OPENAI_CHAT_COMPLETIONS,
        "models": [{"id": "fake", "context_window_tokens": 32000,
                    "max_output_tokens": 1024, "capabilities": {"images": True}}],
    })
    store.select(provider.id, "fake", "off")
    store.write_credential(provider.id, "fake-key")
    store.save_preferences(workspace=str(tmp_path), permission="yolo")
    monkeypatch.setattr(web, "STATE", {"store": store, "sessions": web.SessionStore(store.sessions_dir)})
    monkeypatch.setattr(web, "APPROVALS", web.Approvals())
    monkeypatch.setattr(web, "RUNNING", {"agent": None})
    handler = object.__new__(web.Handler)
    session = web.STATE["sessions"].create("fake")
    web.STATE["sessions"].append(session, web.Message(kind="user", text="task"))
    yield handler, session, store
    for jobs in web.STATE.get("jobs", {}).values():
        jobs.close_all()
    if web.STATE.get("mcp"):
        web.STATE["mcp"].close()


def model(monkeypatch, responses):
    seen = []
    def reply(request):
        seen.append(json.loads(request.content))
        assert responses, "unexpected extra model request"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=responses.pop(0))
    monkeypatch.setattr(loop, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    return seen


def test_compaction_keeps_full_transcript_and_new_answer(runtime, monkeypatch):
    handler, session, store = runtime
    for i in range(10):
        web.STATE["sessions"].append(session, web.Message(kind="user", text="old-" + str(i) + "x" * 1500))
    settings = store.load()
    settings.context_limit = 2000
    async def fold(self, original, trimmed, window):
        return [{"role": "user", "content": "checkpoint"}], compaction.CompactionReport(summarised=True, epochs=1)
    monkeypatch.setattr(loop.Agent, "_fold", fold)
    model(monkeypatch, [stream(text="durable answer")])
    events = []
    handler._run_turn(session, settings, events.append)
    saved = web.STATE["sessions"].load(session.id)
    assert len(saved.items) == 12
    assert saved.items[-1].text == "durable answer"
    assert saved.items[1].text.startswith("old-0")
    assert "compacted" in [e["type"] for e in events]


def test_mcp_start_schema_call_failure_and_reload(runtime, monkeypatch):
    handler, session, store = runtime
    raw = store.read_config()
    raw["mcp"] = {"servers": [{"name": "stub", "command": sys.executable,
                               "args": [str(Path(__file__).with_name("mcp_server_stub.py"))]}]}
    store.write_config(raw)
    web.STATE["mcp"] = web.start_mcp(store.load().mcp_servers, Path(store.load().workspace))
    assert web.STATE["mcp"] is not None
    seen = model(monkeypatch, [stream(calls=[("mcp__stub__echo", {"text": "hello MCP"}),
                                           ("mcp__stub__fail", {})]), stream(text="finished")])
    handler._run_turn(session, store.load(), lambda e: None)
    spec = next(s["function"] for s in seen[0]["tools"] if s["function"]["name"] == "mcp__stub__echo")
    assert spec["parameters"]["required"] == ["text"]
    assert spec["parameters"]["properties"]["text"]["type"] == "string"
    tools = [m for m in web._transcript(web.STATE["sessions"].load(session.id)) if m["role"] == "tool"]
    assert tools[0]["text"] == "hello MCP"
    assert tools[0]["arguments"] == {"text": "hello MCP"}
    assert tools[1]["ok"] is False
    assert tools[1]["error"] == "execution_failed"


def test_subagent_shares_approval_channel(runtime, monkeypatch):
    handler, session, store = runtime
    store.save_preferences(permission="ask")
    seen = model(monkeypatch, [stream(calls=[("subagent", {"task": "write child.txt"})]),
                              stream(calls=[("write_file", {"path": "child.txt", "content": "child"})]),
                              stream(text="child finished"), stream(text="parent finished")])
    approvals = []
    def emit(event):
        if event["type"] == "approval":
            approvals.append(event)
            assert web.APPROVALS.resolve(event["token"], True)
    handler._run_turn(session, store.load(), emit)
    assert (Path(store.load().workspace) / "child.txt").read_text() == "child"
    assert [e["tool"] for e in approvals] == ["write_file"]
    assert len(seen) == 4


def test_background_task_survives_turn_but_is_session_scoped(runtime, monkeypatch):
    handler, session, store = runtime
    command = f'"{sys.executable}" -c "print(123)"'
    model(monkeypatch, [stream(calls=[("bash_background", {"command": command})]), stream(text="started"),
                        stream(calls=[("job_list", {})]), stream(text="listed"),
                        stream(calls=[("job_list", {})]), stream(text="empty")])
    handler._run_turn(session, store.load(), lambda e: None)
    web.STATE["sessions"].append(session, web.Message(kind="user", text="list"))
    events = []
    handler._run_turn(session, store.load(), events.append)
    assert any(e["type"] == "tool_result" and "job_001" in e["tool"]["content"] for e in events)
    other = web.STATE["sessions"].create("fake")
    web.STATE["sessions"].append(other, web.Message(kind="user", text="list"))
    events = []
    handler._run_turn(other, store.load(), events.append)
    assert any(e["type"] == "tool_result" and "(no background jobs)" in e["tool"]["content"] for e in events)


def test_cancelled_multiple_calls_remain_paired(runtime, monkeypatch):
    handler, session, store = runtime
    model(monkeypatch, [stream(calls=[("write_file", {"path": "one", "content": "1"}),
                                     ("write_file", {"path": "two", "content": "2"})])])
    events = []
    def emit(event):
        events.append(event)
        if event["type"] == "tool_result":
            web.RUNNING["agent"].stop()
    handler._run_turn(session, store.load(), emit)
    messages = web._internal(web.STATE["sessions"].load(session.id))
    compaction.assert_paired(messages)
    assert (Path(store.load().workspace) / "one").exists()
    assert not (Path(store.load().workspace) / "two").exists()
    assert [e for e in events if e["type"] == "tool_result"][-1]["tool"]["error"] == "cancelled"


def test_child_stop_wakes_question_and_prevents_later_write(runtime, monkeypatch):
    handler, session, store = runtime
    model(monkeypatch, [stream(calls=[("subagent", {"task": "ask"})]),
                        stream(calls=[("ask_user", {"questions": [{"question": "continue?"}]}),
                                      ("write_file", {"path": "should-not-exist", "content": "x"})])])
    def emit(event):
        if event["type"] == "question":
            web.RUNNING["agent"].stop()
            web.APPROVALS.cancel_all()
    handler._run_turn(session, store.load(), emit)
    assert not (Path(store.load().workspace) / "should-not-exist").exists()
    assert not web.APPROVALS._pending


def test_images_survive_session_reload(runtime):
    _, session, _ = runtime
    images = [{"mime": "image/png", "data": "AA=="}]
    web.STATE["sessions"].append(session, web.Message(kind="user", text="", data={"images": images}))
    loaded = web.STATE["sessions"].load(session.id)
    assert web._transcript(loaded)[-1]["images"] == images
    assert web._internal(loaded)[-1]["content"] == [{"type": "image", **images[0]}]


def test_disconnect_before_tool_execution_keeps_replay_paired(runtime, monkeypatch):
    handler, session, store = runtime
    model(monkeypatch, [stream(calls=[("write_file", {"path": "never", "content": "x"})])])
    def emit(event):
        if event["type"] == "tool_start":
            raise BrokenPipeError("browser disconnected")
    handler._run_turn(session, store.load(), emit)
    messages = web._internal(web.STATE["sessions"].load(session.id))
    compaction.assert_paired(messages)
    assert not (Path(store.load().workspace) / "never").exists()
    saved = web._transcript(web.STATE["sessions"].load(session.id))
    assert saved[-1]["error"] == "interrupted"


def test_project_skills_follow_actual_workspace(runtime, monkeypatch):
    handler, session, store = runtime
    workspace = Path(store.load().workspace)
    for directory, name in [(workspace, "first"), (workspace / "other", "second")]:
        target = directory / ".nexus" / "skills" / name / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text(f"---\nname: {name}\ndescription: test skill\n---\n{name}", encoding="utf-8")
    seen = model(monkeypatch, [stream(text="one"), stream(text="two")])
    handler._run_turn(session, store.load(), lambda e: None)
    store.save_preferences(workspace=str(workspace / "other"))
    handler._run_turn(session, store.load(), lambda e: None)
    assert "first" in seen[0]["messages"][0]["content"]
    assert "second" in seen[1]["messages"][0]["content"]
    assert "first" not in web.STATE["skills"].skills


def test_image_only_chat_over_http_and_next_turn(runtime, monkeypatch):
    _, session, store = runtime
    seen = model(monkeypatch, [stream(text="image received"), stream(text="next turn")])
    image = {"mime": "image/png", "data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPe4AAAAASUVORK5CYII="}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{httpd.server_port}") as client:
            first = client.post("/api/chat", json={"session_id": session.id, "images": [image]})
            assert first.status_code == 200
            assert "image received" in first.text
            reloaded = client.get(f"/api/sessions/{session.id}").json()["items"]
            assert reloaded[-2]["images"] == [image]
            second = client.post("/api/chat", json={"session_id": session.id, "text": "continue"})
            assert second.status_code == 200
            assert "next turn" in second.text
        images = seen[0]["messages"][-1]["content"]
        assert images[0]["image_url"]["url"] == "data:image/png;base64," + image["data"]
        assert any(isinstance(m["content"], list) for m in seen[1]["messages"])
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_partial_model_error_is_saved(runtime, monkeypatch):
    handler, session, store = runtime
    malformed = 'data: {"choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
    model(monkeypatch, [malformed])
    handler._run_turn(session, store.load(), lambda e: None)
    assert web.STATE["sessions"].load(session.id).items[-1].text == "partial"


def test_fresh_unconfigured_home_can_load_settings(tmp_path, monkeypatch):
    store = web.ConfigStore(tmp_path)
    settings = store.ensure()
    monkeypatch.setattr(web, "STATE", {"store": store, "sessions": web.SessionStore(store.sessions_dir)})
    state = object.__new__(web.Handler)._state()
    assert state["profile"] is None
    assert state["providers"][0]["id"] == settings.provider.id
    assert state["has_key"] is False


def test_server_shutdown_closes_session_background_tasks(tmp_path, monkeypatch):
    from nexus.tools._jobs import JobRegistry
    jobs = JobRegistry()
    started = []
    class Server:
        server_address = ("127.0.0.1", 0)
        def __init__(self, *args):
            pass
        def serve_forever(self):
            web.STATE["jobs"]["session"] = jobs
            started.append(jobs.start([sys.executable, "-c", "import time; time.sleep(30)"], cwd=str(tmp_path)))
            raise KeyboardInterrupt
        def server_close(self):
            pass
    monkeypatch.setattr(web, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(web, "STATE", {})
    cancellation = threading.Event()
    monkeypatch.setattr(web, "RUNNING", {"agent": None, "cancel_event": cancellation})
    web.serve(home=tmp_path / "home", port=0, workspace=tmp_path)
    assert started[0].wait_closed(3)
    assert not started[0].running
    assert cancellation.is_set()


def test_shutdown_refuses_late_run_resource_registration(runtime):
    handler, session, store = runtime
    web.STATE["closing"] = True
    events = []
    handler._run_turn(session, store.load(), events.append)
    assert events[0]["type"] == "error"
    assert "jobs" not in web.STATE
    assert "cancel_event" not in web.RUNNING


def test_stop_at_tool_start_prevents_the_write(runtime, monkeypatch):
    handler, session, store = runtime
    model(monkeypatch, [stream(calls=[("write_file", {"path": "cancelled", "content": "x"})])])
    def emit(event):
        if event["type"] == "tool_start":
            web.RUNNING["agent"].stop()
    handler._run_turn(session, store.load(), emit)
    assert not (Path(store.load().workspace) / "cancelled").exists()
    assert web._transcript(web.STATE["sessions"].load(session.id))[-1]["error"] == "cancelled"
