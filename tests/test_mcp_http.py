"""MCP HTTP connection checks against the official SDK's local server."""
from __future__ import annotations

import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from mcp.server.mcpserver import MCPServer

from nexus.mcp import MCPError, MCPRegistry, RemoteServer, ServerConfig
from nexus.tools.mcp_tools import specs_for


@pytest.fixture
def http_service():
    service = MCPServer("local-http-check")

    @service.tool()
    def echo(value: str) -> dict[str, str]:
        """Return the supplied value as structured content."""
        return {"value": value}

    app = service.streamable_http_app(json_response=True, stateless_http=True)
    seen_tokens = []

    async def authenticated(scope, receive, send):
        if scope["type"] == "http":
            token = dict(scope["headers"]).get(b"authorization", b"")
            seen_tokens.append(token)
            if token != b"Bearer local-test-token":
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await app(scope, receive, send)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    web = uvicorn.Server(uvicorn.Config(authenticated, log_level="error"))
    thread = threading.Thread(target=lambda: web.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not web.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert web.started, "local SDK HTTP server did not start"
        yield address, seen_tokens
    finally:
        web.should_exit = True
        thread.join(timeout=5)
        sock.close()


def test_http_bearer_discovery_call_and_structured_tool_bridge(http_service):
    address, seen = http_service
    registry = MCPRegistry()
    try:
        config = ServerConfig.from_dict({"name": "remote", "transport": "http",
                                         "url": address, "token": "local-test-token"})
        names = registry.add(config)
        assert names == ["mcp__remote__echo"]
        result = registry.servers["remote"].call_tool("echo", {"value": "hello"})
        assert result.ok
        assert result.structured_content == {"value": "hello"}
        spec = specs_for(SimpleNamespace(mcp=registry))[0]
        payload = spec.handler(None, {"value": "from-agent"})
        assert payload["data"] == {"value": "from-agent"}
        assert "from-agent" in payload["content"]
        assert registry.status()[0]["connected"] is True
        assert seen and set(seen) == {b"Bearer local-test-token"}
    finally:
        registry.close()
    assert registry.servers == {}


def test_http_rejected_token_reports_connection_error(http_service):
    address, _seen = http_service
    remote = RemoteServer("rejected", address, "wrong-token", timeout=3)
    try:
        with pytest.raises(MCPError, match="401|authentication"):
            remote.start()
        assert not remote.connected
    finally:
        remote.close()
