"""Browser transport and local references, using the existing HTTP fixture."""
import json

import httpx

from test_http import live as live
from nexus.web import server as web


def test_stream_delivers_runtime_events_without_polling(live):
    from nexus.runtime.stream import runtime_updates

    base, _, _, _, _ = live
    with httpx.stream("GET", base + "/api/stream", timeout=3) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        event = {"type": "thinking", "text": "first fragment"}
        runtime_updates(web.STATE).publish("ling", "session", event)
        delivered = next(line[6:] for line in response.iter_lines() if line.startswith("data: "))
        assert json.loads(delivered) == {
            "scope": "ling", "owner_id": "session", "revision": 1, **event,
            "live": {"thinking": "first fragment", "revision": 1},
        }


def test_workspace_reference_reads_file_and_preserves_scope(live, tmp_path):
    base, workspace, store, _, _ = live
    store.save_preferences(workspace=str(workspace))
    result = httpx.get(base + "/api/workspace-file", params={"path": "a.txt", "workspace": str(workspace)})
    assert result.status_code == 200
    assert result.json()["text"] == "1: hello"
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    assert httpx.get(base + "/api/workspace-file", params={"path": str(outside)}).status_code == 403


def test_runtime_includes_browser_assets(live):
    base, _, _, _, _ = live
    for path in ("/presentation.js", "/presentation.css", "/live.js", "/vendor/marked.js", "/vendor/purify.js", "/vendor/highlight.js", "/vendor/highlight.css"):
        response = httpx.get(base + path)
        assert response.status_code == 200, path
        assert response.content
