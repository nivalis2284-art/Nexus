"""HTTP + static files. Standard library only: no framework, no build step.

Routes
    GET  /                     the single page
    GET  /app.js, /app.css     assets
    GET  /api/state            providers, models, selection, sessions
    POST /api/sessions         create
    GET  /api/sessions/<id>    transcript
    DEL  /api/sessions/<id>    delete
    POST /api/chat             run a turn (NDJSON stream)
    POST /api/approval         answer a pending approval
    POST /api/providers        add a provider
    PUT  /api/providers/<id>   edit a provider
    DEL  /api/providers/<id>   remove a provider
    POST /api/select           choose provider + model + reasoning level
    POST /api/settings         preferences (permission, max steps)
    POST /api/workspace        change the workspace directory

The chat endpoint streams newline-delimited JSON. NDJSON over a plain POST keeps
the client trivial: no SSE parsing, no reconnection logic, no EventSource quirks.

Persistence happens once per turn, after the loop finishes.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse, urlsplit

from .. import mcp, skills
from ..attachments import AttachmentStore, message_text
from ..core import effort as reasoning
from ..core import subagent
from ..core.agent import Agent, ApprovalRequest
from ..core.config import ConfigStore, build_profile
from ..core.permission import default_policy
from ..core.workspace import inspect
from ..model import RuntimeCredentials
from ..runtime import TaskController
from ..runtime.assembly import TaskAssembler
from ..runtime.scheduler import TaskScheduler
from ..task import TaskConflict, TaskNotFound, TaskStore, TaskValidationError
from ..session import Message, SessionStore
from ..skills import SkillSet
from ..tools import build, mcp_tools
from ..tools._jobs import JobRegistry
from .task_api import TaskHTTP
from .attachments import route as attachment_route
from ..ling import LingInbox, WorkService, ling_session_id

APPROVAL_TIMEOUT = 600.0

# Upload limits. Base64 inflates by ~33%, so 5MB becomes ~6.7MB of JSON; eight
# of those is already a large request, which is why the count is capped too.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES_PER_TURN = 8
MAX_REQUEST_BYTES = MAX_IMAGES_PER_TURN * ((MAX_IMAGE_BYTES + 2) // 3 * 4) + 4_000_000
ALLOWED_IMAGE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif"}
)


def _read_images(raw: Any) -> list[dict[str, str]]:
    """Validate image blocks from a chat request.

    Rejects anything that is not a supported image or is too large, rather than
    forwarding it to a provider that would reject it more confusingly.
    """
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ValueError("images must be a list")
    if len(raw) > MAX_IMAGES_PER_TURN:
        raise ValueError(f"too many images (max {MAX_IMAGES_PER_TURN})")
    out: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"image {index} is not an object")
        mime = str(item.get("mime") or "").strip().lower()
        data = item.get("data")
        if mime not in ALLOWED_IMAGE_MIMES:
            raise ValueError(f"image {index} has unsupported type '{mime}'")
        if not isinstance(data, str) or not data:
            raise ValueError(f"image {index} has no data")
        # Rough decoded size from the base64 length.
        approx = len(data) * 3 // 4
        if approx > MAX_IMAGE_BYTES:
            raise ValueError(f"image {index} is larger than 5MB")
        out.append({"mime": mime, "data": data})
    return out


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


class Approvals:
    """One pending human interaction at a time, keyed by a random token.

    Handles two shapes with one mechanism: a yes/no approval, and a free-text
    answer to a question. Both mean "the turn is blocked until a human replies".

    The wait is bound to the owning run's lifetime, the way DSH's
    ``TimedQuestionWait`` binds to its parent turn. Without that binding, closing
    a browser tab mid-question leaves the loop parked inside ``Event.wait`` for
    the full timeout and the run lock unreleased, so every new message 409s.
    ``cancel_all`` is the one place that releases waiters, and the chat handler
    calls it on disconnect, on stop, and in its ``finally``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}
        # Which shape each token carries, so a cancel can answer in kind:
        # a declined approval, and an absent answer for a question.
        self._kinds: dict[str, str] = {}
        self._answers: dict[str, Any] = {}
        self._cancelled = False

    def create(self, kind: str = "approval") -> str:
        token = secrets.token_urlsafe(12)
        event = threading.Event()
        with self._lock:
            # A cancel that arrives while nobody is waiting must still apply to
            # the next waiter, or a stop during a fast tool call would be lost.
            if self._cancelled:
                self._cancelled = False
            self._pending[token] = event
            self._kinds[token] = kind
            self._answers.pop(token, None)
        return token

    def wait(self, token: str, timeout: float = APPROVAL_TIMEOUT) -> bool:
        event = self._pending.get(token)
        if event is None:
            return False
        event.wait(timeout)
        with self._lock:
            return bool(self._answers.get(token, False))

    def wait_answer(self, token: str, timeout: float = APPROVAL_TIMEOUT) -> Any:
        """Wait for an answer. ``None`` means dismissed, cancelled or timed out.

        The value is either a string (one question) or a list of strings (many),
        so the caller gets back exactly what the client submitted.
        """
        event = self._pending.get(token)
        if event is None:
            return None
        event.wait(timeout)
        with self._lock:
            value = self._answers.get(token)
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return [str(item) for item in value]
        return str(value)

    def resolve(self, token: str, allowed: bool) -> bool:
        with self._lock:
            event = self._pending.get(token)
            if event is None:
                return False
            self._answers[token] = bool(allowed)
        event.set()
        return True

    def cancel_all(self) -> int:
        """Release every waiter without granting anything.

        Used when the turn is stopped, the client disconnects, or the handler is
        finishing for any reason. Returns how many waiters were woken.
        """
        with self._lock:
            waiting = list(self._pending.values())
            self._cancelled = True
            for token, kind in self._kinds.items():
                # Answer in the shape the waiter expects: a declined approval,
                # and "no answer at all" for a question.
                if kind == "question":
                    self._answers.setdefault(token, None)
                else:
                    self._answers.setdefault(token, False)
        for event in waiting:
            event.set()
        return len(waiting)

    def resolve_text(self, token: str, text: Any) -> bool:
        """Store an answer. ``text`` may be a string or a list of strings."""
        if isinstance(text, list):
            value: Any = [str(item) for item in text]
        else:
            value = str(text)
        with self._lock:
            event = self._pending.get(token)
            if event is None:
                return False
            self._answers[token] = value
        event.set()
        return True

    def forget(self, token: str) -> None:
        with self._lock:
            self._pending.pop(token, None)
            self._kinds.pop(token, None)
            self._answers.pop(token, None)


