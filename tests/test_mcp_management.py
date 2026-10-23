"""MCP settings CRUD against a fake live registry and a temporary config home."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nexus.core.config import ConfigStore
from nexus.mcp import ServerConfig
from nexus.web import mcp_api


class FakeRegistry:
    """Record lifecycle calls and report a realistic SDK transport name."""

    def __init__(self) -> None:
        self.added = []
        self.removed = []
        self.reconnected = []
        self.rows = {}

    def add(self, config, *, cwd=None):
        # MCPRegistry.add replaces a same-named live connection first.
        self.remove(config.name)
        self.added.append((config, cwd))
        self.rows[config.name] = {
            "name": config.name,
            "transport": config.transport,
            "connected": True,
            "tool_count": 1,
            "error": "",
        }
        return [f"mcp__{config.name}__echo"]

    def remove(self, name):
        self.removed.append(name)
        self.rows.pop(name, None)

    def reconnect(self, config, *, cwd=None):
        self.reconnected.append((config, cwd))
        self.rows[config.name] = {
            "name": config.name,
            "transport": config.transport,
            "connected": True,
            "tool_count": 1,
            "error": "",
        }
        return [f"mcp__{config.name}__echo"]

    def status(self):
        return list(self.rows.values())


@pytest.fixture
def management(tmp_path):
    store = ConfigStore(tmp_path / "home")
    store.ensure()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store.save_preferences(workspace=str(workspace))
    registry = FakeRegistry()
    return {"store": store, "mcp": registry}, registry, workspace


def stdio(name="local", **overrides):
    values = {
        "name": name,
        "transport": "stdio",
        "command": "python",
        "args": ["server.py"],
        "env": {"MODE": "test"},
        "enabled": True,
        "url": "",
    }
    values.update(overrides)
    return values


def http(name="remote", **overrides):
    values = {
        "name": name,
        "transport": "http",
        "command": "",
        "args": [],
        "env": {},
        "enabled": True,
        "url": "https://mcp.example.test/mcp",
    }
    values.update(overrides)
    return values


def servers(store):
    return (store.read_config().get("mcp") or {}).get("servers") or []


def test_create_read_rename_and_delete_keep_config_and_registry_in_step(management):
    state, registry, workspace = management
    store = state["store"]

    created = mcp_api.save(state, stdio())
    assert created["servers"] == [{
        "name": "local",
        "transport": "stdio",
        "command": "python",
        "args": ["server.py"],
        "env": {"MODE": "test"},
        "enabled": True,
        "url": "",
        "has_token": False,
        "connected": True,
        "tool_count": 1,
        "error": "",
    }]
    config, cwd = registry.added[-1]
    assert (config.name, config.command, config.args) == ("local", "python", ["server.py"])
    assert cwd == str(workspace)
    assert servers(store) == [{
        "name": "local",
        "transport": "stdio",
        "command": "python",
        "args": ["server.py"],
        "env": {"MODE": "test"},
        "enabled": True,
        "url": "",
    }]

    renamed = mcp_api.save(state, stdio(
        "renamed", previous_name="local", command="python3", args=["other.py"],
    ))
    assert "local" in registry.removed
    assert "local" not in registry.rows
    assert "renamed" in registry.rows
    assert registry.added[-1][0].name == "renamed"
    assert registry.added[-1][0].command == "python3"
    assert [entry["name"] for entry in servers(store)] == ["renamed"]
    assert renamed["servers"][0]["name"] == "renamed"

    deleted = mcp_api.command(state, "renamed", "delete")
    assert deleted == {"servers": []}
    assert registry.removed[-1] == "renamed"
    assert servers(store) == []
    assert "mcp:renamed" not in store.read_credentials()


def test_rename_rejects_a_missing_previous_name_without_touching_credentials(management):
    state, registry, _workspace = management
    store = state["store"]
    store.write_credential("mcp:missing", "keep-me")

    with pytest.raises(ValueError, match="原服务不存在"):
        mcp_api.save(state, stdio("new", previous_name="missing"))

    assert registry.removed == []
    assert registry.added == []
    assert store.read_credentials()["mcp:missing"] == "keep-me"
    assert "mcp:new" not in store.read_credentials()
    assert servers(store) == []


def test_snapshot_hides_tokens_and_preserves_the_configured_http_transport(management):
    state, registry, _workspace = management
    store = state["store"]

    saved = mcp_api.save(state, {**http(), "token": "secret-remote-token"})
    listed = mcp_api.snapshot(state)
    for payload in (saved, listed):
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "secret-remote-token" not in serialized
        assert "token" not in payload["servers"][0]
        assert payload["servers"][0]["has_token"] is True
        assert payload["servers"][0]["transport"] == "http"
    assert registry.rows["remote"]["transport"] == "streamable-http"
    assert "secret-remote-token" not in store.config_path.read_text(encoding="utf-8")
    assert store.read_credentials()["mcp:remote"] == "secret-remote-token"

    renamed = mcp_api.save(state, http("renamed", previous_name="remote"))
    assert "secret-remote-token" not in json.dumps(renamed, ensure_ascii=False)
    assert renamed["servers"][0]["has_token"] is True
    assert store.read_credentials()["mcp:renamed"] == "secret-remote-token"
    assert "mcp:remote" not in store.read_credentials()
    assert servers(store)[0]["name"] == "renamed"
    assert "token" not in servers(store)[0]


def test_disable_and_reenable_control_the_connection_and_keep_the_token(management):
    state, registry, _workspace = management
    store = state["store"]
    mcp_api.save(state, {**http(), "token": "kept-token"})

    disabled = mcp_api.save(state, http("remote", enabled=False))
    assert servers(store)[0]["enabled"] is False
    assert disabled["servers"][0]["connected"] is False
    assert disabled["servers"][0]["has_token"] is True
    assert "kept-token" not in json.dumps(disabled, ensure_ascii=False)
    assert registry.removed[-1] == "remote"
    assert "remote" not in registry.rows
    assert store.read_credentials()["mcp:remote"] == "kept-token"

    with pytest.raises(ValueError, match="已停用"):
        mcp_api.command(state, "remote", "reconnect")
    assert registry.reconnected == []
    assert "remote" not in registry.rows

    enabled = mcp_api.save(state, http("remote", enabled=True))
    assert enabled["servers"][0]["connected"] is True
    assert registry.added[-1][0].token == "kept-token"
    assert servers(store)[0]["enabled"] is True
    assert "kept-token" not in json.dumps(enabled, ensure_ascii=False)


def test_reconnect_uses_the_saved_entry_and_current_workspace(management, tmp_path):
    state, registry, _workspace = management
    store = state["store"]
    mcp_api.save(state, {**http(), "token": "reconnect-token"})
    moved = tmp_path / "moved"
    moved.mkdir()
    store.save_preferences(workspace=str(moved))

    result = mcp_api.command(state, "remote", "reconnect")

    config, cwd = registry.reconnected[-1]
    assert (config.name, config.url, config.token) == (
        "remote", "https://mcp.example.test/mcp", "reconnect-token",
    )
    assert cwd == str(moved)
    assert result["servers"][0]["connected"] is True
    assert result["servers"][0]["transport"] == "http"
    assert "reconnect-token" not in json.dumps(result, ensure_ascii=False)


def test_managed_scheduler_reports_plugin_ownership_and_leaves_with_plugin(management):
    state, _registry, _workspace = management
    item = SimpleNamespace(id="autonomy", services={"scheduler": "plugin_autonomy_scheduler"})
    state["plugins"] = SimpleNamespace(entries={"autonomy": item})
    listed = mcp_api.snapshot(state)["servers"]
    assert listed == [{
        "name": "plugin_autonomy_scheduler", "transport": "stdio", "enabled": True,
        "managed": True, "plugin_id": "autonomy", "service": "scheduler",
        "has_token": False, "connected": False, "tool_count": 0, "error": "",
    }]
    item.services.clear()
    assert mcp_api.snapshot(state)["servers"] == []
