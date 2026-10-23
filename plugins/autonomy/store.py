"""Private durable data for the autonomy plugin.

Schedules and watches live in the plugin's scheduler service. This store owns
the records specific to Ling: responsibilities, responsibility-linked work
metadata, the feedback cursor, and legacy migration backups. Task facts stay
in the host and are reached through ``host.query``.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


PAUSABLE_TASK_STATES = ("running", "queued", "pausing")


class PluginStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS responsibilities (
                  id TEXT PRIMARY KEY,
                  title TEXT NOT NULL DEFAULT '',
                  instruction TEXT NOT NULL DEFAULT '',
                  state TEXT NOT NULL CHECK(state IN ('active','paused','ended')),
                  origin TEXT NOT NULL CHECK(origin IN ('user','ling')),
                  summary TEXT NOT NULL DEFAULT '',
                  paused_task_ids_json TEXT NOT NULL DEFAULT '[]',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS work_metadata (
                  task_id TEXT PRIMARY KEY,
                  responsibility_id TEXT,
                  report_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS feedback_state (
                  id INTEGER PRIMARY KEY CHECK(id=1),
                  notification_id INTEGER NOT NULL DEFAULT 0,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS read_events (
                  event_id TEXT PRIMARY KEY,
                  read_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def meta(self, key: str, value: str | None = None) -> str | None:
        with self._lock, self._db() as db:
            if value is not None:
                db.execute(
                    "INSERT INTO meta(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(value)),
                )
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row is not None else None

    # --- feedback delivery -------------------------------------------------

    def feedback_cursor(self) -> int:
        with self._lock, self._db() as db:
            row = db.execute(
                "SELECT notification_id FROM feedback_state WHERE id=1"
            ).fetchone()
            return int(row["notification_id"]) if row is not None else 0

    def has_feedback_cursor(self) -> bool:
        with self._lock, self._db() as db:
            return db.execute(
                "SELECT 1 FROM feedback_state WHERE id=1"
            ).fetchone() is not None

    def set_feedback_cursor(self, notification_id: int) -> None:
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO feedback_state(id,notification_id,updated_at) VALUES(1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET notification_id=excluded.notification_id,"
                "updated_at=excluded.updated_at",
                (int(notification_id), now_iso()),
            )

    def ensure_feedback_cursor(self, initial: int) -> None:
        with self._lock, self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO feedback_state(id,notification_id,updated_at) "
                "VALUES(1,?,?)",
                (int(initial), now_iso()),
            )

    # --- responsibilities --------------------------------------------------

    @staticmethod
    def _responsibility(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "instruction": row["instruction"],
            "state": row["state"],
            "origin": row["origin"],
            "summary": row["summary"],
            "paused_task_ids": json.loads(row["paused_task_ids_json"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def responsibilities(self) -> list[dict[str, Any]]:
        with self._lock, self._db() as db:
            rows = db.execute(
                "SELECT * FROM responsibilities ORDER BY created_at DESC,id"
            ).fetchall()
            result = [self._responsibility(row) for row in rows]
        linked: dict[str, list[str]] = {}
        for metadata in self.all_work_metadata().values():
            if metadata.get("responsibility_id"):
                linked.setdefault(metadata["responsibility_id"], []).append(metadata["task_id"])
        for item in result:
            item["task_ids"] = linked.get(item["id"], [])
        return result

    def get_responsibility(self, responsibility_id: str) -> dict[str, Any]:
        with self._lock, self._db() as db:
            row = db.execute(
                "SELECT * FROM responsibilities WHERE id=?", (responsibility_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"长期交代 {responsibility_id} 不存在")
        value = self._responsibility(row)
        value["task_ids"] = [
            metadata["task_id"]
            for metadata in self.all_work_metadata().values()
            if metadata.get("responsibility_id") == responsibility_id
        ]
        return value

    def save_responsibility(
        self,
        *,
        responsibility_id: str | None = None,
        title: str | None = None,
        instruction: str | None = None,
        summary: str | None = None,
        origin: str = "user",
    ) -> dict[str, Any]:
        if origin not in ("user", "ling"):
            raise ValueError("长期交代来源必须是 user 或 ling")
        title = title.strip() if title is not None else None
        instruction = instruction.strip() if instruction is not None else None
        summary = summary.strip() if summary is not None else None
        stamp = now_iso()
        with self._lock, self._db() as db:
            if responsibility_id is None:
                responsibility_id = "responsibility_" + uuid.uuid4().hex[:16]
            row = db.execute(
                "SELECT * FROM responsibilities WHERE id=?", (responsibility_id,)
            ).fetchone()
            if row is None:
                if not title:
                    raise ValueError("长期交代需要标题")
                if not instruction:
                    raise ValueError("长期交代需要说明")
                db.execute(
                    "INSERT INTO responsibilities(id,title,instruction,state,origin,"
                    "summary,paused_task_ids_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (responsibility_id, title, instruction, "active", origin,
                     summary or "", "[]", stamp, stamp),
                )
            else:
                changes: dict[str, Any] = {}
                if title is not None:
                    changes["title"] = title
                if instruction is not None:
                    changes["instruction"] = instruction
                if summary is not None:
                    changes["summary"] = summary
                if changes:
                    assignments = ",".join(f"{key}=?" for key in changes)
                    db.execute(
                        f"UPDATE responsibilities SET {assignments},updated_at=? WHERE id=?",
                        (*changes.values(), stamp, responsibility_id),
                    )
        return self.get_responsibility(responsibility_id)

    def set_responsibility_state(self, responsibility_id: str, state: str) -> None:
        if state not in ("active", "paused", "ended"):
            raise ValueError("长期交代状态无效")
        with self._lock, self._db() as db:
            cursor = db.execute(
                "UPDATE responsibilities SET state=?,updated_at=? WHERE id=?",
                (state, now_iso(), responsibility_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"长期交代 {responsibility_id} 不存在")

    def remember_paused_tasks(self, responsibility_id: str, task_ids: list[str]) -> None:
        if not task_ids:
            return
        with self._lock, self._db() as db:
            row = db.execute(
                "SELECT paused_task_ids_json FROM responsibilities WHERE id=?",
                (responsibility_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"长期交代 {responsibility_id} 不存在")
            values = json.loads(row["paused_task_ids_json"] or "[]")
            for task_id in task_ids:
                if task_id not in values:
                    values.append(task_id)
            db.execute(
                "UPDATE responsibilities SET paused_task_ids_json=?,updated_at=? WHERE id=?",
                (json.dumps(values, ensure_ascii=False), now_iso(), responsibility_id),
            )

    def forget_paused_task(self, task_id: str) -> None:
        with self._lock, self._db() as db:
            rows = db.execute(
                "SELECT id,paused_task_ids_json FROM responsibilities"
            ).fetchall()
            for row in rows:
                values = json.loads(row["paused_task_ids_json"] or "[]")
                if task_id not in values:
                    continue
                kept = [item for item in values if item != task_id]
                db.execute(
                    "UPDATE responsibilities SET paused_task_ids_json=?,updated_at=? WHERE id=?",
                    (json.dumps(kept, ensure_ascii=False), now_iso(), row["id"]),
                )

    def forget_paused_tasks(self, task_ids: list[str]) -> None:
        for task_id in task_ids:
            self.forget_paused_task(task_id)

    # --- work metadata -----------------------------------------------------

    def link_work(
        self,
        task_id: str,
        responsibility_id: str | None = None,
        report_requested: bool = False,
    ) -> dict[str, Any]:
        if responsibility_id is not None:
            record = self.get_responsibility(responsibility_id)
            if record["state"] != "active":
                raise ValueError("暂停或结束的长期交代不能启动新工作")
        with self._lock, self._db() as db:
            db.execute(
                "INSERT INTO work_metadata(task_id,responsibility_id,report_requested) "
                "VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET "
                "responsibility_id=excluded.responsibility_id,"
                "report_requested=excluded.report_requested",
                (task_id, responsibility_id, int(bool(report_requested))),
            )
        return self.work_metadata(task_id)

    def work_metadata(self, task_id: str) -> dict[str, Any]:
        with self._lock, self._db() as db:
            row = db.execute(
                "SELECT * FROM work_metadata WHERE task_id=?", (task_id,)
            ).fetchone()
        if row is None:
            return {
                "task_id": task_id,
                "responsibility_id": None,
                "report_requested": False,
            }
        return {
            "task_id": row["task_id"],
            "responsibility_id": row["responsibility_id"],
            "report_requested": bool(row["report_requested"]),
        }

    def all_work_metadata(self) -> dict[str, dict[str, Any]]:
        with self._lock, self._db() as db:
            rows = db.execute("SELECT * FROM work_metadata").fetchall()
        return {
            row["task_id"]: {
                "task_id": row["task_id"],
                "responsibility_id": row["responsibility_id"],
                "report_requested": bool(row["report_requested"]),
            }
            for row in rows
        }

    def task_paused(self, task_id: str) -> bool:
        metadata = self.work_metadata(task_id)
        responsibility_id = metadata.get("responsibility_id")
        if not responsibility_id:
            return False
        try:
            return self.get_responsibility(responsibility_id)["state"] == "paused"
        except ValueError:
            return False

    def association_for(self, responsibility_id: str) -> list[str]:
        return [
            task_id for task_id, metadata in self.all_work_metadata().items()
            if metadata.get("responsibility_id") == responsibility_id
        ]

    def resumable_task_ids(self) -> list[str]:
        values: list[str] = []
        for record in self.responsibilities():
            if record["state"] != "active":
                continue
            for task_id in record["paused_task_ids"]:
                if task_id not in values:
                    values.append(task_id)
        return values

    # --- activity display --------------------------------------------------

    def mark_run_read(self, event_id: str) -> None:
        with self._lock, self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO read_events(event_id,read_at) VALUES(?,?)",
                (str(event_id), now_iso()),
            )

    def read_events(self) -> dict[str, str]:
        with self._lock, self._db() as db:
            rows = db.execute("SELECT event_id,read_at FROM read_events").fetchall()
        return {row["event_id"]: row["read_at"] for row in rows}

    # --- one-time legacy import -------------------------------------------

    def migrate_legacy(
        self,
        home: Path,
        *,
        scheduler_path: Path,
        notification_head: Callable[[], int],
    ) -> dict[str, Any]:
        """Copy private state from the pre-plugin installation exactly once.

        Generic activity runs are migrated by the host. SQLite backups are kept
        before reading private legacy facts.
        """
        home = Path(home)
        backup_dir = self.path.parent / "legacy-backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        old_automation = home / "automation.sqlite3"
        old_tasks = home / "tasks.sqlite3"
        old_scheduler = home / "scheduler" / "scheduler.sqlite3"

        first = self.meta("legacy_imported") != "1"
        if first:
            for source, target in (
                (old_automation, backup_dir / "automation.sqlite3"),
                (old_tasks, backup_dir / "tasks.sqlite3"),
                (old_scheduler, backup_dir / "scheduler.sqlite3"),
            ):
                if source.is_file() and not target.exists():
                    _backup_database(source, target)

        copied_scheduler = old_scheduler.is_file() and not scheduler_path.exists()
        if copied_scheduler:
            scheduler_path.parent.mkdir(parents=True, exist_ok=True)
            _backup_database(old_scheduler, scheduler_path)

        if not self.has_feedback_cursor():
            cursor = _legacy_feedback_cursor(old_automation)
            if cursor is None:
                cursor = int(notification_head())
            self.ensure_feedback_cursor(cursor)

        if first:
            _import_responsibilities(self, old_tasks)
            self.meta("legacy_imported", "1")

        authority_exists = copied_scheduler or scheduler_path.is_file()
        return {
            "legacy_rules": None if authority_exists else _legacy_rules(old_automation),
        }


def _backup_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source, timeout=10) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def _legacy_feedback_cursor(path: Path) -> int | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(path, timeout=5)
    try:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_state'"
        ).fetchone() is None:
            return None
        row = connection.execute(
            "SELECT notification_id FROM feedback_state WHERE id=1"
        ).fetchone()
        return int(row[0]) if row is not None else None
    finally:
        connection.close()


def _legacy_rules(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"settings", "schedules", "watches"} <= tables:
            return None
        settings = {
            row["key"]: json.loads(row["value"])
            for row in connection.execute("SELECT * FROM settings")
        }
        schedules = []
        for row in connection.execute("SELECT * FROM schedules"):
            item = dict(row)
            item["weekdays"] = json.loads(item.get("weekdays") or "[]")
            item["enabled"] = bool(item["enabled"])
            item["notify"] = bool(item["notify"])
            schedules.append(item)
        watches = []
        for row in connection.execute("SELECT * FROM watches"):
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            watches.append(item)
        return {"settings": settings, "schedules": schedules, "watches": watches}
    finally:
        connection.close()


def _import_responsibilities(store: PluginStore, task_path: Path) -> None:
    if not task_path.is_file():
        return
    connection = sqlite3.connect(task_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "responsibilities" in tables:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(responsibilities)")
            }
            required = {
                "id", "title", "instruction", "state", "origin", "summary",
                "paused_task_ids_json", "created_at", "updated_at",
            }
            if required <= columns:
                with store._lock, store._db() as db:
                    for row in connection.execute("SELECT * FROM responsibilities"):
                        db.execute(
                            "INSERT OR IGNORE INTO responsibilities(id,title,instruction,"
                            "state,origin,summary,paused_task_ids_json,created_at,updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?)",
                            tuple(row[key] for key in (
                                "id", "title", "instruction", "state", "origin",
                                "summary", "paused_task_ids_json", "created_at", "updated_at",
                            )),
                        )
        if "work_metadata" in tables:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(work_metadata)")
            }
            if {"task_id", "responsibility_id", "report_requested"} <= columns:
                with store._lock, store._db() as db:
                    for row in connection.execute("SELECT * FROM work_metadata"):
                        db.execute(
                            "INSERT OR IGNORE INTO work_metadata(task_id,responsibility_id,"
                            "report_requested) VALUES(?,?,?)",
                            (row["task_id"], row["responsibility_id"],
                             int(bool(row["report_requested"]))),
                        )
    finally:
        connection.close()


__all__ = ["PAUSABLE_TASK_STATES", "PluginStore", "now_iso"]