APPROVALS = Approvals()
STATE: dict[str, Any] = {}
BUSY = threading.Lock()
RESOURCES = threading.Lock()
# The agent of the run in progress, so another request can ask it to stop.
RUNNING: dict[str, Any] = {"agent": None}


def _task_http() -> TaskHTTP:
    return TaskHTTP(STATE, _json)


def _ling() -> LingInbox:
    with RESOURCES:
        if STATE.get("closing"):
            raise TaskConflict("服务正在关闭")
        if "tasks" not in STATE:
            raise TaskValidationError("task service is not initialized")
        if STATE.get("ling") is None:
            def run(session, emit, inbox):
                handler = object.__new__(Handler)
                try:
                    handler._run_turn(session, STATE["store"].load(), emit, ling=inbox)
                finally:
                    if RUNNING.get("session_id") == session.id:
                        APPROVALS.cancel_all()
                        RUNNING["agent"] = None
                        RUNNING.pop("session_id", None)
                        RUNNING.pop("cancel_event", None)
            STATE["ling"] = LingInbox(STATE, run, BUSY)
        return STATE["ling"]


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
                    "text": item.text,
                    "thinking": item.data.get("thinking") or "",
                    "usage": item.data.get("usage") or None,
                    "elapsed": item.data.get("elapsed"),
                    "at": item.at,
                    "images": item.data.get("images") or [],
                    "attachments": item.data.get("attachments") or [],
                }
            )
        elif item.kind == "tool":
            out.append(
                {
                    "role": "tool",
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


def _internal(session) -> list[dict[str, Any]]:
    """Rebuild the provider-neutral history the agent consumes.

    Thinking is deliberately excluded: it is display material and is never
    replayed (see core.thinking).
    """
    out: list[dict[str, Any]] = []
    for item in session.items:
        if item.kind == "user":
            images = item.data.get("images") or []
            out.append(
                {
                    "role": "user",
                    "content": _content_blocks(message_text(item.text, item.data.get("attachments") or []), images),
                }
            )
        elif item.kind == "assistant":
            calls = item.data.get("tool_calls") or []
            if item.text or calls:
                out.append({"role": "assistant", "content": item.text, "tool_calls": calls})
        elif item.kind == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": item.data.get("tool_call_id", ""),
                    "name": item.data.get("name", ""),
                    "content": item.text,
                }
            )
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
            if not _is_attachment_upload(path):
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
        if attachment_route(self, STATE, _json, "GET"):
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._file("app.js", "text/javascript; charset=utf-8")
        if path == "/tasks.js":
            return self._file("tasks.js", "text/javascript; charset=utf-8")
        if path == "/ling.js":
            return self._file("ling.js", "text/javascript; charset=utf-8")
        if path == "/app.css":
            return self._file("app.css", "text/css; charset=utf-8")
        if path == "/api/state":
            try:
                return _json(self, 200, self._state())
            except (OSError, ValueError) as error:
                return _json(self, 500, {"error": str(error)})
        if path in ("/api/ling", "/api/notifications"):
            try:
                if path == "/api/ling":
                    return _json(self, 200, _ling().projection(_transcript))
                query = parse_qs(urlparse(self.path).query)
                after = max(0, int(query.get("after", ["0"])[-1]))
                limit = min(1000, max(1, int(query.get("limit", ["100"])[-1])))
                values = STATE["tasks"].notifications(
                    after=after, limit=limit,
                    unread_only=query.get("unread", ["0"])[-1] in ("1", "true"))
                return _json(self, 200, {"notifications": values,
                    "next": values[-1]["id"] if values else after,
                    "unread_count": STATE["tasks"].notification_count()})
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
        path = urlparse(self.path).path
        if path.startswith("/api/tasks/") and path.endswith("/schedule"):
            task_id = path.split("/")[3]
            try:
                return _task_http().clear_schedule(self, task_id)
            except (OSError, ValueError) as error:
                return _task_error(self, error)
        if path.startswith("/api/tasks/"):
            task_id = path.rsplit("/", 1)[-1]
            try:
                return _task_http().delete(self, task_id, _read_json(self))
            except (OSError, ValueError) as error:
                return _task_error(self, error)
        if path.startswith("/api/sessions/"):
            session_id = path.rsplit("/", 1)[-1]
            if session_id == ling_session_id(STATE.get("tasks")):
                return _json(self, 409, {"error": "灵的持续记录由统一入口管理，不能作为普通会话删除。"})
            if RUNNING.get("session_id") == session_id:
                return _json(self, 409, {"error": "请先停止当前会话，再删除。"})
            removed = STATE["sessions"].delete(session_id)
            if removed:
                AttachmentStore(STATE["store"].home).delete_owner("sessions", session_id)
                jobs = STATE.get("jobs", {}).pop(session_id, None)
                if jobs is not None:
                    jobs.close_all()
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
        path = urlparse(self.path).path
        body = _read_json(self)
        if path.startswith("/api/tasks/") and path.endswith("/schedule"):
            task_id = path.split("/")[3]
            try:
                return _task_http().schedule(self, task_id, body)
            except (OSError, ValueError) as error:
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
        if attachment_route(self, STATE, _json, "POST"):
            return
        path = urlparse(self.path).path
        body = _read_json(self)
        try:
            if path == "/api/ling/input":
                inbox = _ling()
                text = str(body.get("text") or "").strip()
                images = _read_images(body.get("images"))
                settings = STATE["store"].load()
                if not settings.api_key or not settings.provider.base_url:
                    raise TaskValidationError("请先配置模型的 Base URL 和 API Key")
                build_profile(settings.provider, settings.model)
                if images and not settings.model.get("capabilities", {}).get("images"):
                    raise TaskValidationError("当前模型没有声明图片能力")
                attachments = AttachmentStore(STATE["store"].home).resolve(
                    "sessions", inbox.session_id, body.get("attachments", []))
                if len(images) + len(attachments) > 8:
                    raise TaskValidationError("每次最多 8 个附件")
                return _json(self, 202, inbox.enqueue(text, images, attachments,
                    str(body.get("request_id") or "")))
            if path == "/api/ling/resume":
                return _json(self, 200, _ling().resume())
            if path == "/api/ling/stop":
                inbox = _ling()
                if RUNNING.get("session_id") == inbox.session_id:
                    return self._abort()
                return _json(self, 200, {"stopped": False})
            if path.startswith("/api/notifications/") and path.endswith("/read"):
                value = STATE["tasks"].ack_notification(int(path.split("/")[3]))
                return _json(self, 200, value or {"read": True})
            if path == "/api/tasks":
                return _task_http().create(self, body)
            if path == "/api/task-control":
                return _task_http().task_control(self, body)
            if path.startswith("/api/tasks/") and path.endswith("/commands"):
                return _task_http().command(self, path.split("/")[3], body)
            if path.startswith("/api/tasks/") and path.endswith("/lifecycle"):
                return _task_http().lifecycle(self, path.split("/")[3], body)
            if path.startswith("/api/tasks/") and path.endswith("/messages"):
                return _task_http().messages(self, path.split("/")[3], body)
            if path.startswith("/api/tasks/") and "/interactions/" in path and path.endswith("/answer"):
                pieces = path.split("/")
                return _task_http().answer(self, pieces[3], pieces[5], body)
            if path.startswith("/api/tasks/") and "/interactions/" in path and path.endswith("/confirm"):
                pieces = path.split("/")
                return _task_http().start_confirmation(self, pieces[3], pieces[5], body)
            if path == "/api/sessions":
                return self._create()
            if path.startswith("/api/sessions/") and path.endswith("/rename"):
                return self._rename(path.split("/")[-2], body)
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
            if path == "/api/chat":
                return self._chat(body)
            if path == "/api/chat/abort":
                return self._abort()
        except ValueError as error:
            return _task_error(self, error)
        except OSError as error:
            return _json(self, 500, {"error": str(error)})
        return _json(self, 404, {"error": "not found"})

    # --- state -----------------------------------------------------------

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
        ling_id = ling_session_id(STATE.get("tasks"))
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
            "workspace": settings.workspace,
            "sessions": [item for item in STATE["sessions"].list()
                         if item["id"] != ling_id],
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

    def _create(self) -> None:
        settings = STATE["store"].load()
        session = STATE["sessions"].create(settings.model.get("id", ""))
        return _json(self, 201, {"id": session.id, "title": session.title})

    def _rename(self, session_id: str, value: dict[str, Any]) -> None:
        title = value.get("title")
        if not isinstance(title, str):
            raise ValueError("title is required")
        renamed = STATE["sessions"].rename(session_id, title)
        if not renamed:
            return _json(self, 404, {"error": "session not found"})
        return _json(self, 200, {"id": session_id, "renamed": True})

    # --- configuration ---------------------------------------------------

    def _add_provider(self, value: dict[str, Any]) -> None:
        provider = STATE["store"].add_provider(value)
        return _json(self, 201, {"provider": provider.to_dict(), **self._state()})

    def _select(self, value: dict[str, Any]) -> None:
        store: ConfigStore = STATE["store"]
        provider_id = str(value.get("provider") or "")
        model_id = str(value.get("model") or "")
        if not provider_id or not model_id:
            raise ValueError("provider and model are required")
        level = value.get("effort")
        if level is not None:
            # "off" / "" / "default" collapse to None = provider default.
            level = reasoning.normalize(level)
        store.select(provider_id, model_id, level)
        return _json(self, 200, self._state())

    def _settings(self, value: dict[str, Any]) -> None:
        store: ConfigStore = STATE["store"]
        store.save_preferences(
            permission=value.get("permission"),
            max_steps=value.get("max_steps"),
        )
        return _json(self, 200, self._state())

    def _workspace(self, value: dict[str, Any]) -> None:
        raw = value.get("workspace")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("workspace path is required")
        target = Path(raw.strip()).expanduser()
        if not target.is_dir():
            raise ValueError(f"not a directory: {target}")
        if not BUSY.acquire(blocking=False):
            return _json(self, 409, {"error": "请等当前任务停止后再切换工作目录。"})
        try:
            workspace = target.resolve()
            store = STATE["store"]
            live = _start_mcp(store, workspace)
            previous = STATE.get("mcp")
            STATE["mcp"] = live
            if previous is not None:
                previous.close()
            store.save_preferences(workspace=str(workspace))
            STATE["skills"] = _load_skills(store, workspace)
        finally:
            BUSY.release()
        return _json(self, 200, self._state())

    def _approve(self, value: dict[str, Any]) -> None:
        token = str(value.get("token") or "")
        ok = APPROVALS.resolve(token, bool(value.get("allowed")))
        return _json(self, 200 if ok else 404, {"resolved": ok})

    def _answer(self, value: dict[str, Any]) -> None:
        token = str(value.get("token") or "")
        answers = value.get("answers")
        if answers is None:
            answers = value.get("text")
        if isinstance(answers, str):
            payload: Any = answers
        elif isinstance(answers, list) and all(isinstance(item, str) for item in answers):
            payload = answers
        else:
            return _json(self, 400, {"error": "answers must be a string or a list of strings"})
        ok = APPROVALS.resolve_text(token, payload)
        return _json(self, 200 if ok else 404, {"resolved": ok})

    def _abort(self) -> None:
        """Ask the run in progress to stop.

        The loop checks the flag before each model request and before each tool
        run, so this is a request, not an immediate kill: a file write already
        started is allowed to finish.
        """
        agent = RUNNING.get("agent")
        cancellation = RUNNING.get("cancel_event")
        if agent is None and cancellation is None:
            return _json(self, 200, {"stopped": False, "reason": "idle"})
        if agent is not None:
            agent.stop()
        if cancellation is not None:
            cancellation.set()
        # Waking the waiters matters as much as the flag: if the loop is parked
        # in an approval or question, the flag alone would not be seen until the
        # timeout, and the run lock would stay held the whole time.
        woken = APPROVALS.cancel_all()
        return _json(self, 200, {"stopped": True, "unblocked": woken})

    # --- chat ------------------------------------------------------------

    def _chat(self, value: dict[str, Any]) -> None:
        session_id = str(value.get("session_id") or "")
        text = str(value.get("text") or "").strip()
        images = _read_images(value.get("images"))
        if not text and not images and not value.get("attachments"):
            raise ValueError("message is empty")
        session = STATE["sessions"].load(session_id)
        if session is None:
            raise ValueError("session not found")
        attachments = AttachmentStore(STATE["store"].home).resolve("sessions", session_id, value.get("attachments", []))
        if len(images) + len(attachments) > 8:
            raise ValueError("每次最多 8 个附件")
        settings = STATE["store"].load()
        if not settings.provider.base_url:
            return _json(
                self, 400,
                {"error": f"还没配置 {settings.provider.name} 的 Base URL，请在设置里填写。"},
            )
        if not settings.api_key:
            return _json(
                self, 400,
                {"error": f"还没配置 {settings.provider.name} 的 API Key。"},
            )
        if images and not settings.model.get("capabilities", {}).get("images"):
            return _json(
                self, 400,
                {"error": "当前模型没有声明图片能力，请先在设置里启用。"},
            )
        # Validate before opening the stream or saving a user message.
        build_profile(settings.provider, settings.model)
        if not BUSY.acquire(blocking=False):
            return _json(self, 409, {"error": "上一条还在运行。"})

        try:
            if STATE.get("closing"):
                return _json(self, 503, {"error": "服务正在关闭。"})
            store: SessionStore = STATE["sessions"]
            store.append(session, Message(kind="user", text=text, data={"images": images, "attachments": attachments}))
            if not session.title:
                session.title = (text or (attachments[0]["name"] if attachments else "图片"))[:40]
                store.rename(session.id, session.title, only_if_empty=True)

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            # Tools run in a worker thread (asyncio.to_thread), and some of them
            # emit — ask_user sends a question from there. The event loop thread
            # may be writing the same socket at the same moment, and two
            # interleaved chunked writes corrupt the stream. One lock per turn.
            write_lock = threading.Lock()

            def emit(payload: dict[str, Any]) -> None:
                data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                chunk = f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n"
                with write_lock:
                    self.wfile.write(chunk)
                    self.wfile.flush()

            try:
                self._run_turn(session, settings, emit)
            finally:
                try:
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                except OSError:
                    pass
        finally:
            BUSY.release()

    def _run_turn(self, session, settings, emit, *, ling: LingInbox | None = None) -> None:
        workspace = inspect(Path(settings.workspace))
        tool_config = {
            "webfetch_hosts": settings.webfetch_hosts,
            "search_endpoint": settings.search_endpoint,
        }
        # Resolve project skills for the actual workspace on every turn.
        skill_set = _load_skills(STATE["store"], workspace.root)
        STATE["skills"] = skill_set
        with RESOURCES:
            if STATE.get("closing"):
                emit({"type": "error", "message": "服务正在关闭。"})
                return
            session_jobs = STATE.setdefault("jobs", {}).setdefault(session.id, JobRegistry())
            cancel_event = threading.Event()
            RUNNING["cancel_event"] = cancel_event
            RUNNING["session_id"] = session.id

        def work_input(task_id, body):
            results = []
            TaskHTTP(STATE, lambda handler, status, value: results.append(value)).messages(None, task_id, body)
            return results[-1]

        work_service = WorkService(STATE, session, settings, ling.current_input, intake=work_input) if ling else None

        def toolset(*, supervise=True):
            return build(
                skill_set.bodies(), root=workspace.root, config=tool_config,
                mcp=STATE.get("mcp"), jobs=session_jobs, cancel_event=cancel_event,
                attachment_source=(AttachmentStore(STATE["store"].home), "sessions", session.id),
                attachment_ids={a["id"] for item in session.items for a in item.data.get("attachments", [])},
                work_service=work_service if supervise else None,
            )

        registry, context = toolset()

        def ask_owner(questions: list[dict[str, Any]]) -> list[str] | None:
            """Block on the same human channel that approvals use.

            ``questions`` is a list of ``{question, options}``. All of them are
            sent in one event and answered in one submit.
            """
            if cancel_event.is_set():
                return None
            token = APPROVALS.create("question")
            if cancel_event.is_set():
                APPROVALS.forget(token)
                return None
            try:
                emit({"type": "question", "token": token, "questions": questions})
            except OSError:
                # Nobody is listening any more; do not park for ten minutes.
                APPROVALS.forget(token)
                return None
            try:
                answer = APPROVALS.wait_answer(token)
            finally:
                APPROVALS.forget(token)
            if answer is None:
                return None
            # A single string from an older client becomes a one-element list.
            if isinstance(answer, str):
                return [answer]
            return answer

        context.ask = ask_owner
        policy = default_policy(settings.permission, settings.permission_rules)
        history = _internal(session)
        started = time.monotonic()
        profile = build_profile(settings.provider, settings.model)

        async def approve(request: ApprovalRequest) -> bool:
            if cancel_event.is_set():
                return False
            token = APPROVALS.create()
            if cancel_event.is_set():
                APPROVALS.forget(token)
                return False
            try:
                emit(
                    {
                        "type": "approval",
                        "token": token,
                        "tool": request.tool,
                        "value": request.value,
                        "arguments": request.arguments,
                    }
                )
            except OSError:
                # The stream is gone; refusing is the safe answer and, more
                # importantly, returns immediately instead of parking.
                APPROVALS.forget(token)
                return False
            try:
                allowed = await asyncio.to_thread(APPROVALS.wait, token)
            finally:
                APPROVALS.forget(token)
            if allowed:
                policy.remember(request.tool, request.value)
            return allowed

        persisted_count = 0

        def checkpoint(message):
            nonlocal persisted_count
            self._persist(session, [message])
            persisted_count += 1
            if ling:
                # The persisted message now replaces its streaming preview.
                with ling._condition:
                    ling.live = {}

        def before_model(messages):
            if work_service:
                agent.interaction_context = work_service.context_text()

        agent = Agent(
            profile=profile,
            credentials=RuntimeCredentials(settings.api_key),
            registry=registry,
            context=context,
            workspace=workspace,
            policy=policy,
            max_steps=settings.max_steps,
            context_limit=settings.context_limit,
            approve=approve,
            effort=settings.effort,
            skills=skill_set.manifest(),
            cancel_event=cancel_event,
            interaction_context=work_service.context_text() if work_service else "",
            on_message=checkpoint if ling else None,
            before_model=before_model if ling else None,
        )

        # A subagent is a whole nested run. The tool handler is synchronous (it
        # runs in a worker thread), so the nested event loop is created here and
        # the result is handed back through a queue.
        def spawn_subagent(task: str, max_steps: int):
            request = subagent.SubagentRequest(task=task, max_steps=max_steps, depth=1)

            async def factory(prompt: str, budget: int):
                child_registry, child_context = toolset(supervise=False)
                child_context.ask = ask_owner
                child = Agent(
                    profile=profile,
                    credentials=RuntimeCredentials(settings.api_key),
                    registry=child_registry,
                    context=child_context,
                    workspace=workspace,
                    policy=policy,
                    max_steps=budget,
                    context_limit=settings.context_limit,
                    effort=settings.effort,
                    skills=skill_set.manifest(),
                    approve=approve,
                    cancel_event=cancel_event,
                )
                async for event in child.run([{"role": "user", "content": prompt}]):
                    yield event

            async def drive_child():
                return await subagent.run_subagent(request, make_runner=factory)

            try:
                return asyncio.run(drive_child())
            except Exception as error:  # noqa: BLE001 -- the parent must keep running
                return subagent.SubagentResult(
                    text=f"Subagent failed: {type(error).__name__}: {error}",
                    stopped="error",
                    depth=1,
                )

        context.spawn_subagent = spawn_subagent

        async def drive() -> None:
            async for event in agent.run(history):
                if event.type == "delta":
                    emit({"type": "delta", "text": event.text})
                elif event.type == "thinking":
                    emit({"type": "thinking", "text": event.text})
                elif event.type == "retrying":
                    emit({"type": "retrying", "data": event.data})
                elif event.type == "context":
                    emit({"type": "context", "data": event.data})
                elif event.type == "compacted":
                    emit({"type": "compacted", "data": event.data})
                elif event.type == "tool_start":
                    emit({"type": "tool_start", "tool": event.data})
                elif event.type == "tool_result":
                    emit({"type": "tool_result", "tool": event.data})
                elif event.type == "error":
                    emit({"type": "error", "message": event.text})
                elif event.type == "done":
                    usage = event.data.get("usage") or {}
                    elapsed = round(time.monotonic() - started, 3)
                    # Attach usage and elapsed to this turn's last assistant
                    # message so both survive a reload; the stream alone would
                    # forget them.
                    for message in reversed(agent.new_messages):
                        if message.get("role") == "assistant":
                            message["usage"] = usage
                            message["elapsed"] = elapsed
                            break
                    emit({"type": "done", "data": event.data | {"elapsed": elapsed}})

        RUNNING["agent"] = agent
        RUNNING["session_id"] = session.id
        try:
            asyncio.run(drive())
        except (BrokenPipeError, ConnectionResetError):
            # The browser went away mid-stream: stop the loop instead of
            # letting it keep calling the model into a dead socket. Wake any
            # pending approval too, or the thread waits out the full timeout.
            agent.stop()
            APPROVALS.cancel_all()
            return
        except Exception as error:  # noqa: BLE001 -- surface, never kill the server
            try:
                emit({"type": "error", "message": f"{type(error).__name__}: {error}"})
            except OSError:
                pass
        finally:
            APPROVALS.cancel_all()
            agent.close_pending_calls(history)
            try:
                self._persist(session, agent.new_messages[persisted_count:])
            finally:
                RUNNING["agent"] = None
                RUNNING.pop("session_id", None)
                RUNNING.pop("cancel_event", None)

    def _persist(self, session, new_messages: list[dict[str, Any]]) -> None:
        store: SessionStore = STATE["sessions"]
        for message in new_messages:
            role = message.get("role")
            if role == "assistant":
                calls = message.get("tool_calls") or []
                thinking = message.get("thinking") or ""
                if message.get("content") or calls or thinking:
                    data: dict[str, Any] = {}
                    if calls:
                        data["tool_calls"] = calls
                    if thinking:
                        data["thinking"] = thinking
                    usage = message.get("usage")
                    if usage:
                        data["usage"] = usage
                    elapsed = message.get("elapsed")
                    if isinstance(elapsed, (int, float)):
                        data["elapsed"] = elapsed
                    store.append(
                        session,
                        Message(
                            kind="assistant",
                            text=message.get("content") or "",
                            data=data,
                        ),
                    )
            elif role == "tool":
                store.append(
                    session,
                    Message(
                        kind="tool",
                        text=message.get("content") or "",
                        data={
                            "tool_call_id": message.get("tool_call_id", ""),
                            "name": message.get("name", ""),
                            "arguments": message.get("arguments"),
                            "error": message.get("error", ""),
                            "ok": message.get("ok", not str(message.get("content", "")).startswith(
                                ("The user declined", "Blocked by policy", "Unknown tool")
                            )),
                            "details": message.get("details"),
                        },
                    ),
                )


