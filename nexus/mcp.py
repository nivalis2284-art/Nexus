"""MCP client connections over the official SDK.

The SDK is asynchronous while Nexus' agent and tool dispatcher are synchronous.
One dedicated event loop thread owns every connection, and each connection's
transport and session stay open in one long-lived coroutine on that loop.  This
keeps anyio's cancel scopes on the task that entered them; callers on agent
threads only submit short coroutines with ``run_coroutine_threadsafe``.

Both stdio and Streamable HTTP are supported.  Remote servers may carry a
bearer token; the SDK handles the protocol, pagination, and transport shutdown.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from concurrent.futures import CancelledError
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
from mcp import ClientSession, StdioServerParameters, stdio_client, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError as SDKError

CLIENT_NAME = "nexus"
CLIENT_VERSION = "0.4"
DEFAULT_TIMEOUT = 30.0

# Names that must never reach a third-party server process.
_SECRET_HINTS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL",
    "AUTH", "COOKIE", "SESSION", "PRIVATE",
)


class MCPError(RuntimeError):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


def scrubbed_parent_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """A child environment with credential-shaped variables removed."""
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(hint in upper for hint in _SECRET_HINTS):
            continue
        env[key] = value
    env.update(extra or {})
    return env


@dataclass
class ToolDeclaration:
    """One tool as the server describes it, before we adapt it."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class CallResult:
    ok: bool
    text: str
    is_error: bool = False
    structured_content: Any = None


