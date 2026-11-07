"""HTTP integration: Ling intake, persistence, and settings."""

from __future__ import annotations

import http.client
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from nexus.mcp import MCPRegistry
from nexus.model import APIFamily
from nexus.runtime import TaskController
from nexus.task import TaskStore
from nexus.session import SessionStore
from nexus.web import server as web


def post(url: str, payload: dict) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.status, response.read()


def get(url: str) -> tuple[int, bytes]:
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read()


def until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition did not become true")


@pytest.fixture()
def live(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello\n", encoding="utf-8")
    home = tmp_path / "home"
    store = web.ConfigStore(home)
    store.ensure()
    # Pin the protocol: the fake frames below are Chat Completions, and the
    # shipped default provider uses a different family.
    for provider in store.providers():
        store.update_provider(
            provider.id,
            {
                # The shipped default has no endpoint by design, so the fixture
                # supplies one; the transport is mocked either way.
                "base_url": "https://fake.test",
                "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
                "models": [{"id": "fake", "context_window_tokens": 32000,
                            "max_output_tokens": 1024}],
            },
        )
        store.select(provider.id, "fake", "off")
        store.write_credential(provider.id, "test-key")
    tasks = TaskStore(home / "tasks.sqlite3")
    controller = TaskController(tasks)
    monkeypatch.setattr(web, "STATE", {
        "store": store,
        "sessions": SessionStore(store.sessions_dir),
        "tasks": tasks, "task_controller": controller,
        "skills": {}, "jobs": {}, "closing": False,
        "mcp": MCPRegistry(),
    })

    frames = [
        (
            'data: {"id":"c1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
            '"id":"call_1","type":"function","function":{"name":"read_file",'
            '"arguments":"{\\"path\\": \\"a.txt\\"}"}}]},"finish_reason":null}]}\n\n'
            'data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            "data: [DONE]\n\n"
        ),
        (
            'data: {"id":"c1","choices":[{"index":0,"delta":{"content":"它写着 hello."},'
            '"finish_reason":null}]}\n\n'
            'data: {"id":"c1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            "data: [DONE]\n\n"
        ),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        text = frames.pop(0) if frames else frames[-1]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=text
        )

    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", patched)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, workspace, store, tasks, controller
    ling = web.STATE.get("ling")
    if ling:
        ling.close()
    controller.close()
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.parametrize("kind", ["provider", "last_provider", "session"])
def test_delete_consumes_body_before_next_request(live, kind):
    base, _, store, _, _ = live
    if kind == "provider":
        provider = store.add_provider({
            "name": "delete test", "base_url": "https://fake.test",
            "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
            "models": [{"id": "fake"}], "api_key": "delete-test-key",
        })
        path, expected = f"/api/providers/{provider.id}", 200
    elif kind == "last_provider":
        provider = store.providers()[0]
        path, expected = f"/api/providers/{provider.id}", 200
    else:
        session = web.STATE["sessions"].create("fake")
        path, expected = f"/api/sessions/{session.id}", 200

    connection = http.client.HTTPConnection(base.removeprefix("http://"), timeout=5)
    try:
        for index in range(2):
            connection.request("DELETE", path, body=b"{}", headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read()
            assert response.status == (404 if kind == "session" and index else expected), body
            json.loads(body)
        connection.request("GET", "/api/state")
        response = connection.getresponse()
        value = json.loads(response.read())
        assert response.status == 200, value
        if kind == "provider":
            assert provider.id not in {p["id"] for p in value["providers"]}
            assert not store.has_key(provider.id)
        elif kind == "last_provider":
            assert value["providers"] == []
            assert value["current"]["provider"] == value["current"]["model"] == ""
            assert value["profile"] is None
            assert not store.has_key(provider.id)
            connection.request("POST", "/api/providers", body=json.dumps({
                "name": "New", "base_url": "https://new.test",
                "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
                "models": [{"id": "new-model"}],
            }), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read()
            assert response.status == 201, body
            added = json.loads(body)
            assert added["current"]["provider"] == added["providers"][0]["id"]
            assert added["current"]["model"] == "new-model"
        else:
            assert web.STATE["sessions"].load(session.id) is None
    finally:
        connection.close()


def test_full_turn_over_http(live):
    base, workspace, _, _, _ = live
    status, body = post(base + "/api/workspace", {"workspace": str(workspace)})
    assert status == 200, body

    status, body = post(base + "/api/ling/input", {"text": "读一下 a.txt", "request_id": "full-turn"})
    assert status == 202, body
    accepted = json.loads(body)
    input_id, session_id = accepted["input_id"], accepted["session_id"]

    def finished():
        value = json.loads(get(base + "/api/ling")[1])
        input_status = next((item["status"] for item in value["inputs"] if item["id"] == input_id), None)
        return value if input_status == "done" else None

    projection = until(finished)
    assert any(item["role"] == "tool" for item in projection["items"])
    assert any(item.get("text", "").startswith("它写着") for item in projection["items"])

    # The transcript is durable, and the tool call survived the round trip.
    status, body = get(base + f"/api/sessions/{session_id}")
    saved = json.loads(body)["items"]
    roles = [item["role"] for item in saved]
    assert roles == ["user", "tool", "assistant"]
    assert "hello" in saved[1]["text"]


def test_input_requires_a_key(live):
    base, _workspace, store, _, _ = live
    for provider in store.providers():
        store.write_credential(provider.id, "")
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(base + "/api/ling/input", {"text": "hi", "request_id": "no-key"})
    assert caught.value.code == 400


def test_retired_chat_and_session_creation_routes_are_absent(live):
    base, _, _, _, _ = live
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(base + "/api/chat", {"text": "hi"})
    assert caught.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(base + "/api/sessions", {})
    assert caught.value.code == 404
