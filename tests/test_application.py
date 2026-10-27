"""The same application runs without an HTTP handler or browser."""

import json
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest
from test_task_runtime import configure, text_frames

from nexus.application import Application
from nexus.core import agent as loop
from nexus.runtime.conversation import history_for_model
from nexus.session import Message
from nexus.web import server as web


def test_application_input_result_settings_and_shutdown(tmp_path, monkeypatch):
    config, _ = configure(tmp_path)
    seen = []

    def reply(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=text_frames("application result"))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(loop, "_client", lambda: real_client(transport=httpx.MockTransport(reply)))
    app = Application().start(home=config.home)
    try:
        inbox = app.inbox()
        app.submit_input({"text": "complete this input", "request_id": "direct-input"})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            saved = app.state["sessions"].load(inbox.session_id)
            if any(item.text == "application result" for item in saved.items):
                break
            time.sleep(.02)
        else:
            pytest.fail("application did not produce its result")
        assert history_for_model(saved)[-1]["content"] == "application result"
        assert len(seen) == 1
        app.save_settings({"permission": "ask"})
        assert config.load().permission == "ask"
        subscriber = app.state["runtime_updates"].subscribe()
        jobs = app.state["jobs"][inbox.session_id]
    finally:
        app.close()
    assert jobs.closed
    assert not inbox._thread.is_alive()
    assert not any(worker.is_alive() for worker in app.state["task_controller"]._workers)
    while subscriber.get(timeout=1) is not None:
        pass
    with pytest.raises(ValueError, match="关闭"):
        app.submit_input({"text": "late input", "request_id": "late"})


def test_http_binding_failure_closes_created_application(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "STATE", {})
    instances = []

    def bind(*args):
        instances.append(web.STATE["application"])
        raise OSError("address in use")

    monkeypatch.setattr(web, "ThreadingHTTPServer", bind)
    with pytest.raises(OSError, match="address in use"):
        web.serve(home=tmp_path / "home", workspace=tmp_path)
    app = instances[0]
    assert app.state["closing"]
    assert not app.state["ling"]._thread.is_alive()
    assert not any(worker.is_alive() for worker in app.state["task_controller"]._workers)


def test_http_start_attaches_actual_port_before_serving(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "STATE", {})

    class Server:
        server_address = ("127.0.0.1", 18766)

        def __init__(self, *args):
            self.closed = False

        def serve_forever(self):
            assert web.STATE["plugins"].base_url == "http://127.0.0.1:18766"
            raise KeyboardInterrupt

        def server_close(self):
            self.closed = True

    monkeypatch.setattr(web, "ThreadingHTTPServer", Server)
    web.serve(home=tmp_path / "home", workspace=tmp_path)
    assert web.STATE["plugins"].closing


def test_chat_snapshot_is_shared_with_http_and_detached(tmp_path, monkeypatch):
    app = Application().start(home=tmp_path / "home", workspace=tmp_path)
    monkeypatch.setattr(web, "STATE", app.state)
    feed = app.state["runtime_updates"]
    session_id = app.inbox().session_id
    feed.publish("ling", session_id, {"type": "context", "data": {"tokens": 123}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        direct = app.chat_snapshot()
        with httpx.Client(trust_env=False) as client:
            remote = client.get(f"http://127.0.0.1:{server.server_port}/api/ling")
        assert remote.status_code == 200
        assert remote.json() == direct
        direct["live"]["context"]["tokens"] = 0
        assert app.chat_snapshot()["live"]["context"]["tokens"] == 123
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        app.close()


def test_completion_during_snapshot_is_not_acknowledged_before_history_read(tmp_path, monkeypatch):
    app = Application().start(home=tmp_path / "home", workspace=tmp_path)
    try:
        sessions = app.state["sessions"]
        original_load = sessions.load
        feed = app.state["runtime_updates"]
        updates = feed.subscribe()
        armed = True

        def load_then_complete(owner_id):
            nonlocal armed
            loaded = original_load(owner_id)
            if armed:
                armed = False
                writer = original_load(owner_id)
                sessions.append(writer, Message("assistant", "completed while reading",
                                                 {"message_id": "racing-message"}))
                feed.publish("ling", owner_id, {"type": "done"})
            return loaded

        monkeypatch.setattr(sessions, "load", load_then_complete)
        snapshot = app.chat_snapshot()
        assert not any(item.get("message_id") == "racing-message" for item in snapshot["items"])
        completion = updates.get(timeout=1)
        assert completion["revision"] > snapshot["live_revision"]
        recovered = app.chat_snapshot()
        assert any(item.get("text") == "completed while reading" for item in recovered["items"])
        feed.unsubscribe(updates)
    finally:
        app.close()
