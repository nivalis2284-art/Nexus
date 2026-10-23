"""Tests for the MCP client against a real stdio server (the stub)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from nexus.mcp import (
    MCPError,
    MCPRegistry,
    ServerConfig,
    StdioServer,
    scrubbed_parent_env,
)

STUB = Path(__file__).resolve().parent / "mcp_server_stub.py"


def config(**overrides) -> ServerConfig:
    base = {"name": "stub", "command": sys.executable, "args": [str(STUB)]}
    base.update(overrides)
    return ServerConfig.from_dict(base)


def test_secret_env_is_scrubbed():
    os.environ["NEXUS_TEST_SECRET_TOKEN"] = "do-not-leak"
    try:
        env = scrubbed_parent_env()
        assert "NEXUS_TEST_SECRET_TOKEN" not in env
        assert all("TOKEN" not in key.upper() for key in env)
    finally:
        os.environ.pop("NEXUS_TEST_SECRET_TOKEN", None)


def test_explicit_env_is_kept():
    env = scrubbed_parent_env({"MY_VAR": "value"})
    assert env["MY_VAR"] == "value"


def test_initialize_and_paginated_tool_list():
    with StdioServer("stub", sys.executable, [str(STUB)], timeout=20) as server:
        info = server.initialize()
        assert info["serverInfo"]["name"] == "test-server"
        tools = server.list_tools()
        # Two pages, three tools: pagination must be followed.
        assert {tool.name for tool in tools} == {"echo", "fail", "env_probe"}


def test_tool_call_returns_text():
    with StdioServer("stub", sys.executable, [str(STUB)], timeout=20) as server:
        server.initialize()
        result = server.call_tool("echo", {"text": "hello"})
        assert result.ok
        assert result.text == "hello"


def test_error_result_is_reported_not_raised():
    with StdioServer("stub", sys.executable, [str(STUB)], timeout=20) as server:
        server.initialize()
        result = server.call_tool("fail", {})
        assert result.ok is False
        assert result.is_error is True
        assert "deliberate failure" in result.text


def test_protocol_error_is_raised():
    with StdioServer("stub", sys.executable, [str(STUB)], timeout=20) as server:
        server.initialize()
        with pytest.raises(MCPError):
            server.call_tool("no_such_tool", {})


def test_child_does_not_inherit_secrets():
    os.environ["NEXUS_LEAK_SECRET"] = "should-not-appear"
    try:
        with StdioServer("stub", sys.executable, [str(STUB)], timeout=20) as server:
            server.initialize()
            result = server.call_tool("env_probe", {})
            assert result.text == "clean", "a secret-looking env var reached the child"
    finally:
        os.environ.pop("NEXUS_LEAK_SECRET", None)


def test_registry_indexes_tools():
    registry = MCPRegistry()
    try:
        names = registry.add(config())
        assert names == ["mcp__stub__echo", "mcp__stub__fail", "mcp__stub__env_probe"]
        assert set(registry.tools) == set(names)
    finally:
        registry.close()
    assert registry.tools == {}


def test_bad_command_fails_cleanly():
    with pytest.raises(MCPError):
        StdioServer("nope", "definitely-not-a-real-binary-xyz", []).start()


def test_close_reaps_the_server_and_releases_its_pipes():
    server = StdioServer("stub", sys.executable, [str(STUB)], timeout=20)
    server.start()
    server.initialize()
    runner = server._runner
    assert runner is not None

    server.close()

    assert not server.connected
    assert runner.done()
    assert server._session is None


def test_calling_a_stopped_server_reports_not_running():
    server = StdioServer("not-started", sys.executable, [])
    with pytest.raises(MCPError, match="not running"):
        server.call_tool("echo", {})


def test_registry_reports_a_connection_failure_until_removed():
    registry = MCPRegistry()
    with pytest.raises(MCPError):
        registry.add(ServerConfig(name="broken", command="definitely-not-a-real-binary-xyz"))
    status = registry.status()[0]
    assert status["name"] == "broken"
    assert not status["connected"]
    assert status["error"]
    registry.remove("broken")
    assert registry.status() == []