class _LoopThread:
    """One process-wide asyncio loop for MCP transport and session lifecycles."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="nexus-mcp", daemon=True
        )
        self._thread.start()
        self._ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def run(self, coro: Any, timeout: float | None = None) -> Any:
        if not self._thread.is_alive():
            raise MCPError("MCP event loop is not running")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except TimeoutError as error:
            future.cancel()
            raise MCPError(f"MCP request timed out after {timeout}s") from error

    def stop(self) -> None:
        if not self._thread.is_alive():
            return
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


_loop_lock = threading.Lock()
_loop_thread: _LoopThread | None = None


def _loop() -> _LoopThread:
    global _loop_thread
    with _loop_lock:
        if _loop_thread is None or not _loop_thread._thread.is_alive():
            _loop_thread = _LoopThread()
        return _loop_thread


def _sdk_error(error: BaseException) -> MCPError:
    if isinstance(error, MCPError):
        return error
    if isinstance(error, SDKError):
        return MCPError(str(error), getattr(error, "code", None))
    if isinstance(error, BaseExceptionGroup):
        nested = next(
            (
                _sdk_error(item)
                for item in error.exceptions
                if not isinstance(item, CancelledError)
            ),
            None,
        )
        if nested is not None:
            return nested
    return MCPError(f"{type(error).__name__}: {error}")


def _declaration(tool: types.Tool) -> ToolDeclaration:
    schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
    return ToolDeclaration(
        name=tool.name,
        description=tool.description or "",
        input_schema=schema,
    )


def _content_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        if isinstance(block, types.TextContent):
            parts.append(block.text)
        elif isinstance(block, types.EmbeddedResource):
            resource = block.resource
            text = getattr(resource, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                parts.append(
                    f"[resource blob omitted: {getattr(resource, 'mime_type', None) or 'unknown'}]"
                )
        elif isinstance(block, (types.ImageContent, types.AudioContent)):
            parts.append(
                f"[{block.type} omitted: {getattr(block, 'mime_type', None) or 'unknown'}]"
            )
        else:
            dumped = block.model_dump(by_alias=True, exclude_none=True)
            parts.append(json.dumps(dumped, ensure_ascii=False))
    return "\n".join(part for part in parts if part)


class _HTTPTransport:
    """Async context manager that also owns the SDK HTTP client."""

    def __init__(
        self, url: str, headers: dict[str, str] | None, timeout: float
    ) -> None:
        self.url = url
        self.headers = headers
        self.timeout = timeout
        self._stack = AsyncExitStack()
        self._status: list[int] = []

    async def __aenter__(self) -> Any:
        client = create_mcp_http_client(
            headers=self.headers,
            timeout=httpx2.Timeout(self.timeout, read=self.timeout),
        )

        async def note_status(response: httpx2.Response) -> None:
            self._status.append(response.status_code)

        client.event_hooks["response"].append(note_status)
        await self._stack.enter_async_context(client)
        return await self._stack.enter_async_context(
            streamable_http_client(self.url, http_client=client)
        )

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.__aexit__(*exc)

    def authentication_error(self) -> str | None:
        if 401 in self._status:
            return "authentication rejected (HTTP 401)"
        if 403 in self._status:
            return "authorization rejected (HTTP 403)"
        return None


class _Client:
    """Shared surface for one MCP connection."""

    transport = ""

    def __init__(self, name: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.name = name
        self.timeout = timeout
        self.connected = False
        self.error: str | None = None
        self.tools: list[ToolDeclaration] = []
        self._runner: asyncio.Task[None] | None = None
        self._session: ClientSession | None = None
        self._info: dict[str, Any] = {}
        self._ready: asyncio.Future[None] | None = None
        self._closed: asyncio.Event | None = None
        self._on_tools_changed: Any = None

    def set_tools_changed_callback(self, callback: Any) -> None:
        self._on_tools_changed = callback

    async def _message(self, message: Any) -> None:
        if isinstance(message, types.ToolListChangedNotification):
            await self._refresh_tools()
        elif isinstance(message, Exception):
            self.error = f"{type(message).__name__}: {message}"

    async def _refresh_tools(self) -> None:
        if self._session is None:
            return
        tools: list[ToolDeclaration] = []
        cursor: str | None = None
        for _ in range(100):
            params = types.PaginatedRequestParams(cursor=cursor) if cursor else None
            result = await self._session.list_tools(params=params)
            tools.extend(_declaration(tool) for tool in result.tools)
            cursor = result.next_cursor
            if not cursor:
                break
        self.tools = tools
        if self._on_tools_changed is not None:
            self._on_tools_changed(self)

    async def _lifecycle(self) -> None:
        assert self._ready is not None and self._closed is not None
        transport = self._transport()
        try:
            async with transport as (read, write):
                async with ClientSession(
                    read,
                    write,
                    read_timeout_seconds=self.timeout,
                    message_handler=self._message,
                    client_info=types.Implementation(
                        name=CLIENT_NAME, version=CLIENT_VERSION
                    ),
                ) as session:
                    self._session = session
                    info = await session.initialize()
                    self._info = info.model_dump(
                        by_alias=True, exclude_none=True, mode="json"
                    )
                    self.connected = True
                    self.error = None
                    await self._refresh_tools()
                    if not self._ready.done():
                        self._ready.set_result(None)
                    await self._closed.wait()
        except BaseException as error:
            failure = _sdk_error(error)
            auth = getattr(transport, "authentication_error", None)
            detail = auth() if auth is not None else None
            if detail is not None:
                failure = MCPError(detail, failure.code)
            self.error = str(failure)
            if not self._ready.done():
                self._ready.set_exception(failure)
        finally:
            self.connected = False
            self._session = None
            if not self._ready.done():
                self._ready.set_exception(
                    MCPError(f"server '{self.name}' closed before initializing")
                )

    def _transport(self) -> Any:
        raise NotImplementedError

    def start(self) -> None:
        if self._runner is not None:
            return
        loop = _loop()

        async def launch() -> None:
            self._ready = loop.loop.create_future()
            self._closed = asyncio.Event()
            self._runner = loop.loop.create_task(self._lifecycle())
            try:
                await asyncio.wait_for(asyncio.shield(self._ready), self.timeout)
            except TimeoutError as error:
                raise MCPError(
                    f"'{self.name}' did not initialize within {self.timeout}s"
                ) from error

        try:
            loop.run(launch(), self.timeout + 5)
        except BaseException:
            self.close()
            raise

    def initialize(self) -> dict[str, Any]:
        self.start()
        return dict(self._info)

    def list_tools(self) -> list[ToolDeclaration]:
        return list(self.tools)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> CallResult:
        if not self.connected or self._session is None:
            raise MCPError(f"server '{self.name}' is not running")
        session = self._session

        async def invoke() -> CallResult:
            result = await session.call_tool(name, arguments)
            text = _content_text(result.content)
            return CallResult(
                ok=not result.is_error,
                text=text or "(empty result)",
                is_error=bool(result.is_error),
                structured_content=result.structured_content,
            )

        try:
            return _loop().run(invoke(), self.timeout)
        except BaseException as error:
            raise _sdk_error(error) from error

    def close(self) -> None:
        if self._runner is None:
            return
        runner = self._runner
        try:
            loop = _loop()
            if loop.loop.is_closed():
                return

            async def stop() -> None:
                if self._closed is not None:
                    self._closed.set()
                try:
                    await asyncio.wait_for(asyncio.shield(runner), self.timeout)
                except TimeoutError:
                    runner.cancel()
                    try:
                        await runner
                    except BaseException:
                        pass

            loop.run(stop(), self.timeout + 5)
        except BaseException:
            pass
        finally:
            self.connected = False
            self._session = None
            self._runner = None
            if self._closed is not None:
                self._closed.set()

    def __enter__(self) -> _Client:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


class StdioServer(_Client):
    """One MCP server process connected over stdio."""

    transport = "stdio"

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(name, timeout)
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd

    def _transport(self) -> Any:
        argv = resolve_command([self.command, *self.args])
        params = StdioServerParameters(
            command=argv[0],
            args=argv[1:],
            env=scrubbed_parent_env(self.env),
            cwd=self.cwd,
        )
        return stdio_client(params)


class RemoteServer(_Client):
    """One Streamable HTTP MCP server."""

    transport = "streamable-http"

    def __init__(
        self,
        name: str,
        url: str,
        token: str = "",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        super().__init__(name, timeout)
        self.url = url
        self.token = token

    def _transport(self) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        return _HTTPTransport(self.url, headers, self.timeout)


@dataclass
class ServerConfig:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    transport: str = "stdio"
    url: str = ""
    token: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ServerConfig | None:
        name = str(raw.get("name") or "").strip()
        if not name:
            return None
        transport = str(raw.get("transport") or "stdio").strip().lower()
        command = str(raw.get("command") or "").strip()
        url = str(raw.get("url") or "").strip()
        if transport == "stdio" and not command:
            return None
        if transport in ("http", "streamable-http", "streamable_http"):
            transport = "streamable-http"
            if not url:
                return None
        elif transport != "stdio":
            return None
        args = raw.get("args")
        env = raw.get("env")
        return cls(
            name=name,
            command=command,
            args=[str(a) for a in args] if isinstance(args, list) else [],
            env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            enabled=bool(raw.get("enabled", True)),
            transport=transport,
            url=url,
            token=str(raw.get("token") or ""),
        )


class MCPRegistry:
    """Owns the live servers for one execution scope."""

    def __init__(self) -> None:
        self.servers: dict[str, _Client] = {}
        self.tools: dict[str, tuple[_Client, ToolDeclaration]] = {}
        self._failures: dict[str, dict[str, Any]] = {}

    def _index(self, client: _Client) -> list[str]:
        for qualified, (owner, _declaration) in list(self.tools.items()):
            if owner is client:
                del self.tools[qualified]
        names: list[str] = []
        for declaration in client.tools:
            qualified = qualify(client.name, declaration.name)
            self.tools[qualified] = (client, declaration)
            names.append(qualified)
        return names

    def add(self, config: ServerConfig, *, cwd: Path | None = None) -> list[str]:
        """Connect and index one server's tools. Returns the qualified names."""
        self.remove(config.name)
        if config.transport == "streamable-http":
            server: _Client = RemoteServer(config.name, config.url, config.token)
        else:
            server = StdioServer(
                name=config.name,
                command=config.command,
                args=config.args,
                env=config.env,
                cwd=cwd,
            )
        server.set_tools_changed_callback(self._index)
        try:
            server.start()
        except BaseException as error:
            server.close()
            self._failures[config.name] = {
                "name": config.name,
                "transport": server.transport,
                "connected": False,
                "error": str(error),
                "tool_count": 0,
            }
            raise
        self.servers[config.name] = server
        return self._index(server)

    def remove(self, name: str) -> None:
        """Disconnect and forget one server, including a previous failure."""
        self._failures.pop(name, None)
        server = self.servers.pop(name, None)
        if server is not None:
            for qualified, (owner, _declaration) in list(self.tools.items()):
                if owner is server:
                    del self.tools[qualified]
            server.close()

    def reconnect(self, config: ServerConfig, *, cwd: Path | None = None) -> list[str]:
        self.remove(config.name)
        return self.add(config, cwd=cwd)

    def status(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, server in self.servers.items():
            rows.append(
                {
                    "name": name,
                    "transport": server.transport,
                    "connected": server.connected,
                    "error": server.error,
                    "tool_count": sum(
                        1 for owner, _declaration in self.tools.values() if owner is server
                    ),
                }
            )
        for name, failure in self._failures.items():
            if name not in self.servers:
                rows.append(dict(failure))
        return rows

    def close(self) -> None:
        for server in list(self.servers.values()):
            server.close()
        self.servers.clear()
        self.tools.clear()
        self._failures.clear()


def resolve_command(argv: list[str]) -> list[str]:
    """Make a command runnable without a shell, on Windows too.

    ``npx``, ``uvx``, ``pnpm`` and friends are ``.cmd`` shims on Windows, and
    ``CreateProcess`` cannot execute them directly.  Node's own launcher is used
    when it can be found, because that keeps the process tree clean.  If it
    cannot, the shim is handed to ``cmd.exe`` instead; arguments stay list-based.
    """
    if os.name != "nt" or not argv:
        return argv

    import shutil

    command = argv[0]
    located = shutil.which(command) or command
    lowered = located.lower()

    if not lowered.endswith((".cmd", ".bat")):
        return [located, *argv[1:]]

    node = shutil.which("node")
    if node:
        shim = Path(located)
        candidate = shim.parent / "node_modules" / "npm" / "bin" / f"{shim.stem}-cli.js"
        if candidate.is_file():
            return [node, str(candidate), *argv[1:]]
        candidate_alt = shim.parent / "node_modules" / "npm" / "bin" / "npx-cli.js"
        if shim.stem.lower() == "npx" and candidate_alt.is_file():
            return [node, str(candidate_alt), *argv[1:]]

    return ["cmd.exe", "/c", located, *argv[1:]]


def qualify(server_name: str, tool_name: str) -> str:
    """Namespaced so two servers cannot shadow each other or a builtin."""
    return f"mcp__{server_name}__{tool_name}"


def split_qualified(qualified: str) -> tuple[str, str] | None:
    if not qualified.startswith("mcp__"):
        return None
    rest = qualified[len("mcp__"):]
    if "__" not in rest:
        return None
    server, _, tool = rest.partition("__")
    return (server, tool) if server and tool else None
