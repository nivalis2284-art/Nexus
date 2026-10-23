"""The editor manages actual skill files in the selected source."""

from test_task_http import request
from test_task_http import task_live as task_live

from nexus.web import server as web


def test_skill_editor_crud_and_project_selection(task_live, tmp_path):
    base, _, _ = task_live
    status, listing = request(base, "/api/skills")
    assert status == 200
    status, listing = request(base, "/api/skills", "POST", {
        "source": "personal", "name": "test-editor", "description": "brief", "body": "details",
    })
    assert status == 200, listing
    item = next(s for s in listing["skills"] if s["name"] == "test-editor")
    path = web.STATE["store"].home / "skills" / "test-editor" / "SKILL.md"
    assert path.is_file()
    assert item["effective"] and not item["read_only"]
    status, listing = request(base, "/api/skills", "POST", {
        "source": "personal", "path": item["path"], "name": "test-renamed",
        "description": "new brief", "body": "new details",
    })
    assert status == 200, listing
    assert not path.exists()
    renamed = next(s for s in listing["skills"] if s["name"] == "test-renamed")
    assert renamed["body"] == "new details"
    status, listing = request(base, "/api/skills/delete", "POST", {
        "source": "personal", "path": renamed["path"],
    })
    assert status == 200, listing
    assert not any(s["name"] == "test-renamed" for s in listing["skills"])

    project = tmp_path / "second-project"
    project.mkdir()
    web.STATE["store"].save_preferences(workspace=str(project))
    status, listing = request(base, "/api/skills", "POST", {
        "source": "project", "name": "project-only", "description": "project", "body": "body",
    })
    assert status == 200, listing
    assert (project / ".nexus" / "skills" / "project-only" / "SKILL.md").is_file()


def test_builtin_skills_are_visible_and_read_only(task_live):
    base, _, _ = task_live
    status, listing = request(base, "/api/skills")
    assert status == 200
    builtin = next(s for s in listing["skills"] if s["source"] == "builtin")
    assert builtin["read_only"]
    status, error = request(base, "/api/skills/delete", "POST", {
        "source": "builtin", "path": builtin["path"],
    })
    assert status == 400, error
