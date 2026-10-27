"""Standard-library HTTP transport and static UI for the Agent services.

Ling and accepted work run on the server; browser connections only submit
inputs and read durable records.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from ..application import (Application, MAX_IMAGE_BYTES, MAX_IMAGES_PER_TURN, ALLOWED_IMAGE_MIMES)
from ..attachments import AttachmentStore
from ..core import effort as reasoning
from ..core.config import ConfigStore
from ..ling import LingInbox
from ..mcp import MCPError, MCPRegistry
from ..runtime.connections import start_mcp
from ..session import SessionStore
from ..task import TaskConflict, TaskNotFound, TaskStore, TaskValidationError
from . import instructions_api, mcp_api, plugin_api, skills_api
from .attachments import route as attachment_route
from .task_api import TaskHTTP

# Upload limits. Base64 inflates by ~33%, so 5MB becomes ~6.7MB of JSON; eight
# of those is already a large request, which is why the count is capped too.
MAX_REQUEST_BYTES = MAX_IMAGES_PER_TURN * ((MAX_IMAGE_BYTES + 2) // 3 * 4) + 4_000_000

def _content_blocks(text: str, images: list[dict[str, str]]) -> Any:
    """Build the internal content value: plain string when there are no images."""
    if not images:
        return text
    blocks: list[dict[str, Any]] = []
    if text:
        blocks.append({"type": "text", "text": text})
    for image in images:
        blocks.append({"type": "image", "mime": image["mime"], "data": image["data"]})
    return blocks


STATE: dict[str, Any] = {}


def _task_http() -> TaskHTTP:
    return TaskHTTP(STATE, _json)


def _application() -> Application:
    app = STATE.get("application")
    if app is None:
        app = Application(STATE)
    return app


def _ling() -> LingInbox:
    return _application().inbox()


def _task_error(handler: BaseHTTPRequestHandler, error: BaseException) -> None:
    status = 404 if isinstance(error, TaskNotFound) else 409 if isinstance(error, TaskConflict) else 400
    _json(handler, status, {"error": str(error)})


def _json(handler: BaseHTTPRequestHandler, status: int, value: Any) -> None:
    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return {}
    if length <= 0 or length > MAX_REQUEST_BYTES:
        return {}
    try:
        value = json.loads(handler.rfile.read(length))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


# --- request origin ------------------------------------------------------
#
# Binding to loopback is not an authorization boundary: every page the user
# visits can reach 127.0.0.1, and DNS rebinding can point a hostile hostname
# here. Three checks run before any route, outermost first:
#
#   Host    must be this service's own loopback authority. A rebound name no
#           longer matches, so the request dies before it is routed.
#   Origin  when present, must be this service's own origin. A browser always
#           sends it on the cross-site writes this blocks.
#   Type    a write that carries a body must be application/json. A browser
#           cannot send that cross-origin without a CORS preflight, which this
#           server never grants; text/plain is the shape an attacker can send.
#
# A missing Origin is accepted: curl and the existing tests do not send one,
# and a browser cannot omit it on a cross-site write.

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _service_port(handler: BaseHTTPRequestHandler) -> int | None:
    """The port this process actually bound, for comparing Host and Origin."""
    address = getattr(getattr(handler, "server", None), "server_address", None)
    if isinstance(address, tuple) and len(address) >= 2:
        try:
            return int(address[1])
        except (TypeError, ValueError):
            return None
    return None


def _parsed_port(url) -> int | None:
    """``url.port`` with its ValueError turned into "no usable port"."""
    try:
        return url.port
    except ValueError:
        return None


def _authority_allowed(url, expected: int | None) -> bool:
    """True when ``url`` names loopback on the port this service bound.

    A browser omits the port when it is the scheme default (80), so that one
    spelling is accepted; everything else must match exactly.
    """
    if expected is None or url is None or url.hostname not in LOOPBACK_HOSTS:
        return False
    port = _parsed_port(url)
    return port == expected or (port is None and expected == 80)


def _host_allowed(value: str, expected: int | None) -> bool:
    """Check a Host header. Rejects delimiters, userinfo and unknown ports."""
    if not value or any(ch in value for ch in " \t\r\n/,@"):
        return False
    try:
        parsed = urlsplit("//" + value)
    except ValueError:
        return False
    return _authority_allowed(parsed, expected)


def _origin_allowed(value: str, expected: int | None) -> bool:
    """Check an Origin header: an http origin on this service, nothing else."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != "http" or parsed.username or parsed.password:
        return False
    # An Origin is only ever scheme://host[:port]; a path or query means this
    # is not a browser origin, or someone is probing.
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return False
    return _authority_allowed(parsed, expected)


