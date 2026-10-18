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
from urllib.parse import urlparse

from ..core import effort as reasoning
from ..core import subagent
from ..core.agent import Agent, ApprovalRequest
from ..core.config import ConfigStore, build_profile
from ..core.permission import default_policy
from ..core.workspace import inspect
from ..model import RuntimeCredentials
from ..session import Message, SessionStore
from .. import mcp
from ..skills import SkillSet
from .. import skills
from ..tools import build

APPROVAL_TIMEOUT = 600.0

# Upload limits. Base64 inflates by ~33%, so 5MB becomes ~6.7MB of JSON; eight
# of those is already a large request, which is why the count is capped too.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES_PER_TURN = 8
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
# The agent of the run in progress, so another request can ask it to stop.
RUNNING: dict[str, Any] = {"agent": None}


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
    if length <= 0 or length > 4_000_000:
        return {}
    try:
        value = json.loads(handler.rfile.read(length))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


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
                    "content": _content_blocks(item.text, images) if images else item.text,
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

    # --- routing ---------------------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file("index.html", "text/html; charset=utf-8")
        if path == "/app.js":
            return self._file("app.js", "text/javascript; charset=utf-8")
        if path == "/app.css":
            return self._file("app.css", "text/css; charset=utf-8")
        if path == "/api/state":
            try:
                return _json(self, 200, self._state())
            except (OSError, ValueError) as error:
                return _json(self, 500, {"error": str(error)})
        if path.startswith("/api/sessions/"):
            return self._session(path.rsplit("/", 1)[-1])
        return _json(self, 404, {"error": "not found"})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/sessions/"):
            removed = STATE["sessions"].delete(path.rsplit("/", 1)[-1])
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
        if path.startswith("/api/providers/"):
            provider_id = path.rsplit("/", 1)[-1]
            try:
                STATE["store"].update_provider(provider_id, body)
            except ValueError as error:
                return _json(self, 400, {"error": str(error)})
            return _json(self, 200, self._state())
        return _json(self, 404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        body = _read_json(self)
        try:
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
            return _json(self, 400, {"error": str(error)})
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
                "mapping": reasoning.describe(family, settings.effort or "", settings.profile)
                if settings.effort
                else None,
            },
            "profile": json.loads(settings.profile.model_dump_json()),
            "has_key": bool(settings.api_key),
            "permission": settings.permission,
            "max_steps": settings.max_steps,
            "workspace": settings.workspace,
            "sessions": STATE["sessions"].list(),
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
        STATE["store"].save_preferences(workspace=str(target.resolve()))
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
        if agent is None:
            return _json(self, 200, {"stopped": False, "reason": "idle"})
        agent.stop()
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
        if not text and not images:
            raise ValueError("message is empty")
        session = STATE["sessions"].load(session_id)
        if session is None:
            raise ValueError("session not found")
        settings = STATE["store"].load()
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
        if not BUSY.acquire(blocking=False):
            return _json(self, 409, {"error": "上一条还在运行。"})

        try:
            store: SessionStore = STATE["sessions"]
            store.append(session, Message(kind="user", text=text, data={"images": images} if images else {}))
            if not session.title:
                session.title = (text or "图片")[:40]
                store.rewrite(session)

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

    def _run_turn(self, session, settings, emit) -> None:
        workspace = inspect(Path(settings.workspace))
        tool_config = {
            "webfetch_hosts": settings.webfetch_hosts,
            "search_endpoint": settings.search_endpoint,
        }
        skill_set: SkillSet = STATE.get("skills") or SkillSet()
        registry, context = build(skill_set.bodies(), config=tool_config)
        context.root = workspace.root

        def ask_owner(questions: list[dict[str, Any]]) -> list[str] | None:
            """Block on the same human channel that approvals use.

            ``questions`` is a list of ``{question, options}``. All of them are
            sent in one event and answered in one submit.
            """
            token = APPROVALS.create("question")
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
        before = len(history)
        started = time.monotonic()
        profile = build_profile(settings.provider, settings.model)

        async def approve(request: ApprovalRequest) -> bool:
            token = APPROVALS.create()
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
        )

        # A subagent is a whole nested run. The tool handler is synchronous (it
        # runs in a worker thread), so the nested event loop is created here and
        # the result is handed back through a queue.
        def spawn_subagent(task: str, max_steps: int):
            request = subagent.SubagentRequest(task=task, max_steps=max_steps, depth=1)

            async def factory(prompt: str, budget: int):
                child_registry, child_context = build(skill_set.bodies(), config=tool_config)
                child_context.root = workspace.root
                child_context.ask = ask_owner
                # MCP connections are process-wide; share them rather than
                # spawning a second copy of every server.
                child_context.mcp = context.mcp
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
                    for message in reversed(history):
                        if message.get("role") == "assistant":
                            message["usage"] = usage
                            message["elapsed"] = elapsed
                            break
                    emit({"type": "done", "data": event.data | {"elapsed": elapsed}})

        RUNNING["agent"] = agent
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
            RUNNING["agent"] = None
            self._persist(session, history[before:])

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
                            "ok": not str(message.get("content", "")).startswith(
                                ("The user declined", "Blocked by policy", "Unknown tool")
                            ),
                        },
                    ),
                )


def _load_skills(store: ConfigStore, workspace: Path | None = None) -> SkillSet:
    """Project skills first, then user skills; the project wins on a clash."""
    return skills.discover(skills.default_roots(workspace, store.home))


def _start_mcp(store: ConfigStore, workspace: Path) -> MCPRegistry | None:
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
    registry = MCPRegistry()
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
    store = ConfigStore(home)
    store.ensure()
    if workspace is not None:
        store.save_preferences(workspace=str(Path(workspace).resolve()))
    STATE["store"] = store
    STATE["sessions"] = SessionStore(store.sessions_dir)
    STATE["skills"] = _load_skills(store, Path(store.load().workspace))
    STATE["mcp"] = _start_mcp(store, Path(store.load().workspace))
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
