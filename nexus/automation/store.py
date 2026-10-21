"""Nexus-owned wake queue and activity history; rules live in the MCP service."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

RUN_FIELDS = ("status", "reasons", "prompt", "notify", "kind", "summary", "reply",
              "error", "created_at", "finished_at", "read_at")

def now_local():
    return datetime.now().astimezone()

def _iso(value):
    return value.isoformat(timespec="seconds")

class RunStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._lock, self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
                reasons TEXT NOT NULL DEFAULT '[]', prompt TEXT NOT NULL DEFAULT '',
                notify INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'wake',
                summary TEXT, reply TEXT, error TEXT, created_at TEXT NOT NULL,
                finished_at TEXT, read_at TEXT)""")
            db.execute("UPDATE runs SET status='failed',finished_at=?,error='服务中断；保留已知记录' WHERE status='running'", (_iso(now_local()),))
            db.commit()

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "status": row["status"],
            "reasons": json.loads(row["reasons"] or "[]"),
            "prompt": row["prompt"], "notify": bool(row["notify"]),
            "kind": row["kind"], "summary": row["summary"], "reply": row["reply"],
            "error": row["error"], "created_at": row["created_at"],
            "finished_at": row["finished_at"], "read_at": row["read_at"],
        }

    def add_run(
        self, reasons: list[dict[str, Any]], prompt: str = "", notify: bool = False, kind: str = "wake"
    ) -> dict[str, Any]:
        reasons = list(reasons)
        with self._lock, self._db() as connection:
            if kind == "wake":
                pending = connection.execute(
                    "SELECT * FROM runs WHERE kind='wake' AND status='pending' "
                    "ORDER BY created_at LIMIT 1"
                ).fetchone()
                if pending is not None:
                    merged = list(json.loads(pending["reasons"]))
                    for reason in reasons:
                        if reason not in merged:
                            merged.append(reason)
                    connection.execute(
                        "UPDATE runs SET reasons=?,prompt=?,notify=? WHERE id=?",
                        (
                            json.dumps(merged), prompt or pending["prompt"],
                            int(bool(notify) or bool(pending["notify"])), pending["id"],
                        ),
                    )
                    connection.commit()
                    return self._run(connection.execute(
                        "SELECT * FROM runs WHERE id=?", (pending["id"],)
                    ).fetchone())
            run_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO runs(id,status,reasons,prompt,notify,kind,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    run_id, "pending", json.dumps(reasons), prompt, int(bool(notify)),
                    kind, _iso(now_local()),
                ),
            )
            connection.commit()
            return self._run(connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,)
            ).fetchone())

    def next_run(self) -> dict[str, Any] | None:
        with self._lock, self._db() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE status='pending' ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            return self._run(row) if row is not None else None

    def update_run(self, run_id: str, **changes: Any) -> dict[str, Any] | None:
        unknown = set(changes) - set(RUN_FIELDS)
        if unknown:
            raise ValueError(f"unknown run fields: {', '.join(sorted(unknown))}")
        with self._lock, self._db() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return None
            updates: dict[str, Any] = {}
            for key, value in changes.items():
                if key == "reasons":
                    value = json.dumps(list(value))
                elif key == "notify":
                    value = int(bool(value))
                updates[key] = value
            if updates:
                assignments = ",".join(f"{key}=?" for key in updates)
                connection.execute(
                    f"UPDATE runs SET {assignments} WHERE id=?",
                    (*updates.values(), run_id),
                )
                connection.commit()
            return self._run(connection.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,)
            ).fetchone())

    def runs(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._lock, self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC,id DESC LIMIT ?",
                (max(0, int(limit)),),
            )
            return [self._run(row) for row in rows]