def _body_present(handler: BaseHTTPRequestHandler) -> bool:
    """Whether this request declares a body worth guarding."""
    raw = handler.headers.get("Content-Length")
    if raw is not None:
        try:
            if int(raw) > 0:
                return True
        except ValueError:
            return True  # unparsable length: assume a body
    return bool(handler.headers.get("Transfer-Encoding"))


def _json_content_type(value: str | None) -> bool:
    """``application/json`` with an optional parameter such as charset."""
    if not value:
        return False
    return value.split(";", 1)[0].strip().lower() == "application/json"


# Enough to swallow any body these tests and a browser will send, without
# letting a refused request spend unbounded time reading.
# Enough to swallow any body a browser or these tests will send. A request
# declaring more than this is refused without draining: a refused request must
# not be a way to make the server read arbitrarily much.
DRAIN_BYTES = 1024 * 1024


def _drain(handler: BaseHTTPRequestHandler) -> None:
    """Read a refused request's body so the response is not lost to a reset.

    Closing a socket that still has unread inbound bytes makes the OS answer
    with RST, and the client then sees a connection error instead of the 403.
    Reading the declared body first lets the response land. Bounded, so this
    cannot be used to make the server read forever.
    """
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        return
    if length <= 0 or length > DRAIN_BYTES:
        return
    remaining = length
    try:
        while remaining > 0:
            chunk = handler.rfile.read(min(remaining, 65536))
            if not chunk:
                return
            remaining -= len(chunk)
    except OSError:
        return


def _is_attachment_upload(path: str) -> bool:
    """Mirror attachments.route's POST shape: raw bytes, never JSON.

    Uploads carry a browser-chosen Content-Type and stream the file directly,
    so the JSON requirement must not apply to them. Host and Origin still do.
    """
    parts = path.strip("/").split("/")
    return (
        len(parts) == 4
        and parts[0] == "api"
        and parts[1] in ("sessions", "tasks")
        and parts[3] == "attachments"
    )


