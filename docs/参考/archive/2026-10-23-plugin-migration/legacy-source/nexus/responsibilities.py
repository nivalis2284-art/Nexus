"""Durable responsibilities and their links to independent work.

Responsibilities live in the task database because their lifecycle is tightly
bound to task lifecycle, while the service keeps task control in the existing
``TaskController``.  A responsibility pause may suspend work it started; a
resume only reopens the responsibility and never dispatches work by itself.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from .task import TaskConflict, TaskNotFound, TaskStore, TaskValidationError
from .task.models import now_iso, task_from_row

RESPONSIBILITY_ORIGINS = ("user", "ling")
_PAUSABLE_TASK_STATES = ("running", "queued", "pausing")


class ResponsibilityStore:
    """Additive responsibility tables in an existing task database."""

    def __init__(self, task_store: TaskStore) -> None:
        self.tasks = task_store
        with self.tasks._lock, self.tasks._transaction() as connection:
            connection.execute(
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
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS work_metadata (
                  task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                  responsibility_id TEXT REFERENCES responsibilities(id) ON DELETE SET NULL,
                  report_requested INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def _row(self, connection: Any, responsibility_id: str) -> Any:
        row = connection.execute(
            "SELECT * FROM responsibilities WHERE id=?", (responsibility_id,)
        ).fetchone()
        if row is None:
            raise TaskNotFound(f"responsibility {responsibility_id} not found")
        return row

    @staticmethod
    def _paused_ids(row: Any) -> list[str]:
        value = json.loads(row["paused_task_ids_json"] or "[]")
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise TaskValidationError("persisted responsibility task ids are invalid")
        return value

    def _record(self, connection: Any, row: Any) -> dict[str, Any]:
        linked = connection.execute(
            """
            SELECT t.* FROM tasks t JOIN work_metadata w ON w.task_id=t.id
            WHERE w.responsibility_id=? ORDER BY t.created_at DESC
            """,
            (row["id"],),
        ).fetchall()
        return {
            "id": row["id"],
            "title": row["title"],
            "instruction": row["instruction"],
            "state": row["state"],
            "origin": row["origin"],
            "summary": row["summary"],
            "paused_task_ids": self._paused_ids(row),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "tasks": [task_from_row(task).to_dict() for task in linked],
        }

    def get(self, responsibility_id: str) -> dict[str, Any]:
        connection = self.tasks._connect()
        try:
            return self._record(connection, self._row(connection, responsibility_id))
        finally:
            connection.close()

    def list(self) -> list[dict[str, Any]]:
        connection = self.tasks._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM responsibilities ORDER BY created_at DESC"
            ).fetchall()
            return [self._record(connection, row) for row in rows]
        finally:
            connection.close()

    def save(
        self, *, title: str | None = None, instruction: str | None = None,
        responsibility_id: str | None = None, summary: str | None = None,
        origin: str = "user",
    ) -> dict[str, Any]:
        if origin not in RESPONSIBILITY_ORIGINS:
            raise TaskValidationError("responsibility origin must be user or ling")
        if title is not None:
            title = title.strip()
        if instruction is not None:
            instruction = instruction.strip()
        if summary is not None:
            summary = summary.strip()
        stamp = now_iso()
        with self.tasks._lock, self.tasks._transaction() as connection:
            if responsibility_id is None:
                responsibility_id = "responsibility_" + uuid.uuid4().hex[:16]
            row = connection.execute(
                "SELECT * FROM responsibilities WHERE id=?", (responsibility_id,)
            ).fetchone()
            if row is None:
                if not title:
                    raise TaskValidationError("responsibility title is required")
                if not instruction:
                    raise TaskValidationError("responsibility instruction is required")
                connection.execute(
                    """INSERT INTO responsibilities(id,title,instruction,state,origin,summary,
                    paused_task_ids_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
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
                    values = ", ".join(f"{key}=?" for key in changes)
                    connection.execute(
                        f"UPDATE responsibilities SET {values},updated_at=? WHERE id=?",
                        (*changes.values(), stamp, responsibility_id),
                    )
            return self._record(connection, self._row(connection, responsibility_id))

    def task_paused(self, task_id: str) -> bool:
        connection = self.tasks._connect()
        try:
            row = connection.execute(
                """SELECT r.state FROM work_metadata w
                JOIN responsibilities r ON r.id=w.responsibility_id WHERE w.task_id=?""",
                (task_id,),
            ).fetchone()
            return bool(row and row["state"] == "paused")
        finally:
            connection.close()

    def resumable_task_ids(self) -> list[str]:
        connection = self.tasks._connect()
        try:
            rows = connection.execute(
                "SELECT paused_task_ids_json FROM responsibilities WHERE state='active'"
            ).fetchall()
            ids = [item for row in rows for item in self._paused_ids(row)]
        finally:
            connection.close()
        result: list[str] = []
        for task_id in ids:
            if task_id in result:
                continue
            try:
                task = self.tasks.get(task_id)
            except TaskNotFound:
                continue
            if task.state == "paused" and not task.archived:
                result.append(task_id)
        return result

    def forget_paused_task(self, task_id: str) -> None:
        stamp = now_iso()
        with self.tasks._lock, self.tasks._transaction() as connection:
            rows = connection.execute(
                "SELECT id,paused_task_ids_json FROM responsibilities"
            ).fetchall()
            for row in rows:
                ids = self._paused_ids(row)
                if task_id not in ids:
                    continue
                kept = [item for item in ids if item != task_id]
                connection.execute(
                    "UPDATE responsibilities SET paused_task_ids_json=?,updated_at=? WHERE id=?",
                    (json.dumps(kept, ensure_ascii=False), stamp, row["id"]),
                )

    def _remember_paused(self, responsibility_id: str, task_ids: list[str]) -> None:
        if not task_ids:
            return
        with self.tasks._lock, self.tasks._transaction() as connection:
            row = self._row(connection, responsibility_id)
            ids = self._paused_ids(row)
            for task_id in task_ids:
                if task_id not in ids:
                    ids.append(task_id)
            connection.execute(
                "UPDATE responsibilities SET paused_task_ids_json=?,updated_at=? WHERE id=?",
                (json.dumps(ids, ensure_ascii=False), now_iso(), responsibility_id),
            )

    def link_work(
        self, task_id: str, responsibility_id: str | None = None,
        report_requested: bool = False,
    ) -> dict[str, Any]:
        with self.tasks._lock, self.tasks._transaction() as connection:
            self.tasks._row(connection, task_id)
            if responsibility_id is not None:
                row = self._row(connection, responsibility_id)
                if row["state"] != "active":
                    raise TaskConflict("new work can only be linked to an active responsibility")
            connection.execute(
                """INSERT INTO work_metadata(task_id,responsibility_id,report_requested)
                VALUES(?,?,?) ON CONFLICT(task_id) DO UPDATE SET
                responsibility_id=excluded.responsibility_id,
                report_requested=excluded.report_requested""",
                (task_id, responsibility_id, int(bool(report_requested))),
            )
            metadata = connection.execute(
                "SELECT * FROM work_metadata WHERE task_id=?", (task_id,)
            ).fetchone()
            return {
                "task_id": metadata["task_id"],
                "responsibility_id": metadata["responsibility_id"],
                "report_requested": bool(metadata["report_requested"]),
            }

    def work_metadata(self, task_id: str) -> dict[str, Any]:
        connection = self.tasks._connect()
        try:
            row = connection.execute(
                "SELECT * FROM work_metadata WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                return {"task_id": task_id, "responsibility_id": None,
                        "report_requested": False}
            return {"task_id": row["task_id"],
                    "responsibility_id": row["responsibility_id"],
                    "report_requested": bool(row["report_requested"])}
        finally:
            connection.close()


class ResponsibilityService:
    """Responsibility lifecycle, linked-work metadata, and Ling's work context."""

    def __init__(self, task_store: TaskStore, controller: Any) -> None:
        self.store = ResponsibilityStore(task_store)
        self.tasks = task_store
        self.controller = controller

    def list(self) -> dict[str, list[dict[str, Any]]]:
        return {"responsibilities": self.store.list()}

    def get(self, responsibility_id: str) -> dict[str, Any]:
        return self.store.get(responsibility_id)

    def save(
        self, *, title: str | None = None, instruction: str | None = None,
        id: str | None = None, summary: str | None = None, origin: str = "user",
    ) -> dict[str, Any]:
        return self.store.save(title=title, instruction=instruction,
                               responsibility_id=id, summary=summary, origin=origin)

    def control(self, id: str, operation: Literal["pause", "resume", "end"]) -> dict[str, Any]:
        if operation not in ("pause", "resume", "end"):
            raise TaskValidationError("responsibility operation must be pause, resume or end")
        current = self.store.get(id)
        if operation == "end":
            if current["state"] != "ended":
                with self.tasks._lock, self.tasks._transaction() as connection:
                    connection.execute(
                        "UPDATE responsibilities SET state='ended',updated_at=? WHERE id=?",
                        (now_iso(), id),
                    )
            return self.store.get(id)
        if operation == "resume":
            if current["state"] == "ended":
                raise TaskConflict("an ended responsibility cannot be resumed")
            if current["state"] != "active":
                with self.tasks._lock, self.tasks._transaction() as connection:
                    connection.execute(
                        "UPDATE responsibilities SET state='active',updated_at=? WHERE id=?",
                        (now_iso(), id),
                    )
            return self.store.get(id)

        if current["state"] == "ended":
            raise TaskConflict("an ended responsibility cannot be paused")
        if current["state"] != "paused":
            with self.tasks._lock, self.tasks._transaction() as connection:
                connection.execute(
                    "UPDATE responsibilities SET state='paused',updated_at=? WHERE id=?",
                    (now_iso(), id),
                )
        linked = [task for task in self.tasks.list()
                  if self.store.work_metadata(task.id)["responsibility_id"] == id
                  and task.state in _PAUSABLE_TASK_STATES]
        paused: list[str] = []
        for task in linked:
            try:
                self.controller.pause(
                    task.id,
                    request_id=f"responsibility-pause-{id}-{task.id}-{task.control_version}",
                    expected_version=task.control_version,
                )
            except TaskConflict as error:
                current = self.tasks.get(task.id)
                if current.state in _PAUSABLE_TASK_STATES:
                    raise TaskConflict(
                        f"responsibility pause did not stop linked task {task.id} "
                        f"(state {current.state})"
                    ) from error
                continue
            paused.append(task.id)
            self.store._remember_paused(id, [task.id])
        return self.store.get(id)

    def task_paused(self, task_id: str) -> bool:
        return self.store.task_paused(task_id)

    def forget_paused_task(self, task_id: str) -> None:
        self.store.forget_paused_task(task_id)

    def resumable_task_ids(self) -> list[str]:
        return self.store.resumable_task_ids()

    def link_work(
        self, task_id: str, responsibility_id: str | None = None,
        report_requested: bool = False,
    ) -> dict[str, Any]:
        return self.store.link_work(task_id, responsibility_id, report_requested)

    def work_metadata(self, task_id: str) -> dict[str, Any]:
        return self.store.work_metadata(task_id)

    def work_context(self) -> str:
        records = self.store.list()
        active = [self._active_record(item) for item in records if item["state"] == "active"]
        paused = [self._summary(item) for item in records if item["state"] == "paused"]
        ended = [self._summary(item) for item in records if item["state"] == "ended"]
        context = {
            "active": active,
            "paused": paused,
            "ended": ended,
            "resumable_task_ids": self.resumable_task_ids(),
            "note": "Paused and ended responsibilities do not resume work automatically.",
        }
        return json.dumps(context, ensure_ascii=False)

    @staticmethod
    def _summary(record: dict[str, Any]) -> dict[str, Any]:
        return {key: record[key] for key in
                ("id", "title", "state", "summary", "updated_at")}

    @staticmethod
    def _active_record(record: dict[str, Any]) -> dict[str, Any]:
        return {
            **{key: record[key] for key in
               ("id", "title", "instruction", "state", "origin", "summary",
                "paused_task_ids", "created_at", "updated_at")},
            "tasks": [
                {key: task[key] for key in
                 ("id", "title", "state", "work_summary", "wait_reason")}
                for task in record["tasks"]
            ],
        }
