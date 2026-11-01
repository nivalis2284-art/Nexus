"""Named workspace history; deleting a record never touches project files."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "Work"


def project_record(path, name=None):
    path = Path(path).expanduser().resolve()
    return {"path": str(path), "name": name or path.name or str(path)}


def initialize(store, workspace=None):
    raw = store.read_config()
    records = raw.get("projects", [])
    current = Path(workspace or store.load().workspace).expanduser().resolve()
    # The old launcher persisted the installation root on every start.
    if workspace is None and "projects" not in raw and current == PROJECT_ROOT:
        current = DEFAULT_WORKSPACE
    if current == DEFAULT_WORKSPACE:
        current.mkdir(parents=True, exist_ok=True)
    elif not any(Path(row["path"]) == current for row in records):
        records.append(project_record(current))
    store.save_preferences(workspace=str(current), projects=records)


def snapshot(store):
    records = store.read_config().get("projects", [])
    current = project_record(store.load().workspace)
    current = next((row for row in records if row["path"] == current["path"]), current)
    return {"projects": records, "project": current,
            "default_project": project_record(DEFAULT_WORKSPACE)}


def command(app, value):
    action = value.get("action")
    raw_path = value.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("请选择项目文件夹。")
    path = Path(raw_path).expanduser().resolve()
    store = app.state["store"]
    records = list(store.read_config().get("projects", []))
    found = next((row for row in records if Path(row["path"]) == path), None)
    settings = {}
    if action in ("save", "rename"):
        name = value.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("请输入项目名称。")
        if action == "rename" and found is None:
            raise ValueError("项目记录不存在，请重新打开项目列表。")
        if action == "save":
            settings["workspace"] = str(path)
        records = [row for row in records if Path(row["path"]) != path]
        records.insert(0, project_record(path, name.strip()))
    elif action == "select":
        if found is None and path != DEFAULT_WORKSPACE:
            raise ValueError("项目记录不存在，请重新打开项目列表。")
        if path == DEFAULT_WORKSPACE:
            path.mkdir(parents=True, exist_ok=True)
        settings["workspace"] = str(path)
    elif action == "delete":
        if found is None:
            raise ValueError("项目记录不存在，请重新打开项目列表。")
        records.remove(found)
        if path == Path(store.load().workspace).resolve():
            DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
            settings["workspace"] = str(DEFAULT_WORKSPACE)
    else:
        raise ValueError("未知项目操作。")
    app.save_settings(settings, projects=records)

