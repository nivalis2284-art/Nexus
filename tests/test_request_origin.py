"""The local API refuses requests that a hostile page can drive.

Loopback binding is not authorization: any page the user visits can reach
127.0.0.1, and DNS rebinding can aim a hostile name at it. These drive a real
``ThreadingHTTPServer`` over a real socket, so the checks are exercised exactly
where they run in production (``Handler.parse_request``) rather than by
calling helpers in isolation.
"""

from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from nexus.web import server as web


@pytest.fixture()
def live(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = web.ConfigStore(tmp_path / "home")
    store.ensure()
    store.save_preferences(workspace=str(workspace), permission="ask")
    monkeypatch.setattr(
        web,
        "STATE",
        {
            "store": store,
            "sessions": web.SessionStore(store.sessions_dir),
            "skills": {},
            "closing": False,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    try:
        yield port, store
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def call(port: int, method: str, path: str, *, body: bytes | None = None,
         headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    """One request over a real socket, with every header under our control."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def entity(port: int, headers: dict[str, str] | None = None) -> dict[str, str]:
    base = {"Host": f"127.0.0.1:{port}"}
    base.update(headers or {})
    return base


# --- the allowed baseline ------------------------------------------------


def test_loopback_json_post_is_accepted(live):
    port, store = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers=entity(port, {"Content-Type": "application/json"}),
    )
    assert status == 200, raw
    assert store.load().permission == "yolo"


def test_local_origin_is_accepted(live):
    port, _ = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers=entity(port, {
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
        }),
    )
    assert status == 200, raw


def test_localhost_origin_is_accepted(live):
    port, _ = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers=entity(port, {
            "Content-Type": "application/json",
            "Origin": f"http://localhost:{port}",
        }),
    )
    assert status == 200, raw


def test_missing_origin_is_accepted(live):
    """curl and the existing tests send no Origin; that must keep working."""
    port, store = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers=entity(port, {"Content-Type": "application/json"}),
    )
    assert status == 200, raw
    assert store.load().permission == "yolo"


@pytest.mark.parametrize("host", ["localhost", "[::1]"])
def test_loopback_spellings_are_accepted(live, host):
    port, _ = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers=entity(port, {
            "Host": f"{host}:{port}",
            "Content-Type": "application/json",
        }),
    )
    assert status == 200, raw


def test_json_with_charset_parameter_is_accepted(live):
    port, _ = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers=entity(port, {"Content-Type": "application/json; charset=utf-8"}),
    )
    assert status == 200, raw


# --- Host -----------------------------------------------------------------


@pytest.mark.parametrize("host", [
    "evil.example",
    "evil.example:80",
    "127.0.0.1.evil.example",
    "127.0.0.1:1@evil.example",
    "127.0.0.1, evil.example",
])
def test_forged_host_is_rejected(live, host):
    port, store = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers={"Host": host, "Content-Type": "application/json"},
    )
    assert status == 403, raw
    # No route ran, so the write never happened.
    assert store.load().permission == "ask"


def test_wrong_port_in_host_is_rejected(live):
    port, _ = live
    status, _ = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers={"Host": f"127.0.0.1:{port + 1}", "Content-Type": "application/json"},
    )
    assert status == 403


def test_host_is_checked_on_get_too(live):
    port, _ = live
    status, _ = call(port, "GET", "/api/state", headers={"Host": "evil.example"})
    assert status == 403
    status, raw = call(port, "GET", "/api/state", headers=entity(port))
    assert status == 200, raw


# --- Origin ---------------------------------------------------------------


@pytest.mark.parametrize("origin", [
    "https://evil.example",
    "http://evil.example",
    "null",
    "http://127.0.0.1:9999",
])
def test_foreign_origin_is_rejected(live, origin):
    port, store = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers=entity(port, {"Content-Type": "application/json", "Origin": origin}),
    )
    assert status == 403, raw
    assert store.load().permission == "ask"


def test_origin_is_checked_on_get_too(live):
    port, _ = live
    status, _ = call(
        port, "GET", "/api/state",
        headers=entity(port, {"Origin": "https://evil.example"}),
    )
    assert status == 403


# --- Content-Type ---------------------------------------------------------


@pytest.mark.parametrize("content_type", [
    "text/plain",
    "text/plain;charset=UTF-8",
    "application/x-www-form-urlencoded",
    "multipart/form-data; boundary=x",
    "",
])
def test_non_json_write_is_rejected(live, content_type):
    port, store = live
    status, raw = call(
        port, "POST", "/api/settings",
        body=json.dumps({"permission": "yolo"}).encode(),
        headers=entity(port, {"Content-Type": content_type}),
    )
    assert status == 415, raw
    assert store.load().permission == "ask"


def test_put_without_json_type_is_rejected(live):
    port, _ = live
    status, _ = call(
        port, "PUT", "/api/tasks/whatever",
        body=b"{}", headers=entity(port, {"Content-Type": "text/plain"}),
    )
    assert status == 415


# --- uploads keep working -------------------------------------------------


def test_attachment_upload_still_accepts_raw_bytes(live):
    """Uploads stream the browser's own Content-Type; JSON must not apply."""
    port, _ = live
    session = web.STATE["sessions"].create("fake")
    data = b"attachment body over http\n"
    status, raw = call(
        port, "POST", f"/api/sessions/{session.id}/attachments?name=notes.md",
        body=data,
        headers=entity(port, {"Content-Type": "text/markdown"}),
    )
    assert status == 201, raw
    item = json.loads(raw)
    assert item["status"] == "ready"

    status, fetched = call(port, "GET", item["url"], headers=entity(port))
    assert status == 200
    assert fetched == data


def test_attachment_upload_is_still_host_checked(live):
    port, _ = live
    session = web.STATE["sessions"].create("fake")
    status, _ = call(
        port, "POST", f"/api/sessions/{session.id}/attachments?name=notes.md",
        body=b"data", headers={"Host": "evil.example", "Content-Type": "text/markdown"},
    )
    assert status == 403
