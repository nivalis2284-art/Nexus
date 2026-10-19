"""Tests for the MCP client against a real stdio server (the stub)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

from nexus.mcp import (
    MCPError,
    MCPRegistry,
    ServerConfig,
    StdioServer,
    qualify,
    scrubbed_parent_env,
    split_qualified,
)

STUB = Path(__file__).resolve().parent / "mcp_server_stub.py"


def config(**overrides) -> ServerConfig:
    base = {"name": "stub", "command": sys.executable, "args": [str(STUB)]}
    base.update(overrides)
    return ServerConfig.from_dict(base)


def test_qualified_names_round_trip():
    assert qualify("fs", "read") == "mcp__fs__read"
    assert split_qualified("mcp__fs__read") == ("fs", "read")
    assert split_qualified("read_file") is None
    assert split_qualified("mcp__onlyserver") is None


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
    process = server._process
    assert process is not None

    server.close()

    assert process.poll() is not None
    assert server._reader is not None
    assert not server._reader.is_alive()
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed


def test_close_wakes_a_request_that_is_still_waiting():
    class Stream:
        closed = False

        def close(self):
            self.closed = True

    class Process:
        stdin = Stream()
        stdout = Stream()
        stderr = Stream()

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    server = StdioServer("stub", sys.executable, [])
    process = Process()
    server._process = process
    server._send = lambda payload: None
    outcome = []

    def request():
        try:
            server._request("slow")
        except MCPError as error:
            outcome.append(error)

    worker = threading.Thread(target=request)
    worker.start()
    deadline = time.monotonic() + 2
    while not server._events and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server._events
    started = time.monotonic()
    server.close()
    worker.join(1)
    assert not worker.is_alive()
    assert time.monotonic() - started < 1
    assert outcome and outcome[0].code is None
    assert "was closed" in str(outcome[0])
    assert process.stdin.closed
    assert process.stdout.closed
    assert process.stderr.closed


def test_send_failure_releases_request_bookkeeping():
    server = StdioServer("not-started", sys.executable, [])
    with pytest.raises(MCPError, match="not running"):
        server._request("tools/list")
    assert server._events == {}
    assert server._responses == {}
