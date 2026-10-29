"""The SDK stays installable and frontend assets are present in built wheels."""
import importlib.util
import json
import sys
import types
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "mobile-bridge/examples/sdk-example"


def test_sdk_archive_matches_installable_source():
    with zipfile.ZipFile(ROOT / "mobile-bridge/releases/plugins/nexus-plugin-sdk-0.2.0.zip") as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("plugin.json"))["id"] == "sdk-example"
        for name in ("plugin.json", "backend.py", "nexus_plugin_sdk.py", "README.md", "web/index.html"):
            assert archive.read(name) == (EXAMPLE / name).read_bytes()
        assert b"/css/plugin-ui.css" in archive.read("web/index.html")
        assert b"/js/plugin-ui.js" in archive.read("web/index.html")


def test_sdk_example_queries_host_without_writes(monkeypatch):
    package = types.ModuleType("ui_sdk_example")
    package.__path__ = [str(EXAMPLE)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    spec = importlib.util.spec_from_file_location(package.__name__ + ".backend", EXAMPLE / "backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Host:
        chat_api_version = 1

        def chat_snapshot(self):
            return {"session_id": "original-session", "running": False}

    assert module.create_plugin(Host()).call("snapshot", {}) == {
        "session_id": "original-session", "running": False}


def test_built_wheel_contains_every_frontend_asset():
    wheels = list((ROOT / ".artifacts/plugin-ui/dist").glob("nexus-*.whl"))
    if not wheels:
        import pytest
        pytest.skip("Build the wheel first; see SDK validation instructions")
    with zipfile.ZipFile(max(wheels, key=lambda path: path.stat().st_mtime_ns)) as archive:
        names = set(archive.namelist())
        for folder in ("css", "js", "assets", "vendor"):
            for path in (ROOT / "nexus/web" / folder).iterdir():
                if path.is_file():
                    assert path.relative_to(ROOT).as_posix() in names
        assert "nexus/web/js/plugin-ui.js" in names
        assert "nexus/web/css/plugin-ui.css" in names