def _load_skills(store: ConfigStore, workspace: Path | None = None) -> SkillSet:
    """Project skills first, then user skills; the project wins on a clash."""
    return skills.discover(skills.default_roots(workspace, store.home))


def _start_mcp(store: ConfigStore, workspace: Path) -> mcp.MCPRegistry | None:
    """Connect configured MCP servers once, at startup.

    A server that fails to start is reported and skipped: one broken MCP entry
    must not stop the agent from running.
    """
    settings = store.load()
    entries = settings.mcp_servers
    if not entries:
        return None
    problems = mcp_tools.config_problems(entries)
    for problem in problems:
        print(f"mcp: {problem}")
    registry = mcp.MCPRegistry()
    for raw in entries:
        config = mcp.ServerConfig.from_dict(raw)
        if config is None or not config.enabled:
            continue
        try:
            names = registry.add(config, cwd=workspace)
        except mcp.MCPError as error:
            print(f"mcp: '{config.name}' failed to start: {error}")
            continue
        print(f"mcp: '{config.name}' provided {len(names)} tool(s)")
    if not registry.servers:
        return None
    return registry


def serve(*, home: Path | None = None, port: int = 8766, workspace: Path | None = None) -> None:
    previous_ling = STATE.get("ling")
    if previous_ling is not None:
        previous_ling.close()
    previous_scheduler = STATE.get("task_scheduler")
    if previous_scheduler is not None:
        previous_scheduler.close()
    previous_controller = STATE.get("task_controller")
    if previous_controller is not None:
        previous_controller.close()
    store = ConfigStore(home)
    store.ensure()
    if workspace is not None:
        store.save_preferences(workspace=str(Path(workspace).resolve()))
    STATE["store"] = store
    STATE["sessions"] = SessionStore(store.sessions_dir)
    STATE["jobs"] = {}
    STATE.pop("ling", None)
    STATE["closing"] = False
    STATE["skills"] = _load_skills(store, Path(store.load().workspace))
    STATE["mcp"] = _start_mcp(store, Path(store.load().workspace))
    task_store = TaskStore(store.home / "tasks.sqlite3")
    task_store.recover_interrupted()
    task_store.hold_queued_at_startup()
    task_runner = TaskAssembler(task_store, store)
    task_controller = TaskController(task_store, run=task_runner)
    task_controller.start()
    task_scheduler = TaskScheduler(task_store, task_controller)
    task_scheduler.start()
    STATE["tasks"] = task_store
    STATE["task_controller"] = task_controller
    STATE["task_scheduler"] = task_scheduler
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    actual = server.server_address[1]
    print(f"Nexus: http://127.0.0.1:{actual}")
    print(f"data:  {store.home}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler = STATE.get("task_scheduler")
        if scheduler is not None:
            scheduler.close()
        controller = STATE.get("task_controller")
        if controller is not None:
            controller.close()
        # Stop task-owned background processes only. Ordinary chat's historical
        # stop semantics and its own session job registries remain untouched.
        jobs = getattr(task_runner, "_jobs", {})
        for registry in list(jobs.values()):
            registry.close_all()
        with RESOURCES:
            STATE["closing"] = True
            session_jobs = list(STATE.pop("jobs", {}).values())
            cancellation = RUNNING.get("cancel_event")
            if cancellation is not None:
                cancellation.set()
        agent = RUNNING.get("agent")
        if agent is not None:
            agent.stop()
        APPROVALS.cancel_all()
        ling = STATE.get("ling")
        if ling is not None:
            ling.close()
        for jobs in session_jobs:
            jobs.close_all()
        server.server_close()
        live = STATE.get("mcp")
        if live is not None:
            live.close()


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
