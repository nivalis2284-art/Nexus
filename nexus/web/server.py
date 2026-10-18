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
from ..core.agent import Agent, ApprovalRequest
from ..core.config import ConfigStore, build_profile
from ..core.permission import default_policy
from ..core.workspace import inspect
from ..model import RuntimeCredentials
from ..session import Message, SessionStore
from ..tools import build

APPROVAL_TIMEOUT = 600.0


class Approvals:
    """One pending approval at a time, keyed by a random token."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, threading.Event] = {}
        self._answers: dict[str, bool] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(12)
        event = threading.Event()
        with self._lock:
            self._pending[token] = event
            self._answers.pop(token, None)
        return token

    def wait(self, token: str, timeout: float = APPROVAL_TIMEOUT) -> bool:
        event = self._pending.get(token)
        if event is None:
            return False
        event.wait(timeout)
        with self._lock:
            return bool(self._answers.get(token, False))

    def resolve(self, token: str, allowed: bool) -> bool:
        with self._lock:
            event = self._pending.get(token)
            if event is None:
                return False
            self._answers[token] = bool(allowed)
        event.set()
        return True

    def forget(self, token: str) -> None:
        with self._lock:
            self._pending.pop(token, None)
            self._answers.pop(token, None)


APPROVALS = Approvals()
STATE: dict[str, Any] = {}
BUSY = threading.Lock()


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
            out.append({"role": "user", "content": item.text})
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
            if path == "/api/approval":
                return self._approve(body)
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

    # --- chat ------------------------------------------------------------

    def _chat(self, value: dict[str, Any]) -> None:
        session_id = str(value.get("session_id") or "")
        text = str(value.get("text") or "").strip()
        if not text:
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
        if not BUSY.acquire(blocking=False):
            return _json(self, 409, {"error": "上一条还在运行。"})

        try:
            store: SessionStore = STATE["sessions"]
            store.append(session, Message(kind="user", text=text))
            if not session.title:
                session.title = text[:40]
                store.rewrite(session)

            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def emit(payload: dict[str, Any]) -> None:
                data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
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
        registry, context = build(STATE.get("skills") or {})
        context.root = workspace.root
        policy = default_policy(settings.permission)
        history = _internal(session)
        before = len(history)
        started = time.monotonic()
        profile = build_profile(settings.provider, settings.model)

        async def approve(request: ApprovalRequest) -> bool:
            token = APPROVALS.create()
            emit(
                {
                    "type": "approval",
                    "token": token,
                    "tool": request.tool,
                    "value": request.value,
                    "arguments": request.arguments,
                }
            )
            allowed = await asyncio.to_thread(APPROVALS.wait, token)
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
        )

        async def drive() -> None:
            async for event in agent.run(history):
                if event.type == "delta":
                    emit({"type": "delta", "text": event.text})
                elif event.type == "thinking":
                    emit({"type": "thinking", "text": event.text})
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

        try:
            asyncio.run(drive())
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:  # noqa: BLE001 -- surface, never kill the server
            try:
                emit({"type": "error", "message": f"{type(error).__name__}: {error}"})
            except OSError:
                pass
        finally:
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


def _load_skills(store: ConfigStore) -> dict[str, str]:
    root = store.home / "skills"
    found: dict[str, str] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.glob("*.md")):
        try:
            found[path.stem] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return found


def serve(*, home: Path | None = None, port: int = 8766, workspace: Path | None = None) -> None:
    store = ConfigStore(home)
    store.ensure()
    if workspace is not None:
        store.save_preferences(workspace=str(Path(workspace).resolve()))
    STATE["store"] = store
    STATE["sessions"] = SessionStore(store.sessions_dir)
    STATE["skills"] = _load_skills(store)
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
