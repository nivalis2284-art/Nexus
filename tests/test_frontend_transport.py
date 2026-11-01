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
    for path in ("/vendor/marked.js", "/vendor/purify.js", "/vendor/highlight.js", "/vendor/highlight.css"):
        response = httpx.get(base + path)
        assert response.status_code == 200, path
        assert response.content


def test_project_routes_save_rename_select_delete_and_choose_folder(live, tmp_path, monkeypatch):
    from nexus import folder_picker, projects

    base, workspace, store, _, _ = live
    monkeypatch.setattr(projects, "DEFAULT_WORKSPACE", tmp_path / "Work")
    monkeypatch.setattr(folder_picker, "choose", lambda initial: {"path": str(workspace)})
    picked = httpx.post(base + "/api/projects/choose-folder", json={})
    assert picked.status_code == 200
    assert picked.json() == {"path": str(workspace)}
    for action, name in [("save", "项目"), ("rename", "显示名称")]:
        response = httpx.post(base + "/api/projects", json={
            "action": action, "path": str(workspace), "name": name,
        })
        assert response.status_code == 200, response.text
        assert response.json()["project"] == {"path": str(workspace), "name": name}
    response = httpx.post(base + "/api/projects", json={
        "action": "select", "path": str(projects.DEFAULT_WORKSPACE),
    })
    assert response.status_code == 200, response.text
    assert response.json()["workspace"] == str(projects.DEFAULT_WORKSPACE)
    response = httpx.post(base + "/api/projects", json={"action": "delete", "path": str(workspace)})
    assert response.status_code == 200
    assert response.json()["projects"] == []
    assert (workspace / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert store.load().workspace == str(projects.DEFAULT_WORKSPACE)
