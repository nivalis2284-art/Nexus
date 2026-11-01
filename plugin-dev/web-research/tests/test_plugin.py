import os
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

from nexus.mcp import MCPRegistry
from nexus.plugins.manager import PluginManager
from nexus.tools import build


def test_provider_setting_changes_the_next_search():
    spec = importlib.util.spec_from_file_location("web_research_backend", Path(__file__).parents[1] / "backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    host = SimpleNamespace(settings={}, call_service=lambda *args, **kwargs: calls.append(kwargs) or {"content": "ok"})
    search = module.Plugin(host).tools({})[0]
    search.handler(None, {"query": "example", "limit": 1})
    host.settings = {"provider": "Tavily", "tavily_api_key": "secret-for-test"}
    search.handler(None, {"query": "example", "limit": 1})
    assert calls[0]["provider"] == "DDGS"
    assert calls[1]["provider"] == "Tavily" and calls[1]["api_key"] == "secret-for-test"
    assert "api_key" not in search.schema()["parameters"]["properties"]


def test_packaged_plugin_loads_and_reads_sources(tmp_path, monkeypatch):
    python = os.environ.get("NEXUS_WEB_TEST_PYTHON")
    if python:
        monkeypatch.setattr("nexus.plugins.environment.prepare", lambda *args, **kwargs: Path(python))
    home = tmp_path / "home"
    home.mkdir()
    mcp = MCPRegistry()
    manager = PluginManager({"store": SimpleNamespace(home=home), "mcp": mcp}, root=tmp_path / "plugins")
    archive = next(Path(__file__).parents[1].glob("nexus-web-research-*.zip"))
    try:
        manager.import_zip(archive.read_bytes())
        manager.load("web-research")
        deadline = time.monotonic() + 180
        item = manager.entries["web-research"]
        while item.status == "loading" and time.monotonic() < deadline:
            time.sleep(.2)
        assert item.status == "ready", item.error
        registry, context = build(root=tmp_path)
        manager.decorate(registry, context, {"kind": "test"})
        names = {spec.name for spec in registry.specs()}
        assert {"nexus_web_search", "nexus_web_read", "nexus_github_read"} <= names
        result = registry.execute("nexus_web_search", {"query": "Python official documentation", "limit": 2}, context)
        assert result.ok, result.content
        assert result.data["results"] and result.data["results"][0]["url"]
        result = registry.execute("nexus_web_read", {"url": "https://example.com"}, context)
        assert result.ok, result.content
        assert "Example Domain" in result.content
        result = registry.execute("nexus_github_read", {"repository": "octocat/Hello-World", "path": "README"}, context)
        assert result.ok, result.content
        assert "Hello World" in result.content
        result = registry.execute("nexus_github_read", {"repository": "octocat/Hello-World"}, context)
        assert result.ok, result.content
        assert any(item["name"] == "README" for item in result.data["items"])
        result = registry.execute("nexus_web_read", {"url": "http://127.0.0.1/"}, context)
        assert not result.ok
    finally:
        manager.close()
        mcp.close()
