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
            db.execute("""CREATE TABLE IF NOT EXISTS feedback_state (
                id INTEGER PRIMARY KEY CHECK(id=1),
                notification_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL)""")
            if db.execute("SELECT 1 FROM feedback_state WHERE id=1").fetchone() is None:
                # A new installation adopts the existing notification head as
                # its baseline. It does not replay old task history, while a
                # later restart resumes from the saved delivery position.
                db.execute(
                    "INSERT INTO feedback_state(id,notification_id,updated_at) VALUES(1,?,?)",
                    (self._notification_head(), _iso(now_local())),
                )
            db.execute("UPDATE runs SET status='failed',finished_at=?,error='服务中断；保留已知记录' WHERE status='running'", (_iso(now_local()),))
            db.commit()

    def _notification_head(self) -> int:
        """Read the task feed head without creating or upgrading that database."""
        path = self.path.parent / "tasks.sqlite3"
        if not path.exists():
            return 0
        connection = sqlite3.connect(path, timeout=5)
        try:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notifications'"
            ).fetchone() is None:
                return 0
            return int(connection.execute(
                "SELECT COALESCE(MAX(id),0) FROM notifications"
            ).fetchone()[0])
        finally:
            connection.close()

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
        with self._lock, self._db() as connection:
            result = self._add_run(connection, reasons, prompt=prompt, notify=notify, kind=kind)
            connection.commit()
            return result

    def _add_run(self, connection: sqlite3.Connection, reasons: list[dict[str, Any]], *,
                 prompt: str = "", notify: bool = False, kind: str = "wake") -> dict[str, Any]:
        reasons = list(reasons)
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
        return self._run(connection.execute(
            "SELECT * FROM runs WHERE id=?", (run_id,)
        ).fetchone())

    def feedback_cursor(self) -> int:
        with self._lock, self._db() as connection:
            row = connection.execute(
                "SELECT notification_id FROM feedback_state WHERE id=1"
            ).fetchone()
            return int(row[0]) if row is not None else 0

    def collect_work_feedback(self, tasks: Any, *,
                              report_for: Any = None) -> dict[str, Any] | None:
        """Deliver newly committed task notices to the existing wake queue.

        The cursor and merged wake are committed by one RunStore transaction,
        so a crash cannot advance delivery without saving the wake or replay a
        wake whose cursor was already saved. Reading the task feed is separate
        from the page read state; acknowledging a notice does not affect this
        cursor.
        """
        with self._lock, self._db() as connection:
            row = connection.execute(
                "SELECT notification_id FROM feedback_state WHERE id=1"
            ).fetchone()
            cursor = int(row[0]) if row is not None else 0
            through = cursor
            reasons: list[dict[str, Any]] = []
            notify = False
            while True:
                page = tasks.notifications(after=through, limit=200)
                if not page:
                    break
                for notice in page:
                    through = max(through, int(notice["id"]))
                    reason = self._work_reason(notice)
                    if reason is None:
                        continue
                    task_id = str(notice["task_id"])
                    report_requested = False
                    if callable(report_for):
                        metadata = report_for(task_id) or {}
                        report_requested = bool(metadata.get("report_requested"))
                    if notice.get("kind") == "completed" and report_requested:
                        reason["report_requested"] = True
                        notify = True
                    if reason not in reasons:
                        reasons.append(reason)
                if len(page) < 200:
                    break
            connection.execute(
                "UPDATE feedback_state SET notification_id=?,updated_at=? WHERE id=1",
                (through, _iso(now_local())),
            )
            result = (self._add_run(connection, reasons, notify=notify)
                      if reasons else None)
            connection.commit()
            return result

    @staticmethod
    def _work_reason(notice: dict[str, Any]) -> dict[str, Any] | None:
        """Keep autonomous outcomes; owner-initiated stops stay page-only."""
        event = str(notice.get("event_type") or "")
        kind = str(notice.get("kind") or "")
        source = str(notice.get("source") or "")
        if kind == "completed":
            pass
        elif kind == "paused":
            if source not in ("agent", "error"):
                return None
        elif kind == "interrupted":
            if source not in ("", "error"):
                return None
        elif event == "interaction.pending":
            pass
        else:
            return None
        state = str(notice.get("current_state") or "")
        if kind == "completed":
            summary = (notice.get("work_summary") or notice.get("blocked")
                       or notice.get("wait_reason") or notice.get("text") or "")
        else:
            summary = (notice.get("wait_reason") or notice.get("blocked")
                       or notice.get("work_summary") or notice.get("text") or "")
        summary = str(summary)[:4000]
        reason = {
            "type": "work_result",
            "notification_id": int(notice["id"]),
            "task_id": str(notice["task_id"]),
            "state": state,
            "summary": summary,
            "source": source or event or kind,
            "kind": kind,
            "event": event,
        }
        if notice.get("interaction_id"):
            reason["interaction_id"] = str(notice["interaction_id"])
        if notice.get("wait_reason"):
            reason["wait_reason"] = str(notice["wait_reason"])[:2000]
        return reason

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
