"""JSONL sessions: one file per conversation, one JSON object per line.

A session file is append-only during a run and rewritten only for edits
(delete/rewrite). That keeps the common path cheap and the failure mode simple:
a torn last line is dropped on load instead of losing the whole file.

Record kinds:
    meta      {"kind":"meta","id":..,"title":..,"created_at":..,"model":..}
    user      {"kind":"user","text":..,"at":..}
    assistant {"kind":"assistant","text":..,"at":..}
    tool      {"kind":"tool","id":..,"name":..,"arguments":{..},"result":{..},"at":..}
    notice    {"kind":"notice","text":..,"at":..}
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


def new_session_id() -> str:
    return "s" + uuid.uuid4().hex[:16]


@dataclass
class Message:
    kind: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    at: str = ""

    def to_record(self) -> dict[str, Any]:
        record = {"kind": self.kind, "text": self.text, "at": self.at or _now()}
        record.update(self.data)
        return record


@dataclass
class Session:
    id: str
    title: str = ""
    created_at: str = ""
    model: str = ""
    items: list[Message] = field(default_factory=list)

    @property
    def path(self) -> Path | None:
        return self._path

    _path: Path | None = None


class SessionStore:
    """Files in one directory; no index, no database."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        if not session_id or not session_id.replace("_", "").isalnum():
            raise ValueError("invalid session id")
        return self.directory / f"{session_id}.jsonl"

    # --- reading ---------------------------------------------------------

    def list(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in self.directory.glob("*.jsonl"):
            session = self.load(path.stem)
            if session is None:
                continue
            last = ""
            for item in reversed(session.items):
                if item.kind in ("user", "assistant") and item.text:
                    last = item.text
                    break
            out.append(
                {
                    "id": session.id,
                    "title": session.title or (last[:40] or "新会话"),
                    "created_at": session.created_at,
                    "model": session.model,
                    "messages": sum(1 for i in session.items if i.kind in ("user", "assistant")),
                    "updated_at": self._mtime(path),
                }
            )
        out.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        return out

    @staticmethod
    def _mtime(path: Path) -> str:
        try:
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(path.stat().st_mtime)) + "Z"
        except OSError:
            return ""

    def load(self, session_id: str) -> Session | None:
        try:
            path = self.path_for(session_id)
        except ValueError:
            return None
        if not path.exists():
            return None
        session = Session(id=session_id)
        session._path = path
        try:
            raw = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        for line in raw:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                # A torn trailing line is expected after a hard kill.
                continue
            if not isinstance(record, dict):
                continue
            kind = record.pop("kind", "")
            text = record.pop("text", "")
            at = record.pop("at", "")
            if kind == "meta":
                session.title = record.get("title") or session.title
                session.created_at = record.get("created_at") or session.created_at
                session.model = record.get("model") or session.model
                continue
            if not isinstance(text, str):
                text = ""
            session.items.append(Message(kind=str(kind), text=text, data=record, at=str(at)))
        return session

    # --- writing ---------------------------------------------------------

    def create(self, model: str, title: str = "") -> Session:
        session = Session(id=new_session_id(), title=title, created_at=_now(), model=model)
        session._path = self.path_for(session.id)
        self._append(
            session,
            {"kind": "meta", "id": session.id, "title": title,
             "created_at": session.created_at, "model": model},
        )
        return session

    def append(self, session: Session, message: Message) -> Message:
        if not message.at:
            message.at = _now()
        self._append(session, message.to_record())
        return message

    def _append(self, session: Session, record: dict[str, Any]) -> None:
        path = session.path
        if path is None:
            path = self.path_for(session.id)
            session._path = path
        line = json.dumps(record, ensure_ascii=False)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if record.get("kind") == "meta":
            session.title = record.get("title") or session.title
            session.created_at = record.get("created_at") or session.created_at
            session.model = record.get("model") or session.model
        else:
            kind = record.pop("kind")
            text = record.pop("text", "")
            at = record.pop("at", "")
            session.items.append(Message(kind=str(kind), text=str(text), data=record, at=str(at)))

    def rewrite(self, session: Session) -> None:
        """Replace the file with the in-memory items (used by edits/pruning)."""
        path = session.path or self.path_for(session.id)
        session._path = path
        lines = [
            json.dumps(
                {"kind": "meta", "id": session.id, "title": session.title,
                 "created_at": session.created_at, "model": session.model},
                ensure_ascii=False,
            )
        ]
        for item in session.items:
            record = {"kind": item.kind, "text": item.text, "at": item.at}
            record.update(item.data)
            lines.append(json.dumps(record, ensure_ascii=False))
        temporary = path.with_suffix(".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        temporary.replace(path)

    def delete(self, session_id: str) -> bool:
        try:
            path = self.path_for(session_id)
        except ValueError:
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False
