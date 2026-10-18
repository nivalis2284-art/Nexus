"""MCP client over stdio: JSON-RPC 2.0, hand-rolled.

Why hand-rolled: the project has exactly two dependencies (httpx, pydantic) and
this protocol is thin — newline-delimited JSON over a child process's pipes.
Adding an SDK would buy convenience and cost a version surface for a protocol
that changes.

What this deliberately does **not** do yet: HTTP transports, sampling
(server-initiated model calls), notifications beyond the ones needed to finish
initialisation, and resource subscriptions. Each is a real feature; none is
needed for "expose a server's tools to the agent".

Security note, learned from ``dsh-mcp-client``: the child must not inherit the
parent's credentials. ``scrubbed_parent_env`` removes anything that looks like a
secret before the process is spawned.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "nexus"
CLIENT_VERSION = "0.4"
DEFAULT_TIMEOUT = 30.0
MAX_LINE_BYTES = 8 * 1024 * 1024

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


class StdioServer:
    """One MCP server process and its request/response bookkeeping."""

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.timeout = timeout

        self._process: subprocess.Popen[bytes] | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._responses: dict[int, dict[str, Any]] = {}
        self._events: dict[int, threading.Event] = {}
        self._reader: threading.Thread | None = None
        self._closed = False
        self._stderr_tail: list[str] = []

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        if self._process is not None:
            return
        try:
            self._process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=scrubbed_parent_env(self.env),
                cwd=str(self.cwd) if self.cwd else None,
                shell=False,
                bufsize=0,
            )
        except OSError as error:
            raise MCPError(f"cannot start '{self.command}': {error}") from error
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def close(self) -> None:
        self._closed = True
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def __enter__(self) -> "StdioServer":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- io --------------------------------------------------------------

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for raw in process.stderr:
            try:
                line = raw.decode("utf-8", errors="replace").rstrip()
            except Exception:  # noqa: BLE001 -- diagnostics only
                continue
            self._stderr_tail.append(line)
            del self._stderr_tail[:-40]

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        while not self._closed:
            try:
                raw = process.stdout.readline()
            except (OSError, ValueError):
                break
            if not raw:
                break
            if len(raw) > MAX_LINE_BYTES:
                continue
            try:
                payload = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                # A server that prints non-JSON to stdout is misbehaving, but it
                # must not take the client down.
                continue
            if not isinstance(payload, dict):
                continue
            request_id = payload.get("id")
            if request_id is None:
                continue  # a notification; nothing to do here
            with self._lock:
                self._responses[int(request_id)] = payload
                event = self._events.get(int(request_id))
            if event is not None:
                event.set()
        # Unblock every waiter: the process is gone.
        with self._lock:
            for event in self._events.values():
                event.set()

    def _send(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPError(f"server '{self.name}' is not running")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        try:
            process.stdin.write(line.encode("utf-8"))
            process.stdin.flush()
        except (OSError, ValueError) as error:
            raise MCPError(f"write to '{self.name}' failed: {error}") from error

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            event = threading.Event()
            self._events[request_id] = event
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        )
        if not event.wait(self.timeout):
            raise MCPError(f"'{self.name}' did not answer {method} within {self.timeout}s")
        with self._lock:
            payload = self._responses.pop(request_id, None)
            self._events.pop(request_id, None)
        if payload is None:
            raise MCPError(f"'{self.name}' closed before answering {method}")
        if "error" in payload:
            error = payload["error"] or {}
            raise MCPError(
                str(error.get("message") or "server error"), error.get("code")
            )
        result = payload.get("result")
        return result if isinstance(result, dict) else {}

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # --- protocol --------------------------------------------------------

    def initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        self._notify("notifications/initialized")
        return result

    def list_tools(self) -> list[ToolDeclaration]:
        tools: list[ToolDeclaration] = []
        cursor: str | None = None
        for _ in range(20):  # bounded: a server must not paginate forever
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            for item in result.get("tools") or []:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name:
                    continue
                schema = item.get("inputSchema")
                tools.append(
                    ToolDeclaration(
                        name=name,
                        description=str(item.get("description") or ""),
                        input_schema=schema if isinstance(schema, dict) else {},
                    )
                )
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> CallResult:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        blocks = result.get("content") or []
        parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text":
                parts.append(str(block.get("text") or ""))
            elif kind == "resource":
                resource = block.get("resource") or {}
                parts.append(str(resource.get("text") or ""))
            elif kind in ("image", "audio"):
                parts.append(f"[{kind} omitted: {block.get('mimeType', 'unknown')}]")
            else:
                parts.append(json.dumps(block, ensure_ascii=False))
        text = "\n".join(part for part in parts if part)
        return CallResult(
            ok=not result.get("isError", False),
            text=text or "(empty result)",
            is_error=bool(result.get("isError", False)),
        )


@dataclass
class ServerConfig:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ServerConfig | None":
        name = str(raw.get("name") or "").strip()
        command = str(raw.get("command") or "").strip()
        if not name or not command:
            return None
        args = raw.get("args")
        env = raw.get("env")
        return cls(
            name=name,
            command=command,
            args=[str(a) for a in args] if isinstance(args, list) else [],
            env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            enabled=bool(raw.get("enabled", True)),
        )


class MCPRegistry:
    """Owns the live servers for one process lifetime."""

    def __init__(self) -> None:
        self.servers: dict[str, StdioServer] = {}
        self.tools: dict[str, tuple[StdioServer, ToolDeclaration]] = {}

    def add(self, config: ServerConfig, *, cwd: Path | None = None) -> list[str]:
        """Connect and index one server's tools. Returns the qualified names."""
        server = StdioServer(
            name=config.name,
            command=config.command,
            args=config.args,
            env=config.env,
            cwd=cwd,
        )
        server.start()
        try:
            server.initialize()
            declarations = server.list_tools()
        except MCPError:
            server.close()
            raise
        self.servers[config.name] = server
        names: list[str] = []
        for declaration in declarations:
            qualified = qualify(config.name, declaration.name)
            self.tools[qualified] = (server, declaration)
            names.append(qualified)
        return names

    def close(self) -> None:
        for server in self.servers.values():
            server.close()
        self.servers.clear()
        self.tools.clear()


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
