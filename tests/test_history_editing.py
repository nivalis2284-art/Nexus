import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from nexus.application import Application
from nexus.runtime.conversation import history_for_model
from nexus.runtime.history import edit_history, effective_checkpoint, history_version
from nexus.session import Message, SessionStore
from nexus.task import TaskConflict
from nexus.web import server as web


def test_delete_keeps_tool_exchanges_complete_and_checkpoint_sources_valid(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create("test")
    for item in [Message("user", "old"), Message("assistant", "old reply"),
                 Message("user", "recent"),
                 Message("checkpoint", "summary", {"through": "1"}),
                 Message("assistant", "using tools", {"tool_calls": [{"id": "a"}, {"id": "b"}]}),
                 Message("tool", "a result", {"tool_call_id": "a"}),
                 Message("tool", "b result", {"tool_call_id": "b"}),
                 Message("assistant", "answer")]:
        store.append(session, item)
    edit_history(session, {"operation": "delete", "indices": [2, 5], "version": history_version(session)})
    store.rewrite(session)
    restored = store.load(session.id)
    assert [item.text for item in restored.items] == ["old", "old reply", "summary", "answer"]
    assert restored.items[2].data["through"] == "1"
    assert [item["content"] for item in history_for_model(restored)] == ["summary", "answer"]
    assert effective_checkpoint(restored)[0] == 2


def test_resend_keeps_checkpoint_created_after_its_selected_message(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create("test")
    for item in [Message("user", "old"), Message("assistant", "old reply"),
                 Message("user", "recent"), Message("checkpoint", "summary", {"through": "1"}),
                 Message("assistant", "recent reply")]:
        store.append(session, item)
    resend = edit_history(session, {"operation": "resend", "indices": [2], "text": "edited",
                                    "request_id": "new", "version": history_version(session)})
    assert resend["text"] == "edited"
    assert [m["content"] for m in history_for_model(session)] == ["summary"]


@pytest.fixture
def app(tmp_path):
    instance = Application().start(home=tmp_path / "home", workspace=tmp_path)
    yield instance
    instance.close()


def add_history(app):
    store = app.state["sessions"]
    session = store.load(app.inbox().session_id)
    store.append(session, Message("user", "question"))
    store.append(session, Message("assistant", "answer"))
    return session


def test_history_rejects_running_and_stale_edits_and_undo_restores(app):
    session = add_history(app)
    body = {"operation": "delete", "indices": [1], "version": history_version(session)}
    with app.busy:
        with pytest.raises(TaskConflict, match="正在处理"):
            app.change_history(body)
    app.change_history(body)
    assert [m.text for m in app.state["sessions"].load(session.id).items] == ["question"]
    with pytest.raises(TaskConflict, match="已更新"):
        app.change_history(body)
    version = history_version(app.state["sessions"].load(session.id))
    app.change_history({"operation": "undo", "version": version})
    assert [m.text for m in app.state["sessions"].load(session.id).items] == ["question", "answer"]


def test_resend_validation_failure_leaves_history_untouched(app):
    session = add_history(app)
    original = session.path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="配置模型"):
        app.change_history({"operation": "resend", "indices": [0], "text": "edited",
                            "request_id": "resend", "version": history_version(session)})
    assert session.path.read_text(encoding="utf-8") == original


def test_summary_changes_model_context_and_new_history_invalidates_undo(app):
    session = add_history(app)
    app.state["sessions"].append(session, Message("checkpoint", "old summary", {"through": "0"}))
    app.change_history({"operation": "summary", "index": 2, "text": "owner edited summary",
                        "version": history_version(session)})
    current = app.state["sessions"].load(session.id)
    assert "owner edited summary" in history_for_model(current)[0]["content"]
    app.state["sessions"].append(current, Message("user", "later conversation"))
    with pytest.raises(TaskConflict, match="会话已继续"):
        app.change_history({"operation": "undo", "version": history_version(current)})


def test_resend_preserves_attachments_and_undo_after_reply(app, monkeypatch):
    session = add_history(app)
    task = app.state["tasks"].create(title="kept", goal="kept", workspace=str(app.state["store"].home))
    session.items[0].data = {"images": [{"mime": "image/png", "data": "eA=="}],
                             "attachments": [{"id": "kept-file"}], "task_id": task.id}
    app.state["sessions"].rewrite(session)
    seen = []
    monkeypatch.setattr(app, "_prepare_input", lambda body: seen.append(body) or [])
    def run(current, emit, inbox):
        app.state["sessions"].append(current, Message("assistant", "new answer"))
    app.inbox().run = run
    app.change_history({"operation": "resend", "indices": [0], "text": "edited",
                        "request_id": "resend", "version": history_version(session)})
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        current = app.state["sessions"].load(session.id)
        if current.items and current.items[-1].text == "new answer" and not app.inbox().running:
            break
        time.sleep(.02)
    assert [m.text for m in current.items] == ["edited", "new answer"]
    assert seen[0]["images"] == [{"mime": "image/png", "data": "eA=="}]
    assert seen[0]["attachments"] == ["kept-file"]
    assert seen[0]["task_id"] == task.id
    app.change_history({"operation": "undo", "version": history_version(current)})
    assert [m.text for m in app.state["sessions"].load(session.id).items] == ["question", "answer"]


def test_context_and_history_http_routes(app, monkeypatch):
    session = add_history(app)
    monkeypatch.setattr(web, "STATE", app.state)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            context = client.get("/api/context").raise_for_status().json()
            assert len(context["roles"]) == 3
            assert client.put("/api/context/owners", json={"items": ["first", "second"]}).status_code == 200
            assert client.put("/api/context/roles", json={"id": "main", "text": "custom role"}).status_code == 200
            result = client.post("/api/ling/history", json={"operation": "delete", "indices": [1], "version": context["version"]})
            assert result.status_code == 200, result.text
            assert result.json()["context"]["messages"][0]["text"] == "question"
            for asset in ("context.js", "context.css", "editing.js"):
                assert client.get("/" + asset).status_code == 200
    finally:
        server.shutdown()
        server.server_close()
