"""HTTP integration test: the real server, a stubbed model transport.

This drives the actual handler (chunked NDJSON, persistence, settings) rather
than the agent in isolation. Only the outbound model call is faked.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

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


@pytest.fixture()
def live(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello\n", encoding="utf-8")
    home = tmp_path / "home"
    store = web.ConfigStore(home)
    store.ensure()
    store.write_credential("default", "test-key")
    web.STATE["store"] = store
    web.STATE["sessions"] = web.SessionStore(store.sessions_dir)
    web.STATE["skills"] = {}

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
    yield base, workspace, store
    httpd.shutdown()
    httpd.server_close()


def test_full_turn_over_http(live):
    base, workspace, _ = live
    status, body = post(base + "/api/workspace", {"workspace": str(workspace)})
    assert status == 200, body

    status, body = post(base + "/api/sessions", {})
    session_id = json.loads(body)["id"]

    status, raw = post(base + "/api/chat", {"session_id": session_id, "text": "读一下 a.txt"})
    assert status == 200
    lines = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    kinds = [item["type"] for item in lines]
    assert "tool_start" in kinds
    assert "tool_result" in kinds
    assert "done" in kinds
    assert any(item.get("text", "").startswith("它写着") for item in lines)

    # The transcript is durable, and the tool call survived the round trip.
    status, body = get(base + f"/api/sessions/{session_id}")
    saved = json.loads(body)["items"]
    roles = [item["role"] for item in saved]
    assert roles == ["user", "tool", "assistant"]
    assert "hello" in saved[1]["text"]


def test_chat_requires_a_key(live):
    base, workspace, store = live
    store.write_credential("default", "")
    status, body = post(base + "/api/sessions", {})
    session_id = json.loads(body)["id"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(base + "/api/chat", {"session_id": session_id, "text": "hi"})
    assert caught.value.code == 400


def test_unknown_session_is_rejected(live):
    base, _, _ = live
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(base + "/api/chat", {"session_id": "nope", "text": "hi"})
    assert caught.value.code == 400
