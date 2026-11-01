"""Project records change workspace without moving or deleting project data."""

import json
from types import SimpleNamespace

import pytest

from nexus import folder_picker, projects
from nexus.application import Application
from nexus.core import config
from nexus.core.config import ConfigStore
from nexus.task import TaskConflict, TaskStore


@pytest.fixture()
def app(tmp_path, monkeypatch):
    root = tmp_path / "Nexus"
    root.mkdir()
    monkeypatch.setattr(projects, "PROJECT_ROOT", root)
    monkeypatch.setattr(projects, "DEFAULT_WORKSPACE", root / "Work")
    monkeypatch.setattr(config, "DEFAULT_WORKSPACE", root / "Work")
    store = ConfigStore(tmp_path / "home")
    store.ensure()
    projects.initialize(store)
    return Application({"store": store})


def test_default_and_selected_project_survive_restart(app, tmp_path):
    store = app.state["store"]
    default = projects.DEFAULT_WORKSPACE
    assert default.is_dir()
    assert store.load().workspace == str(default)
    target = tmp_path / "中文项目"
    target.mkdir()
    app.project_command({"action": "save", "path": str(target), "name": "我的项目"})
    projects.initialize(ConfigStore(store.home))
    assert projects.snapshot(store)["project"] == {"name": "我的项目", "path": str(target)}
    assert store.load().workspace == str(target)
    app.project_command({"action": "save", "path": str(target / "."), "name": "新名称"})
    assert projects.snapshot(store)["projects"] == [{"name": "新名称", "path": str(target)}]


def test_application_restart_loads_last_selection(app, tmp_path):
    target = tmp_path / "project"
    target.mkdir()
    app.project_command({"action": "save", "path": str(target), "name": "重启保留"})
    restarted = Application().start(home=app.state["store"].home)
    try:
        assert restarted.state["store"].load().workspace == str(target)
        assert projects.snapshot(restarted.state["store"])["project"]["name"] == "重启保留"
    finally:
        restarted.close()


def test_old_launcher_root_becomes_work_but_explicit_root_is_a_project(app):
    store = app.state["store"]
    raw = store.read_config()
    raw.pop("projects")
    raw["workspace"] = str(projects.PROJECT_ROOT)
    store.write_config(raw)
    projects.initialize(store)
    assert store.load().workspace == str(projects.DEFAULT_WORKSPACE)
    projects.initialize(store, projects.PROJECT_ROOT)
    assert store.load().workspace == str(projects.PROJECT_ROOT)
    assert projects.snapshot(store)["projects"][0]["path"] == str(projects.PROJECT_ROOT)


def test_rename_and_delete_preserve_files_tasks_and_other_projects(app, tmp_path):
    store = app.state["store"]
    target = tmp_path / "project"
    target.mkdir()
    source = target / "keep.txt"
    source.write_text("keep", encoding="utf-8")
    app.project_command({"action": "save", "path": str(target), "name": "项目"})
    tasks = TaskStore(store.home / "tasks.sqlite3")
    task = tasks.create(title="existing", goal="keep", workspace=str(target))
    app.project_command({"action": "rename", "path": str(target), "name": "显示名称"})
    assert target.is_dir()
    assert projects.snapshot(store)["project"]["name"] == "显示名称"
    other = tmp_path / "other"
    other.mkdir()
    app.project_command({"action": "save", "path": str(other), "name": "另一个项目"})
    app.project_command({"action": "delete", "path": str(target)})
    assert store.load().workspace == str(other)
    app.project_command({"action": "delete", "path": str(other)})
    assert store.load().workspace == str(projects.DEFAULT_WORKSPACE)
    assert projects.snapshot(store)["projects"] == []
    assert source.read_text(encoding="utf-8") == "keep"
    assert tasks.get(task.id).workspace == str(target)
    assert other.is_dir()


def test_busy_switch_and_invalid_folder_do_not_save_history(app, tmp_path):
    store = app.state["store"]
    target = tmp_path / "project"
    target.mkdir()
    app.project_command({"action": "save", "path": str(target), "name": "项目"})
    before = store.read_config()
    app.busy.acquire()
    try:
        with pytest.raises(TaskConflict):
            app.project_command({"action": "delete", "path": str(target)})
        assert store.read_config() == before
        app.project_command({"action": "rename", "path": str(target), "name": "运行时改名"})
        assert store.load().workspace == str(target)
    finally:
        app.busy.release()
    before = store.read_config()
    with pytest.raises(ValueError, match="not a directory"):
        app.project_command({"action": "save", "path": str(tmp_path / "missing"), "name": "无效"})
    assert store.read_config() == before


def test_failed_workspace_effect_rolls_back_project_records(app, tmp_path, monkeypatch):
    target = tmp_path / "project"
    target.mkdir()
    before = app.state["store"].read_config()

    def fail(_):
        raise RuntimeError("effect failed")

    monkeypatch.setattr(app, "workspace_changed", fail)
    with pytest.raises(RuntimeError, match="effect failed"):
        app.project_command({"action": "save", "path": str(target), "name": "项目"})
    assert app.state["store"].read_config() == before


@pytest.mark.parametrize("path", [None, "H:/文档/中文项目"])
def test_system_picker_returns_selected_path_or_cancel(monkeypatch, path):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"path": path}), stderr="")

    monkeypatch.setattr(folder_picker.subprocess, "run", run)
    assert folder_picker.choose("H:/文档") == {"path": path}
    assert calls[0][0][-1] == "H:/文档"
    assert calls[0][1]["encoding"] == "utf-8"
    assert calls[0][1]["env"]["PYTHONIOENCODING"] == "utf-8"


def test_system_picker_failure_is_reported(monkeypatch):
    monkeypatch.setattr(folder_picker.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stdout="", stderr="Tk unavailable"))
    with pytest.raises(OSError, match="Tk unavailable"):
        folder_picker.choose(".")