def _transcript(session) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in session.items:
        if item.kind in ("user", "assistant"):
            if item.kind == "assistant" and not item.text and not item.data.get("thinking"):
                continue
            out.append(
                {
                    "role": item.kind,
                    "message_id": item.data.get("message_id"),
                    "text": item.text,
                    "thinking": item.data.get("thinking") or "",
                    "usage": item.data.get("usage") or None,
                    "elapsed": item.data.get("elapsed"),
                    "at": item.at,
                    "images": item.data.get("images") or [],
                    "attachments": item.data.get("attachments") or [],
                    "automation_run_id": item.data.get("automation_run_id"),
                    "plugin_id": item.data.get("plugin_id"),
                    "plugin_event_id": item.data.get("plugin_event_id"),
                    "sources": item.data.get("sources") or [],
                }
            )
        elif item.kind == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": item.data.get("tool_call_id"),
                    "parent_message_id": item.data.get("parent_message_id"),
                    "name": item.data.get("name", ""),
                    "text": item.text,
                    "ok": bool(item.data.get("ok", True)),
                    "arguments": item.data.get("arguments"),
                    "error": item.data.get("error", ""),
                    "at": item.at,
                }
            )
        elif item.kind == "notice":
            out.append({"role": "notice", "text": item.text, "at": item.at})
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "Nexus/0.2"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    # --- request guard ---------------------------------------------------

    def parse_request(self) -> bool:
        """Parse, then refuse anything not addressed to this loopback service.

        This runs for every method, so a refused request never reaches a route,
        an attachment store or the agent. It is the only place that decides
        admission; the individual do_* methods assume it already passed.
        """
        if not super().parse_request():
            return False
        return self._admit()

    def _admit(self) -> bool:
        port = _service_port(self)
        if not _host_allowed(self.headers.get("Host") or "", port):
            # Also covers DNS rebinding: a hostile name does not spell loopback.
            return self._refuse(403, "Host 头不是本机地址，已拒绝。")
        origin = self.headers.get("Origin")
        if origin is not None and not _origin_allowed(origin, port):
            return self._refuse(403, "跨来源请求已拒绝。")
        if self.command in ("POST", "PUT", "DELETE"):
            path = urlparse(self.path).path
            # Uploads stream raw browser bytes with a caller-chosen type.
            if path == '/api/plugins/import':
                if (self.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower() != 'application/zip':
                    return self._refuse(415, '插件导入需要 application/zip。')
            elif not _is_attachment_upload(path):
                content_type = self.headers.get("Content-Type")
                if (_body_present(self) or content_type is not None) and not _json_content_type(
                    content_type
                ):
                    return self._refuse(415, "写请求必须使用 application/json。")
        return True

    def _refuse(self, status: int, message: str) -> bool:
        """Answer and close, without letting the body reach a route."""
        # Unread bytes in the socket turn the close into an RST, which would
        # lose this response on the wire; swallow the declared body first.
        _drain(self)
        self.close_connection = True
        body = json.dumps({"error": message}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        try:
            self.wfile.flush()
        except OSError:
            pass
        return False

    # --- routing ---------------------------------------------------------

    def do_GET(self) -> None:
        if plugin_api.route(self, STATE, _json, "GET"):
            return
        if attachment_route(self, STATE, _json, "GET"):
            return
        path = urlparse(self.path).path
        if path == "/api/stream":
            return self._stream()
        if path == "/api/workspace-file":
            return self._workspace_file(parse_qs(urlparse(self.path).query))
        if path in ("/presentation.js", "/live.js", "/presentation.css"):
            return self._file(path[1:], "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8")
        if path in ("/vendor/marked.js", "/vendor/purify.js", "/vendor/highlight.js", "/vendor/highlight.css"):
            return self._file(path[1:], "text/css; charset=utf-8" if path.endswith(".css") else "text/javascript; charset=utf-8")
        if path in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._file("app.js", "text/javascript; charset=utf-8")
        if path == "/tasks.js":
            return self._file("tasks.js", "text/javascript; charset=utf-8")
        if path == "/navigation.js":
            return self._file("navigation.js", "text/javascript; charset=utf-8")
        if path == "/skills.js":
            return self._file("skills.js", "text/javascript; charset=utf-8")
        if path == "/api/skills":
            try:
                return _json(self, 200, skills_api.manager(STATE).list())
            except ValueError as error:
                return _json(self, 400, {"error": str(error)})
            except OSError as error:
                return _json(self, 500, {"error": str(error)})
        if path == "/api/instructions":
            status, result = instructions_api.route("GET", path, None, STATE)
            return _json(self, status, result)
        if path == "/ling.js":
            return self._file("ling.js", "text/javascript; charset=utf-8")
        if path == "/mcp.js":
            return self._file("mcp.js", "text/javascript; charset=utf-8")
        if path == "/api/mcp":
            return _json(self, 200, mcp_api.snapshot(STATE))
        if path == "/app.css":
            return self._file("app.css", "text/css; charset=utf-8")
        if path == "/api/state":
            try:
                return _json(self, 200, self._state())
            except (OSError, ValueError) as error:
                return _json(self, 500, {"error": str(error)})
        if path == "/api/ling":
            try:
                return _json(self, 200, _ling().projection(_transcript))
            except (OSError, ValueError) as error:
                return _task_error(self, error)
        if path == "/api/tasks":
            try:
                return _task_http().list(self, parse_qs(urlparse(self.path).query))
            except (OSError, ValueError) as error:
                return _task_error(self, error)
        if path.startswith("/api/tasks/") and path.endswith("/events"):
            task_id = path.split("/")[3]
            try:
                return _task_http().events(self, task_id, parse_qs(urlparse(self.path).query))
            except (OSError, ValueError) as error:
                return _task_error(self, error)
        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            try:
                return _task_http().detail(self, task_id)
            except (OSError, ValueError) as error:
                return _task_error(self, error)
        if path.startswith("/api/sessions/"):
            return self._session(path.rsplit("/", 1)[-1])
        return _json(self, 404, {"error": "not found"})

    def do_DELETE(self) -> None:
        if plugin_api.route(self, STATE, _json, "DELETE"):
            return
        path = urlparse(self.path).path
        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            try:
                return _task_http().delete(self, task_id, _read_json(self))
            except (OSError, ValueError) as error:
                return _task_error(self, error)
        if path.startswith("/api/sessions/"):
            session_id = path.rsplit("/", 1)[-1]
            try:
                removed = _application().delete_session(session_id)
            except TaskConflict as error:
                return _json(self, 409, {"error": str(error)})
            return _json(self, 200 if removed else 404, {"removed": removed})
        if path.startswith("/api/providers/"):
            provider_id = path.rsplit("/", 1)[-1]
            try:
                STATE["store"].remove_provider(provider_id)
            except ValueError as error:
                return _json(self, 400, {"error": str(error)})
            return _json(self, 200, self._state())
        return _json(self, 404, {"error": "not found"})

    def do_PUT(self) -> None:
        if plugin_api.route(self, STATE, _json, "PUT"):
            return
        path = urlparse(self.path).path
        body = _read_json(self)
        if path == "/api/instructions":
            try:
                status, result = instructions_api.route("PUT", path, body, STATE)
                return _json(self, status, result)
            except (ValueError, OSError) as error:
                return _task_error(self, error)
        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            try:
                return _task_http().update(self, task_id, body)
            except (OSError, ValueError) as error:
                return _task_error(self, error)
        if path.startswith("/api/providers/"):
            provider_id = path.rsplit("/", 1)[-1]
            try:
                STATE["store"].update_provider(provider_id, body)
            except ValueError as error:
                return _json(self, 400, {"error": str(error)})
            return _json(self, 200, self._state())
        return _json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        if plugin_api.route(self, STATE, _json, "POST"):
            return
        if attachment_route(self, STATE, _json, "POST"):
            return
        path = urlparse(self.path).path
        body = _read_json(self)
        try:
            if path == "/api/skills":
                return _json(self, 200, skills_api.manager(STATE).save(body))
            if path == "/api/skills/delete":
                return _json(self, 200, skills_api.manager(STATE).delete(body))
            if path == "/api/mcp/servers":
                return _json(self, 200, mcp_api.save(STATE, body))
            if path.startswith("/api/mcp/servers/"):
                pieces = path.split("/")
                if len(pieces) != 6:
                    raise ValueError("未知 MCP 接口")
                return _json(self, 200, mcp_api.command(STATE, unquote(pieces[4]), pieces[5]))
            if path == "/api/ling/input":
                return _json(self, 202, _application().submit_input(body))
            if path == "/api/ling/resume":
                return _json(self, 200, _ling().resume())
            if path == "/api/ling/stop":
                return self._abort()
            if path == "/api/ling/pause-all":
                return _json(self, 200, {"tasks": [task.to_dict() for task in STATE["task_controller"].pause_all()]})
            if path.startswith("/api/tasks/") and path.endswith("/commands"):
                return _task_http().command(self, path.split("/")[3], body)
            if path.startswith("/api/tasks/") and path.endswith("/lifecycle"):
                return _task_http().lifecycle(self, path.split("/")[3], body)
            if path.startswith("/api/tasks/") and path.endswith("/messages"):
                return _task_http().messages(self, path.split("/")[3], body)
            if path.startswith("/api/tasks/") and "/interactions/" in path and path.endswith("/answer"):
                pieces = path.split("/")
                return _task_http().answer(self, pieces[3], pieces[5], body)
            if path == "/api/approval":
                return self._approve(body)
            if path == "/api/answer":
                return self._answer(body)
            if path == "/api/providers":
                return self._add_provider(body)
            if path == "/api/select":
                return self._select(body)
            if path == "/api/settings":
                return self._settings(body)
            if path == "/api/workspace":
                return self._workspace(body)
        except (ValueError, MCPError) as error:
            return _task_error(self, error)
        except OSError as error:
            return _json(self, 500, {"error": str(error)})
        return _json(self, 404, {"error": "not found"})

    # --- state -----------------------------------------------------------

    def _stream(self) -> None:
        from ..runtime.stream import runtime_updates

        feed = runtime_updates(STATE)
        subscriber = feed.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while not STATE.get("closing"):
                try:
                    event = subscriber.get(timeout=15)
                    if event is None:
                        break
                    body = "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    body = ": heartbeat\n\n"
                self.wfile.write(body.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            feed.unsubscribe(subscriber)

    def _workspace_file(self, query) -> None:
        from types import SimpleNamespace
        from ..tools.files import _read_text, resolve

        configured = Path(STATE["store"].load().workspace).resolve()
        root = Path(query.get("workspace", [str(configured)])[-1]).resolve()
        roots = {configured}
        if STATE.get("tasks"):
            roots.update(Path(task.workspace).resolve() for task in STATE["tasks"].list(include_archived=True))
        if root not in roots:
            return _json(self, 403, {"error": "该工作目录不属于当前交流或已有工作"})
        try:
            path = resolve(SimpleNamespace(root=root), query.get("path", [""])[-1])
            if not path.is_file():
                raise FileNotFoundError(str(path))
            import mimetypes
            mime = mimetypes.guess_type(path.name)[0]
            if mime in ALLOWED_IMAGE_MIMES:
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                return self.wfile.write(body)
            line = max(1, int(query.get("line", ["1"])[-1]))
            result = _read_text(path, offset=max(1, line - 20), limit=4000)
            return _json(self, 200, {"path": str(path), "text": result["content"],
                                      "truncated": result.get("truncated", False)})
        except PermissionError as error:
            return _json(self, 403, {"error": str(error)})
        except (OSError, ValueError) as error:
            return _json(self, 404, {"error": str(error)})

    def _file(self, name: str, content_type: str) -> None:
        target = Path(__file__).resolve().parent / name
        try:
            body = target.read_bytes()
        except OSError:
            return _json(self, 404, {"error": "asset missing"})
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _state(self) -> dict[str, Any]:
        store: ConfigStore = STATE["store"]
        settings = store.load()
        providers = [
            {
                "id": provider.id,
                "name": provider.name,
                "family": provider.family,
                "base_url": provider.base_url,
                "has_key": store.has_key(provider.id),
                "models": provider.models,
            }
            for provider in store.providers()
        ]
        family = settings.provider.family
        # An unconfigured or invalid endpoint must remain editable in Settings.
        try:
            profile = settings.profile
        except ValueError:
            profile = None
        return {
            "providers": providers,
            "current": {
                "provider": settings.provider.id,
                "model": settings.model.get("id", ""),
                "effort": settings.effort,
            },
            "effort": {
                "levels": list(reasoning.LEVELS),
                "labels": reasoning.LABELS,
                "current": settings.effort,
                "mapping": reasoning.describe(family, settings.effort or "", profile)
                if settings.effort
                else None,
            },
            "profile": json.loads(profile.model_dump_json()) if profile else None,
            "has_key": bool(settings.api_key),
            "permission": settings.permission,
            "max_steps": settings.max_steps,
            "context_window": settings.context_window,
            "context_limit": settings.context_limit,
            "workspace": settings.workspace,
            "sessions": [],
            "thinking_note": "思考内容仅用于展示，不回传给模型。",
        }

    def _session(self, session_id: str) -> None:
        session = STATE["sessions"].load(session_id)
        if session is None:
            return _json(self, 404, {"error": "session not found"})
        return _json(
            self,
            200,
            {
                "id": session.id,
                "title": session.title,
                "model": session.model,
                "created_at": session.created_at,
                "items": _transcript(session),
            },
        )

    # --- configuration ---------------------------------------------------

    def _add_provider(self, value: dict[str, Any]) -> None:
        provider = STATE["store"].add_provider(value)
        return _json(self, 201, {"provider": provider.to_dict(), **self._state()})

    def _select(self, value: dict[str, Any]) -> None:
        _application().select_model(value)
        return _json(self, 200, self._state())

    def _settings(self, value: dict[str, Any]) -> None:
        try:
            _application().save_settings(value)
        except TaskConflict as error:
            return _json(self, 409, {"error": str(error)})
        return _json(self, 200, self._state())

    def _workspace(self, value: dict[str, Any]) -> None:
        return self._settings({"workspace": value.get("workspace")})

    def _approve(self, value: dict[str, Any]) -> None:
        if value.get("reviewed_unknown") is True:
            return _json(self, 200, _ling().decisions.review_unknown(str(value.get("token") or "")))
        return _json(self, 200, _ling().answer(str(value.get("token") or ""), value.get("allowed")))

    def _answer(self, value: dict[str, Any]) -> None:
        return _json(self, 200, _ling().answer(str(value.get("token") or ""), value.get("answers", value.get("text"))))

    def _abort(self) -> None:
        return _json(self, 200, _ling().stop())

def serve(*, home: Path | None = None, port: int = 8766, workspace: Path | None = None) -> None:
    previous = STATE.get("application")
    if previous is not None:
        previous.close()
    STATE.clear()
    app = Application(STATE)
    app.start(home=home, workspace=workspace)
    server = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        server.daemon_threads = True
        actual = server.server_address[1]
        app.attach_http(f"http://127.0.0.1:{actual}")
        print(f"Nexus: http://127.0.0.1:{actual}")
        print(f"data:  {app.state['store'].home}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.close()
        if server is not None:
            server.server_close()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="nexus", description="Minimal coding agent")
    parser.add_argument("--home", default=os.environ.get("NEXUS_HOME"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NEXUS_PORT", "8766")))
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser() if args.home else None
    settings = ConfigStore(home).ensure()
    target = Path(args.workspace or settings.workspace)
    if not args.no_browser:
        import webbrowser

        def open_later() -> None:
            time.sleep(0.9)
            webbrowser.open(f"http://127.0.0.1:{args.port}/")

        threading.Thread(target=open_later, daemon=True).start()
    serve(home=home, port=args.port, workspace=target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
