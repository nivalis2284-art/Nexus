"""SQLite authority for task state, evidence, schedules, and control events."""

from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..attachments import message_content

from .models import (
    ACTIVITY_STATES, Evidence, Step, Task, TaskConflict, TaskNotFound,
    TaskValidationError, now_iso, task_from_row,
)

TASK_SCHEMA_VERSION = 2

NOTIFICATION_KINDS = frozenset({
    "completed", "blocked", "interaction", "cancelled",
    "interrupted", "paused",
})

# Task facts a saved human decision was granted against. Changing one of these
# makes the decision cover work the user never approved, so it must be
# requested again. Lifecycle transitions (suspend, archive, recovery) leave
# these untouched and keep the decision valid.
AUTHORIZATION_SCOPE_FIELDS = ("goal", "effective_goal", "workspace")


class TaskStore:
    """One local database. Each operation opens a short-lived connection."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def _initialize(self) -> None:
        with self._transaction() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > TASK_SCHEMA_VERSION:
                raise TaskValidationError(
                    f"task database version {version} is newer than this Nexus supports"
                )
            if version == 0:
                statements = (
                    """
                    CREATE TABLE tasks (
                      id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL,
                      constraints TEXT NOT NULL DEFAULT '', effective_goal TEXT NOT NULL DEFAULT '',
                      state TEXT NOT NULL, wait_reason TEXT NOT NULL DEFAULT '',
                      archived INTEGER NOT NULL DEFAULT 0, workspace TEXT NOT NULL,
                      provider_id TEXT NOT NULL DEFAULT '', model_id TEXT NOT NULL DEFAULT '',
                      effort TEXT, progress_version INTEGER NOT NULL DEFAULT 1,
                      control_version INTEGER NOT NULL DEFAULT 1,
                      schedule_version INTEGER NOT NULL DEFAULT 0,
                      steps_json TEXT NOT NULL DEFAULT '[]', decisions_json TEXT NOT NULL DEFAULT '[]',
                      evidence_json TEXT NOT NULL DEFAULT '[]', blocked TEXT NOT NULL DEFAULT '',
                      next_step TEXT NOT NULL DEFAULT '', work_summary TEXT NOT NULL DEFAULT '',
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    """,
                    """
                    CREATE TABLE events (
                      seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      type TEXT NOT NULL, data_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                    );
                    """,
                    """
                    CREATE INDEX events_task_seq ON events(task_id, seq);
                    """,
                    """
                    CREATE TABLE schedules (
                      task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                      due_at TEXT NOT NULL, timezone TEXT NOT NULL, mode TEXT NOT NULL,
                      version INTEGER NOT NULL, status TEXT NOT NULL, claimed_at TEXT,
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    """,
                    """
                    CREATE TABLE commands (
                      request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      operation TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    """,
                    """
                    CREATE TABLE runs (
                      id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                      control_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    """,
                    """
                    CREATE INDEX runs_task ON runs(task_id, created_at);
                    """,
                    """
                    CREATE TABLE operations (
                      id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                      task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      tool_call_id TEXT NOT NULL, tool_name TEXT NOT NULL,
                      arguments_json TEXT NOT NULL, status TEXT NOT NULL,
                      result_text TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL DEFAULT '{}',
                      effect_json TEXT NOT NULL DEFAULT '{}',
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                      UNIQUE(run_id, tool_call_id)
                    );
                    """,
                    """
                    CREATE INDEX operations_task ON operations(task_id, status);
                    """,
                    """
                    CREATE TABLE interactions (
                      id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                      operation_id TEXT REFERENCES operations(id) ON DELETE SET NULL,
                      kind TEXT NOT NULL, payload_json TEXT NOT NULL,
                      status TEXT NOT NULL DEFAULT 'pending', answer_json TEXT NOT NULL DEFAULT 'null',
                      control_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    """,
                    """
                    CREATE INDEX interactions_task ON interactions(task_id, status);
                    """,
                    """
                    CREATE TABLE task_messages (
                      seq INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      kind TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
                    );
                    """,
                    """
                    CREATE TABLE jobs (
                      id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      argv_json TEXT NOT NULL, status TEXT NOT NULL, output TEXT NOT NULL DEFAULT '',
                      cursor INTEGER NOT NULL DEFAULT 0, returncode INTEGER, truncated INTEGER NOT NULL DEFAULT 0,
                      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    """
                )
                for statement in statements:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {TASK_SCHEMA_VERSION}")
            if version < 2:
                # Existing task databases gain additive tables only. Task rows
                # and their model projection stay unchanged across this upgrade.
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS notifications (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                      event_seq INTEGER UNIQUE REFERENCES events(seq) ON DELETE CASCADE,
                      kind TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
                      current_state TEXT NOT NULL, control_version INTEGER NOT NULL,
                      interaction_id TEXT, created_at TEXT NOT NULL, read_at TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS notifications_task_id ON notifications(task_id, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS notifications_unread ON notifications(read_at, id)"
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS task_input_cursors (
                      task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                      applied_through INTEGER NOT NULL DEFAULT 0,
                      updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(f"PRAGMA user_version = {TASK_SCHEMA_VERSION}")
            elif version > TASK_SCHEMA_VERSION:
                raise TaskValidationError(f"unsupported task database version: {version}")

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def _event(self, connection: sqlite3.Connection, task_id: str, kind: str, data: dict[str, Any] | None = None) -> int:
        cursor = connection.execute(
            "INSERT INTO events(task_id,type,data_json,created_at) VALUES(?,?,?,?)",
            (task_id, kind, json.dumps(data or {}, ensure_ascii=False), now_iso()),
        )
        sequence = int(cursor.lastrowid)
        self._record_notification(connection, task_id, sequence, kind, data or {})
        # Copy significant autonomous outcomes in the SAME transaction. These
        # facts must survive deleting the task before the observer next polls.
        if connection.execute("SELECT 1 FROM sqlite_master WHERE name='autonomy_records'").fetchone():
            action = connection.execute("SELECT data FROM autonomy_records WHERE kind='action' AND json_extract(data,'$.task_id')=? LIMIT 1", (task_id,)).fetchone()
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if action and task and task["state"] in ("completed", "cancelled", "paused", "waiting_user"):
                fact = {"task_id": task_id, "goal_id": json.loads(action[0])["goal_id"],
                        "state": task["state"], "summary": task["work_summary"],
                        "blocked": task["blocked"], "next_step": task["next_step"],
                        "evidence": json.loads(task["evidence_json"]),
                        "decisions": json.loads(task["decisions_json"])}
                encoded = json.dumps(fact, ensure_ascii=False, sort_keys=True)
                key = "work:"+hashlib.sha256(encoded.encode()).hexdigest()
                connection.execute("INSERT OR IGNORE INTO autonomy_events(source_key,kind,data,at) VALUES(?,'work_result',?,?)", (key, encoded, now_iso()))
        return sequence

    def _record_notification(self, connection: sqlite3.Connection, task_id: str,
                             event_seq: int, kind: str, data: dict[str, Any]) -> None:
        """Project one significant event into the durable notification feed.

        Task state, the event row, and this projection share the caller's
        transaction. Routine messages and tool progress deliberately produce
        no notification; event_seq makes repeated hooking harmless.
        """
        task = connection.execute(
            "SELECT title,state,control_version,wait_reason,work_summary,blocked "
            "FROM tasks WHERE id=?", (task_id,),
        ).fetchone()
        if task is None:
            return
        notice_kind: str | None = None
        interaction_id = data.get("id") if kind == "interaction.pending" else None
        interaction_id = str(interaction_id) if interaction_id else None
        if kind == "task.control":
            operation = str(data.get("operation") or "")
            if operation == "completed":
                notice_kind = "completed"
            elif operation == "cancelled":
                notice_kind = "cancelled"
            elif operation == "paused":
                notice_kind = "paused"
            elif operation == "waiting_user":
                pending = connection.execute(
                    "SELECT id FROM interactions WHERE task_id=? AND status='pending' "
                    "ORDER BY created_at DESC LIMIT 1", (task_id,),
                ).fetchone()
                if pending is None:
                    notice_kind = "interaction"
                else:
                    # interaction.pending already announced this decision. The
                    # read projection supplies the latest state and version.
                    interaction_id = str(pending["id"])
        elif kind == "task.user_lifecycle":
            state = str(data.get("state") or "")
            if state == "completed":
                notice_kind = "completed"
            elif state == "paused":
                notice_kind = "paused"
        elif kind == "interaction.pending":
            notice_kind = "interaction"
        elif kind == "start.held_after_restart":
            notice_kind = "interaction"
            interaction_id = str(data.get("interaction_id") or "") or None
        elif kind == "run.interrupted":
            notice_kind = "interrupted"
        elif kind == "task.progress":
            blocked, old_blocked = str(task["blocked"] or ""), str(data.get("old_blocked") or "")
            if blocked and blocked != old_blocked:
                notice_kind = "blocked"
        if notice_kind not in NOTIFICATION_KINDS:
            return
        state = str(task["state"])
        title = str(task["title"])
        reason = str(data.get("wait_reason") or task["wait_reason"] or "")
        summary = str(task["work_summary"] or task["blocked"] or task["wait_reason"] or "")
        if notice_kind == "completed":
            text = f"任务已完成：{title}"
            if task["work_summary"]:
                text += f"\n{task['work_summary']}"
        elif notice_kind == "interaction":
            text = f"任务需要你的处理：{title}"
            if reason:
                text += f"\n{reason}"
        elif notice_kind == "blocked":
            text = f"任务遇到阻碍：{title}\n{task['blocked']}"
        elif notice_kind == "cancelled":
            text = f"任务已取消：{title}"
        elif notice_kind == "interrupted":
            text = f"任务执行意外中断：{title}"
        else:
            text = f"任务已挂起：{title}"
            if reason:
                text += f"\n{reason}"
        if summary and notice_kind != "completed" and summary not in text:
            text += f"\n{summary}"
        connection.execute(
            """INSERT OR IGNORE INTO notifications(task_id,event_seq,kind,title,text,
            current_state,control_version,interaction_id,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (task_id, event_seq, notice_kind, title, text[:8000], state,
             int(task["control_version"]), interaction_id, now_iso()),
        )

    def _row(self, connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFound(f"task {task_id} not found")
        return row

    @staticmethod
    def _advance_control_version(
        connection: sqlite3.Connection, task_id: str, *, previous_version: int,
        new_version: int, target_changed: bool,
    ) -> None:
        """Move every still-live record to the task's new control version.

        ``control_version`` identifies the task state a human decision belongs
        to. Every writer that advances it must move pending interactions and
        their unread notices in the same transaction; otherwise a decision is
        left at a version nothing will ever be answered at again (it can be
        neither answered nor dismissed). A target change is the opposite case:
        the earlier version no longer describes the task, so its pending
        decisions are ended explicitly and visibly instead of being carried
        forward or silently stranded.
        """
        if target_changed:
            connection.execute(
                "UPDATE interactions SET status='expired',updated_at=? "
                "WHERE task_id=? AND status='pending'",
                (now_iso(), task_id),
            )
            # An answered-but-not-yet-consumed approval is also an authorization
            # for the previous target. Retire it here so a record written before
            # the target was stored cannot be replayed against the new one.
            connection.execute(
                "UPDATE interactions SET status='expired',updated_at=? "
                "WHERE task_id=? AND kind='approval' AND status='answered'",
                (now_iso(), task_id),
            )
            connection.execute(
                "UPDATE notifications SET control_version=? "
                "WHERE task_id=? AND interaction_id IS NOT NULL AND read_at IS NULL",
                (new_version, task_id),
            )
            return
        connection.execute(
            "UPDATE interactions SET control_version=? WHERE task_id=? "
            "AND status='pending' AND control_version=?",
            (new_version, task_id, previous_version),
        )
        connection.execute(
            "UPDATE notifications SET control_version=? "
            "WHERE task_id=? AND interaction_id IS NOT NULL AND read_at IS NULL",
            (new_version, task_id),
        )

    def create(
        self, *, title: str, goal: str, constraints: str = "", workspace: str,
        provider_id: str = "", model_id: str = "", effort: str | None = None,
        request_id: str | None = None,
    ) -> Task:
        title = title.strip()
        goal = goal.strip()
        if not title or not goal:
            raise TaskValidationError("title and goal are required")
        if len(title) > 160 or len(goal) > 20_000 or len(constraints) > 20_000:
            raise TaskValidationError("task text exceeds its length limit")
        stamp = now_iso()
        task_id = "task_" + uuid.uuid4().hex[:16]
        with self._lock, self._transaction() as connection:
            if request_id:
                previous = connection.execute(
                    "SELECT task_id,operation FROM commands WHERE request_id=?", (request_id,)
                ).fetchone()
                if previous is not None:
                    if previous["operation"] != "create":
                        raise TaskConflict("request id was used for another task command")
                    return task_from_row(self._row(connection, previous["task_id"]))
            connection.execute(
                """INSERT INTO tasks(id,title,goal,constraints,effective_goal,state,workspace,
                provider_id,model_id,effort,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, title, goal, constraints, goal, "pending", workspace,
                 provider_id, model_id, effort, stamp, stamp),
            )
            self._event(connection, task_id, "task.created", {"goal": goal})
            if request_id:
                self._remember_command(connection, request_id, task_id, "create", {"task_id": task_id})
            return task_from_row(self._row(connection, task_id))

    def _remember_command(self, connection: sqlite3.Connection, request_id: str, task_id: str, operation: str, result: dict[str, Any]) -> None:
        if not request_id or len(request_id) > 120:
            raise TaskValidationError("invalid request id")
        connection.execute(
            "INSERT INTO commands(request_id,task_id,operation,result_json,created_at) VALUES(?,?,?,?,?)",
            (request_id, task_id, operation, json.dumps(result, ensure_ascii=False), now_iso()),
        )

    def get(self, task_id: str) -> Task:
        connection = self._connect()
        try:
            return task_from_row(self._row(connection, task_id))
        finally:
            connection.close()

    def list(self, *, include_archived: bool = False) -> list[Task]:
        connection = self._connect()
        try:
            clauses = "" if include_archived else " WHERE archived=0"
            rows = connection.execute(f"SELECT * FROM tasks{clauses} ORDER BY created_at DESC").fetchall()
            return [task_from_row(row) for row in rows]
        finally:
            connection.close()

    def update_progress(self, task_id: str, *, expected_version: int, changes: dict[str, Any]) -> Task:
        allowed = {"steps", "decisions", "blocked", "next_step", "work_summary"}
        unknown = set(changes) - allowed
        if unknown:
            raise TaskValidationError(f"unknown work fields: {', '.join(sorted(unknown))}")
        with self._lock, self._transaction() as connection:
            row = self._row(connection, task_id)
            if row["progress_version"] != expected_version:
                raise TaskConflict(f"task progress was updated (expected {expected_version}, now {row['progress_version']})")
            values: dict[str, Any] = {}
            if "steps" in changes:
                raw = changes["steps"]
                if not isinstance(raw, list) or len(raw) > 100:
                    raise TaskValidationError("steps must be a list of up to 100 items")
                values["steps_json"] = json.dumps([Step.from_dict(item).to_dict() for item in raw], ensure_ascii=False)
            for key in ("decisions",):
                if key in changes:
                    if not isinstance(changes[key], list) or len(changes[key]) > 100:
                        raise TaskValidationError("decisions must be a list of up to 100 objects")
                    values[key + "_json"] = json.dumps(changes[key], ensure_ascii=False)
            for key in ("blocked", "next_step", "work_summary"):
                if key in changes:
                    if not isinstance(changes[key], str) or len(changes[key]) > 8000:
                        raise TaskValidationError(f"{key} must be a string")
                    values[key] = changes[key]
            if not values:
                return task_from_row(row)
            values["progress_version"] = expected_version + 1
            values["updated_at"] = now_iso()
            assignments = ", ".join(f"{key}=?" for key in values)
            connection.execute(f"UPDATE tasks SET {assignments} WHERE id=?", (*values.values(), task_id))
            self._event(connection, task_id, "task.progress",
                        {"version": expected_version + 1, "old_blocked": row["blocked"]})
            return task_from_row(self._row(connection, task_id))

    def add_evidence(self, task_id: str, evidence: Evidence) -> Task:
        with self._lock, self._transaction() as connection:
            row = self._row(connection, task_id)
            existing = json.loads(row["evidence_json"] or "[]")
            if len(existing) >= 300:
                raise TaskValidationError("task evidence limit reached")
            existing.append(evidence.to_dict())
            version = row["progress_version"] + 1
            connection.execute(
                "UPDATE tasks SET evidence_json=?, progress_version=?, updated_at=? WHERE id=?",
                (json.dumps(existing, ensure_ascii=False), version, now_iso(), task_id),
            )
            self._event(connection, task_id, "task.evidence", {"version": version})
            return task_from_row(self._row(connection, task_id))

    def context_message(self, task_id: str) -> None:
        """Persist a compact resume brief before model-visible task context is cut."""
        task = self.get(task_id)
        if task.work_summary or task.next_step:
            return
        self.add_message(
            task_id, kind="notice",
            text="任务已保存；继续时以当前目标、步骤、证据和阻塞为工作上下文。",
        )

    @staticmethod
    def _validated_task_changes(changes: dict[str, Any]) -> dict[str, Any]:
        """Validate user-editable task fields before opening a write transaction."""
        allowed = {"title", "goal", "constraints", "effective_goal", "workspace",
                   "provider_id", "model_id", "effort"}
        unknown = set(changes) - allowed
        if unknown:
            raise TaskValidationError(f"unknown task fields: {', '.join(sorted(unknown))}")
        values: dict[str, Any] = {}
        for key, value in changes.items():
            if key == "effort" and value is None:
                values[key] = None
            elif not isinstance(value, str):
                raise TaskValidationError(f"{key} must be a string")
            elif key in ("title", "goal", "effective_goal", "workspace", "provider_id", "model_id") and not value.strip():
                raise TaskValidationError(f"{key} cannot be empty")
            elif len(value) > 20_000:
                raise TaskValidationError(f"{key} is too long")
            else:
                values[key] = value.strip()
        if "goal" in values and "effective_goal" not in values:
            values["effective_goal"] = values["goal"]
        return values

    def update_task(self, task_id: str, *, expected_version: int,
                    changes: dict[str, Any], steps: list[dict[str, Any]] | None = None) -> Task:
        """Apply user target/binding changes and optional steps in one transaction."""
        values = self._validated_task_changes(changes)
        serialized_steps: list[dict[str, Any]] | None = None
        if steps is not None:
            if not isinstance(steps, list) or len(steps) > 100:
                raise TaskValidationError("steps must be a list of up to 100 items")
            serialized_steps = [Step.from_dict(item).to_dict() for item in steps]
        with self._lock, self._transaction() as connection:
            row = self._row(connection, task_id)
            if row["control_version"] != expected_version:
                raise TaskConflict("task configuration changed; reload before editing")
            if values:
                touched_schedule = set(values) & {
                    "goal", "constraints", "effective_goal", "workspace",
                    "provider_id", "model_id", "effort",
                }
                # A different goal or workspace is different work, so decisions
                # saved for the previous target must be requested again.
                target_changed = any(
                    values[field] != row[field]
                    for field in AUTHORIZATION_SCOPE_FIELDS if field in values
                )
                values["control_version"] = expected_version + 1
                values["updated_at"] = now_iso()
                assignments = ", ".join(f"{key}=?" for key in values)
                connection.execute(f"UPDATE tasks SET {assignments} WHERE id=?",
                                   (*values.values(), task_id))
                self._advance_control_version(
                    connection, task_id, previous_version=expected_version,
                    new_version=expected_version + 1, target_changed=target_changed,
                )
                if touched_schedule:
                    connection.execute("DELETE FROM schedules WHERE task_id=?", (task_id,))
                self._event(connection, task_id, "task.user_edit",
                            {"version": expected_version + 1})
            if serialized_steps is not None:
                connection.execute(
                    "UPDATE tasks SET steps_json=?,progress_version=progress_version+1,"
                    "updated_at=? WHERE id=?",
                    (json.dumps(serialized_steps, ensure_ascii=False), now_iso(), task_id),
                )
                self._event(connection, task_id, "task.progress",
                            {"version": row["progress_version"] + 1})
            return task_from_row(self._row(connection, task_id))

    def command(self, task_id: str, operation: str, *, request_id: str, expected_version: int | None = None) -> Task:
        if operation not in ACTIVITY_STATES and operation not in ("reopen", "archive", "unarchive", "confirm_wait"):
            raise TaskValidationError(f"unknown task operation: {operation}")
        with self._lock, self._transaction() as connection:
            previous = connection.execute(
                "SELECT task_id,operation,result_json FROM commands WHERE request_id=?", (request_id,)
            ).fetchone()
            if previous is not None:
                if previous["task_id"] != task_id or previous["operation"] != operation:
                    raise TaskConflict("request id was used for a different task command")
                return task_from_row(self._row(connection, task_id))
            row = self._row(connection, task_id)
            if expected_version is not None and row["control_version"] != expected_version:
                raise TaskConflict("task control changed; reload before trying again")
            state = row["state"]
            target = state
            wait_reason = row["wait_reason"]
            archived = bool(row["archived"])
            if operation in ("running", "queued"):
                if state == "cancelled":
                    raise TaskConflict("re-enable the task before starting it")
                if state in ("completed", "running", "pausing"):
                    raise TaskConflict(f"cannot start a {state} task")
                target = operation
                wait_reason = ""
            elif operation == "pausing":
                if state != "running":
                    raise TaskConflict("only a running task can be paused")
                target = "pausing"
            elif operation == "paused":
                if state not in ("pending", "running", "pausing", "waiting_user", "queued"):
                    raise TaskConflict(f"cannot suspend a {state} task")
                target = "paused"
                wait_reason = "已挂起；未完成的安排仍会显示"
            elif operation == "confirm_wait":
                if state in ("cancelled", "completed"):
                    raise TaskConflict("inactive task cannot await a start confirmation")
                if state == "running" and row["wait_reason"]:
                    raise TaskConflict("task already has an active interaction")
                target = "waiting_user"
                wait_reason = "定时安排已到期，等待用户确认开始"
            elif operation == "waiting_user":
                if state not in ("running", "pausing", "queued"):
                    raise TaskConflict("only an active task can wait for the user")
                target = "waiting_user"
                # A decision belongs to the state in which it was requested.
                # Waiting itself advances control_version, so update the still-
                # pending decisions in the same transaction; earlier decisions
                # remain tied to their old version and cannot authorize this run.
            elif operation == "completed":
                if state == "cancelled":
                    raise TaskConflict("cancelled tasks are not completed")
                target = "completed"
                wait_reason = ""
            elif operation == "cancelled":
                target = "cancelled"
                wait_reason = "已取消；重新启用后需手动开始"
                connection.execute("DELETE FROM schedules WHERE task_id=?", (task_id,))
            elif operation == "reopen":
                if state != "cancelled":
                    raise TaskConflict("only a cancelled task can be re-enabled")
                target = "paused"
                wait_reason = "已重新启用；点击开始继续"
            elif operation == "archive":
                if state in ("running", "pausing", "queued", "waiting_user"):
                    raise TaskConflict("finish or suspend the task before archiving")
                archived = True
                connection.execute("DELETE FROM schedules WHERE task_id=?", (task_id,))
            elif operation == "unarchive":
                archived = False
            control_version = row["control_version"] + 1
            connection.execute(
                "UPDATE tasks SET state=?,wait_reason=?,archived=?,control_version=?,updated_at=? WHERE id=?",
                (target, wait_reason, int(archived), control_version, now_iso(), task_id),
            )
            # Suspension/resume retain the same live request. Ending work
            # retires its pending decisions and unused grants atomically.
            self._advance_control_version(
                connection, task_id, previous_version=row["control_version"],
                new_version=control_version, target_changed=target in ("cancelled", "completed"),
            )
            result = {"task_id": task_id, "state": target}
            self._remember_command(connection, request_id, task_id, operation, result)
            self._event(connection, task_id, "task.control", {
                "operation": operation, "state": target, "wait_reason": wait_reason,
            })
            return task_from_row(self._row(connection, task_id))

    def edit_lifecycle(self, task_id: str, operation: str, *, request_id: str, expected_version: int) -> Task:
        """Human correction of task status, retained with its source label."""
        if operation not in ("mark_completed", "mark_paused"):
            raise TaskValidationError(f"unknown lifecycle edit: {operation}")
        with self._lock, self._transaction() as connection:
            previous = connection.execute("SELECT task_id,operation FROM commands WHERE request_id=?",
                                          (request_id,)).fetchone()
            if previous is not None:
                if previous["task_id"] != task_id or previous["operation"] != operation:
                    raise TaskConflict("request id was used for another command")
                return task_from_row(self._row(connection, task_id))
            row = self._row(connection, task_id)
            if row["control_version"] != expected_version:
                raise TaskConflict("task changed before your correction")
            if row["state"] in ("running", "pausing", "queued"):
                raise TaskConflict("stop the active task before changing its lifecycle")
            target = "completed" if operation == "mark_completed" else "paused"
            wait_reason = "用户确认完成；这不免除可继续验证的后续工作" if target == "completed" else ""
            if target == "completed":
                if self.pending_interactions(task_id) or self.unfinished_operations(task_id):
                    raise TaskConflict("resolve pending work and unknown operations first")
                connection.execute("DELETE FROM schedules WHERE task_id=?", (task_id,))
            connection.execute(
                "UPDATE tasks SET state=?,wait_reason=?,control_version=?,updated_at=? WHERE id=?",
                (target, wait_reason,
                 expected_version + 1, now_iso(), task_id),
            )
            # A human lifecycle correction changes status only; a decision the
            # user already opened stays answerable at the new version.
            self._advance_control_version(
                connection, task_id, previous_version=expected_version,
                new_version=expected_version + 1, target_changed=False,
            )
            self._remember_command(connection, request_id, task_id, operation, {"state": target})
            self._event(connection, task_id, "task.user_lifecycle", {
                "state": target, "source": "user", "wait_reason": wait_reason,
            })
            return task_from_row(self._row(connection, task_id))

    def delete(self, task_id: str, *, expected_version: int) -> None:
        with self._lock, self._transaction() as connection:
            row = self._row(connection, task_id)
            if row["control_version"] != expected_version:
                raise TaskConflict("task changed before deletion")
            if row["state"] in ("running", "pausing", "queued", "waiting_user"):
                raise TaskConflict("cannot delete an active task")
            connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def begin_run(self, task_id: str) -> dict[str, Any]:
        """Create one execution record; the controller is the only caller."""
        with self._lock, self._transaction() as connection:
            row = self._row(connection, task_id)
            run_id = "run_" + uuid.uuid4().hex[:16]
            stamp = now_iso()
            connection.execute(
                "INSERT INTO runs(id,task_id,status,reason,control_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (run_id, task_id, "running", "controller", row["control_version"], stamp, stamp),
            )
            self._event(connection, task_id, "run.started", {"run_id": run_id})
            return {"id": run_id, "task_id": task_id, "control_version": row["control_version"]}

    def end_run(self, run_id: str, *, status: str, reason: str = "") -> None:
        with self._lock, self._transaction() as connection:
            row = connection.execute("SELECT task_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                return
            connection.execute("UPDATE runs SET status=?,reason=?,updated_at=? WHERE id=?",
                               (status, reason, now_iso(), run_id))
            self._event(connection, row["task_id"], "run.ended", {"run_id": run_id, "status": status})

    def current_run(self, task_id: str) -> str:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id FROM runs WHERE task_id=? AND status='running' ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskConflict("task has no active run claim")
            return row["id"]
        finally:
            connection.close()

    def mark_dispatching_unknown(self, run_id: str) -> None:
        """A worker exited without committing the result of a dispatched call."""
        with self._lock, self._transaction() as connection:
            connection.execute(
                "UPDATE operations SET status='unknown',updated_at=? "
                "WHERE run_id=? AND status='dispatching'",
                (now_iso(), run_id),
            )

    def mark_jobs_interrupted(self, task_id: str) -> None:
        with self._lock, self._transaction() as connection:
            connection.execute(
                "UPDATE jobs SET status='interrupted',updated_at=? WHERE task_id=? AND status='running'",
                (now_iso(), task_id),
            )

    def record_job_snapshots(self, task_id: str, snapshots: list[dict[str, Any]]) -> None:
        """Persist a bounded completion fact, not a resumable process handle."""
        with self._lock, self._transaction() as connection:
            self._row(connection, task_id)
            for snapshot in snapshots:
                value = {key: snapshot.get(key) for key in
                         ("id", "argv", "running", "returncode", "elapsed",
                          "buffered_chars", "truncated")}
                output = str(snapshot.get("tail", ""))[:8_000]
                if not output and snapshot.get("id"):
                    previous = connection.execute(
                        "SELECT output FROM jobs WHERE id=?", (str(value["id"]),)
                    ).fetchone()
                    if previous is not None:
                        output = str(previous["output"])
                connection.execute(
                    """INSERT INTO jobs(id,task_id,argv_json,status,output,cursor,returncode,
                    truncated,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET status=excluded.status,output=excluded.output,
                    returncode=excluded.returncode,truncated=excluded.truncated,updated_at=excluded.updated_at""",
                    (str(value["id"]), task_id, json.dumps(value["argv"], ensure_ascii=False),
                     "finished" if not value["running"] else "running", output, 0,
                     value["returncode"], int(bool(value["truncated"])), now_iso(), now_iso()),
                )

    def task_jobs(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            self._row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM jobs WHERE task_id=? ORDER BY created_at", (task_id,)
            ).fetchall()
            return [{"id": row["id"], "argv": json.loads(row["argv_json"]),
                     "status": row["status"], "returncode": row["returncode"],
                     "output": row["output"], "truncated": bool(row["truncated"])} for row in rows]
        finally:
            connection.close()

    def operation_begin(
        self, *, run_id: str, tool_call_id: str, tool_name: str,
        arguments: dict[str, Any], effect: dict[str, Any] | None = None,
    ) -> str:
        """Persist that a side effect may now be dispatched."""
        with self._lock, self._transaction() as connection:
            run = connection.execute("SELECT task_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise TaskNotFound(f"run {run_id} not found")
            operation_id = "op_" + uuid.uuid4().hex[:16]
            stamp = now_iso()
            connection.execute(
                """INSERT INTO operations(id,run_id,task_id,tool_call_id,tool_name,
                arguments_json,status,effect_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (operation_id, run_id, run["task_id"], tool_call_id, tool_name,
                 json.dumps(arguments, ensure_ascii=False), "dispatching",
                 json.dumps(effect or {}, ensure_ascii=False), stamp, stamp),
            )
            self._event(connection, run["task_id"], "operation.dispatching", {"id": operation_id})
            return operation_id

    def operation_finish(self, operation_id: str, *, ok: bool, content: str, data: dict[str, Any] | None = None) -> None:
        with self._lock, self._transaction() as connection:
            row = connection.execute("SELECT task_id FROM operations WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                return
            connection.execute(
                "UPDATE operations SET status=?,result_text=?,result_json=?,updated_at=? WHERE id=?",
                ("succeeded" if ok else "failed", content, json.dumps(data or {}, ensure_ascii=False), now_iso(), operation_id),
            )
            self._event(connection, row["task_id"], "operation.finished", {"id": operation_id, "ok": ok})

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                raise TaskNotFound(f"operation {operation_id} not found")
            return {"id": row["id"], "tool_name": row["tool_name"],
                    "arguments": json.loads(row["arguments_json"]), "status": row["status"]}
        finally:
            connection.close()

    def resume_credentials(self, task_id: str) -> dict[str, Any]:
        """Return binding and durable work needed by the task runner."""
        task = self.get(task_id)
        return {
            "task": task,
            "messages": self.messages(task_id),
            "interactions": self.pending_interactions(task_id),
            "operations": self.unfinished_operations(task_id),
            "schedule": self.schedule(task_id),
        }

    def recover_interrupted(self) -> list[str]:
        """Convert orphaned runs into reviewable task state on service boot.

        This never restarts a task. A dispatching operation may already have
        affected the world, so it must stay unknown until the environment or a
        human resolves it.
        """
        interrupted: list[str] = []
        with self._lock, self._transaction() as connection:
            rows = connection.execute(
                "SELECT id,task_id FROM runs WHERE status IN ('running','pausing','queued')"
            ).fetchall()
            for row in rows:
                connection.execute("UPDATE runs SET status='interrupted',updated_at=? WHERE id=?",
                                   (now_iso(), row["id"]))
                connection.execute(
                    "UPDATE operations SET status='unknown',updated_at=? WHERE run_id=? AND status='dispatching'",
                    (now_iso(), row["id"]),
                )
                task = connection.execute(
                    "SELECT state,control_version FROM tasks WHERE id=?", (row["task_id"],)
                ).fetchone()
                if task is not None and task["state"] in ("running", "pausing", "queued"):
                    connection.execute(
                        "UPDATE tasks SET state='paused',wait_reason=?,control_version=?,updated_at=? WHERE id=?",
                        ("服务上次意外结束；请核查未知操作后手动继续",
                         task["control_version"] + 1, now_iso(), row["task_id"]),
                    )
                    # Restart recovery is a status change: a decision recorded
                    # before the crash stays answerable instead of becoming an
                    # orphan that can be neither answered nor dismissed.
                    self._advance_control_version(
                        connection, row["task_id"], previous_version=task["control_version"],
                        new_version=task["control_version"] + 1, target_changed=False,
                    )
                self._event(connection, row["task_id"], "run.interrupted", {
                    "run_id": row["id"],
                    "wait_reason": "服务上次意外结束；请核查未知操作后手动继续",
                })
                interrupted.append(row["task_id"])
            # The service cannot resume background processes. Persist the fact
            # explicitly if a job row survived a crash.
            connection.execute(
                "UPDATE jobs SET status='interrupted',updated_at=? WHERE status='running'",
                (now_iso(),),
            )
        return interrupted

    def hold_queued_at_startup(self) -> list[str]:
        """A queue entry from a previous process needs a fresh owner decision."""
        held: list[str] = []
        with self._lock, self._transaction() as connection:
            rows = connection.execute("SELECT id FROM tasks WHERE state='queued'").fetchall()
            for row in rows:
                task = self._row(connection, row["id"])
                interaction_id = "int_" + uuid.uuid4().hex[:16]
                stamp = now_iso()
                connection.execute(
                    """INSERT INTO interactions(id,task_id,run_id,operation_id,kind,payload_json,
                    status,control_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (interaction_id, task["id"], None, None, "start_confirmation",
                     json.dumps({"reason": "queued_before_restart"}, ensure_ascii=False),
                     "pending", task["control_version"] + 1, stamp, stamp),
                )
                connection.execute(
                    "UPDATE tasks SET state='waiting_user',wait_reason=?,control_version=control_version+1,updated_at=? WHERE id=?",
                    ("重启前任务已排队但尚未开始，等待用户确认", stamp, task["id"]),
                )
                # Holding the queue entry is a status change. Any decision that
                # was already pending must follow the new version rather than
                # being stranded at a version that can never be answered.
                self._advance_control_version(
                    connection, task["id"], previous_version=task["control_version"],
                    new_version=task["control_version"] + 1, target_changed=False,
                )
                self._event(connection, task["id"], "start.held_after_restart",
                            {"interaction_id": interaction_id,
                             "wait_reason": "重启前任务已排队但尚未开始，等待用户确认"})
                held.append(task["id"])
        return held

    def interaction_create(
        self, *, task_id: str, run_id: str | None, kind: str, payload: dict[str, Any],
        operation_id: str | None = None, control_version: int = 1,
    ) -> str:
        with self._lock, self._transaction() as connection:
            self._row(connection, task_id)
            interaction_id = "int_" + uuid.uuid4().hex[:16]
            stamp = now_iso()
            connection.execute(
                """INSERT INTO interactions(id,task_id,run_id,operation_id,kind,payload_json,
                status,control_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (interaction_id, task_id, run_id, operation_id, kind,
                 json.dumps(payload, ensure_ascii=False), "pending", control_version, stamp, stamp),
            )
            self._event(connection, task_id, "interaction.pending", {
                "id": interaction_id, "kind": kind,
                "wait_reason": str(payload.get("question") or payload.get("reason") or ""),
            })
            return interaction_id

    def request_confirmation(
        self, task_id: str, *, request_id: str, payload: dict[str, Any],
    ) -> str:
        """Create the pending decision and its version in one transaction."""
        with self._lock, self._transaction() as connection:
            row = self._row(connection, task_id)
            if row["state"] in ("cancelled", "completed") or row["archived"]:
                raise TaskConflict("inactive task cannot await a start confirmation")
            interaction_id = "int_" + uuid.uuid4().hex[:16]
            stamp = now_iso()
            connection.execute(
                """INSERT INTO interactions(id,task_id,run_id,operation_id,kind,payload_json,
                status,control_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (interaction_id, task_id, None, None, "start_confirmation",
                 json.dumps(payload, ensure_ascii=False), "pending",
                 row["control_version"] + 1, stamp, stamp),
            )
            connection.execute(
                "UPDATE tasks SET state='waiting_user',wait_reason=?,control_version=control_version+1,updated_at=? WHERE id=?",
                ("定时安排已到期，等待用户确认开始", stamp, task_id),
            )
            # Earlier pending decisions belong to the state before this start
            # request but still describe the same target, so they stay answerable.
            self._advance_control_version(
                connection, task_id, previous_version=row["control_version"],
                new_version=row["control_version"] + 1, target_changed=False,
            )
            self._remember_command(connection, request_id, task_id, "confirm_wait",
                                   {"interaction_id": interaction_id})
            self._event(connection, task_id, "interaction.pending",
                        {"id": interaction_id, "kind": "start_confirmation",
                         "wait_reason": "定时安排已到期，等待用户确认开始"})
            return interaction_id

    def interaction_answer(self, interaction_id: str, *, answer: Any, expected_version: int | None = None) -> dict[str, Any]:
        with self._lock, self._transaction() as connection:
            row = connection.execute("SELECT * FROM interactions WHERE id=?", (interaction_id,)).fetchone()
            if row is None:
                raise TaskNotFound(f"interaction {interaction_id} not found")
            if row["status"] == "expired":
                raise TaskConflict(
                    "task target changed after this decision was requested; "
                    "request approval again"
                )
            if row["status"] != "pending":
                raise TaskConflict("interaction has already been answered")
            task = self._row(connection, row["task_id"])
            if task["state"] in ("completed", "cancelled"):
                raise TaskConflict("inactive task cannot accept this decision")
            # Lifecycle transitions keep pending decisions at the current
            # version, so a mismatch means the target was changed or the
            # request the user is holding no longer describes this task.
            if row["control_version"] != task["control_version"]:
                raise TaskConflict("task changed before this answer")
            if expected_version is not None and row["control_version"] != expected_version:
                raise TaskConflict("task changed before this answer")
            connection.execute("UPDATE interactions SET status='answered',answer_json=?,updated_at=? WHERE id=?",
                               (json.dumps(answer, ensure_ascii=False), now_iso(), interaction_id))
            rendered = ("approved" if answer is True else "declined") if row["kind"] == "approval" else str(answer)
            payload = json.loads(row["payload_json"])
            action = ""
            if row["kind"] == "approval":
                action = "\nSaved action: " + json.dumps(
                    {"tool": payload.get("tool"), "arguments": payload.get("arguments")}, ensure_ascii=False
                )
            elif row["kind"] == "question":
                action = "\nSaved questions: " + json.dumps(payload.get("questions") or [], ensure_ascii=False)
            connection.execute(
                "INSERT INTO task_messages(task_id,kind,text,data_json,created_at) VALUES(?,?,?,?,?)",
                (row["task_id"], "user",
                 f"User answered the saved {row['kind']}: {rendered}{action}",
                 json.dumps({"interaction_id": interaction_id, "source": "user_answer"},
                            ensure_ascii=False), now_iso()),
            )
            self._event(connection, row["task_id"], "interaction.answered", {"id": interaction_id})
            return {"id": interaction_id, "task_id": row["task_id"], "operation_id": row["operation_id"], "answer": answer}

    def answered_approval(self, task_id: str, *, tool: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        """Find one exact, still-unconsumed answer for this task and tool call.

        Only a decision that is still usable may be replayed. Changing the goal
        or workspace retires the approvals taken for the previous target (see
        ``_advance_control_version``), so this can only match answers granted
        for the work the task is doing now.
        """
        connection = self._connect()
        try:
            self._row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM interactions WHERE task_id=? AND kind='approval' "
                "AND status='answered' ORDER BY updated_at DESC LIMIT 50", (task_id,)
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                if payload.get("tool") == tool and payload.get("arguments") == arguments:
                    return {"id": row["id"], "answer": json.loads(row["answer_json"])}
            return None
        finally:
            connection.close()

    def consume_interaction(self, interaction_id: str) -> None:
        with self._lock, self._transaction() as connection:
            row = connection.execute("SELECT task_id FROM interactions WHERE id=?", (interaction_id,)).fetchone()
            if row is None:
                raise TaskNotFound(f"interaction {interaction_id} not found")
            connection.execute("UPDATE interactions SET status='consumed',updated_at=? WHERE id=?",
                               (now_iso(), interaction_id))
            self._event(connection, row["task_id"], "interaction.consumed", {"id": interaction_id})

    def answered_question(self, task_id: str, *, questions: list[dict[str, Any]]) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            self._row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM interactions WHERE task_id=? AND kind='question' "
                "AND status='answered' ORDER BY updated_at DESC LIMIT 50", (task_id,)
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                if payload.get("questions") == questions:
                    return {"id": row["id"], "answer": json.loads(row["answer_json"])}
            return None
        finally:
            connection.close()

    def pending_interactions(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            task = self._row(connection, task_id)
            if task["state"] in ("completed", "cancelled"):
                return []
            rows = connection.execute(
                "SELECT * FROM interactions WHERE task_id=? AND status='pending' ORDER BY created_at", (task_id,)
            ).fetchall()
            return [{"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload_json"]),
                     "operation_id": r["operation_id"], "control_version": r["control_version"],
                     "created_at": r["created_at"]} for r in rows]
        finally:
            connection.close()

    def interaction(self, interaction_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM interactions WHERE id=?", (interaction_id,)).fetchone()
            if row is None:
                raise TaskNotFound(f"interaction {interaction_id} not found")
            return {"id": row["id"], "task_id": row["task_id"], "run_id": row["run_id"],
                    "kind": row["kind"], "payload": json.loads(row["payload_json"]),
                    "status": row["status"], "answer": json.loads(row["answer_json"]),
                    "operation_id": row["operation_id"]}
        finally:
            connection.close()

    def unfinished_operations(self, task_id: str) -> list[dict[str, Any]]:
        """Operations whose external effects are not known to have completed."""
        connection = self._connect()
        try:
            self._row(connection, task_id)
            rows = connection.execute(
                "SELECT * FROM operations WHERE task_id=? AND status IN ('dispatching','unknown') ORDER BY created_at",
                (task_id,),
            ).fetchall()
            return [
                {"id": r["id"], "tool_name": r["tool_name"], "tool_call_id": r["tool_call_id"],
                 "arguments": json.loads(r["arguments_json"]), "status": r["status"],
                 "effect": json.loads(r["effect_json"])}
                for r in rows
            ]
        finally:
            connection.close()

    def resolve_operation(self, operation_id: str, *, conclusion: str, observation: str,
                          task_id: str | None = None) -> None:
        if conclusion not in ("succeeded", "failed", "not_applied", "unknown"):
            raise TaskValidationError("invalid operation conclusion")
        if not observation.strip():
            raise TaskValidationError("an operation conclusion requires an observation")
        with self._lock, self._transaction() as connection:
            row = connection.execute("SELECT task_id,status FROM operations WHERE id=?", (operation_id,)).fetchone()
            if row is None:
                raise TaskNotFound(f"operation {operation_id} not found")
            if task_id is not None and row["task_id"] != task_id:
                raise TaskConflict("operation belongs to another task")
            connection.execute(
                "UPDATE operations SET status=?,updated_at=? WHERE id=?",
                (conclusion if conclusion != "not_applied" else "failed_not_applied", now_iso(), operation_id),
            )
            self._event(connection, row["task_id"], "operation.resolved",
                        {"id": operation_id, "conclusion": conclusion, "observation": observation[:2000]})

    def events(self, task_id: str, *, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        if after < 0 or not 1 <= limit <= 500:
            raise TaskValidationError("invalid event cursor or limit")
        connection = self._connect()
        try:
            self._row(connection, task_id)
            rows = connection.execute(
                "SELECT seq,type,data_json,created_at FROM events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?",
                (task_id, after, limit),
            ).fetchall()
            return [
                {"seq": row["seq"], "type": row["type"],
                 "data": json.loads(row["data_json"]), "created_at": row["created_at"]}
                for row in rows
            ]
        finally:
            connection.close()

    def notifications(self, *, after: int = 0, limit: int = 100,
                      unread_only: bool = False) -> list[dict[str, Any]]:
        """Read the durable, globally ordered important-event feed."""
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise TaskValidationError("invalid notification cursor or limit")
        connection = self._connect()
        try:
            where = "WHERE n.id>?" + (" AND n.read_at IS NULL" if unread_only else "")
            rows = connection.execute(
                "SELECT n.id AS notification_id,n.task_id,n.kind,n.title,n.text,"
                "n.created_at,n.read_at,n.interaction_id,t.wait_reason AS task_wait_reason,"
                "CASE WHEN n.kind='interaction' AND i.id IS NOT NULL AND i.status='pending' AND t.state NOT IN ('cancelled','completed') "
                "THEN 'waiting_user' "
                "ELSE t.state END AS current_state,"
                "t.control_version AS current_version,"
                "t.wait_reason AS current_wait_reason,i.status AS interaction_status,"
                "i.control_version AS interaction_version,i.payload_json AS interaction_payload "
                "FROM notifications n "
                "JOIN tasks t ON t.id=n.task_id LEFT JOIN interactions i ON i.id=n.interaction_id "
                + where + " ORDER BY n.id LIMIT ?",
                (after, limit),
            ).fetchall()
            return [self._notification_dict(row) for row in rows]
        finally:
            connection.close()

    @staticmethod
    def _notification_dict(row: sqlite3.Row) -> dict[str, Any]:
        item = {
            "id": int(row["notification_id"]), "task_id": row["task_id"], "kind": row["kind"],
            "title": row["title"], "text": row["text"],
            "created_at": row["created_at"], "read_at": row["read_at"],
            "interaction_id": row["interaction_id"],
            "current_state": row["current_state"],
            "control_version": int(row["current_version"]),
        }
        if row["interaction_id"]:
            item["interaction_status"] = row["interaction_status"]
            item["interaction_payload"] = json.loads(row["interaction_payload"] or "{}")
            item["interaction_expired"] = (
                row["interaction_status"] != "pending"
                or row["interaction_version"] < row["current_version"]
                or row["current_state"] in ("completed", "cancelled")
            )
            if item["interaction_expired"]:
                item["stale_reason"] = "interaction is no longer pending at the current control version"
        return item

    def ack_notification(self, notification_id: int) -> dict[str, Any]:
        """Mark one notification read; acknowledgement is idempotent."""
        if type(notification_id) is not int or notification_id <= 0:
            raise TaskValidationError("invalid notification id")
        with self._lock, self._transaction() as connection:
            row = connection.execute("SELECT * FROM notifications WHERE id=?",
                                     (notification_id,)).fetchone()
            if row is None:
                raise TaskNotFound(f"notification {notification_id} not found")
            if row["read_at"] is None:
                connection.execute("UPDATE notifications SET read_at=? WHERE id=? AND read_at IS NULL",
                                   (now_iso(), notification_id))
            row = connection.execute(
                "SELECT n.id AS notification_id,n.task_id,n.kind,n.title,n.text,"
                "n.created_at,n.read_at,n.interaction_id,"
                "CASE WHEN n.kind='interaction' AND i.id IS NOT NULL AND i.status='pending' AND t.state NOT IN ('cancelled','completed') "
                "THEN 'waiting_user' ELSE t.state END AS current_state,"
                "t.control_version AS current_version,"
                "i.status AS interaction_status,i.control_version AS interaction_version,"
                "i.payload_json AS interaction_payload "
                "FROM notifications n JOIN tasks t ON t.id=n.task_id "
                "LEFT JOIN interactions i ON i.id=n.interaction_id WHERE n.id=?",
                (notification_id,),
            ).fetchone()
            return self._notification_dict(row)

    def notification_count(self, *, unread_only: bool = True) -> int:
        """Return the full count, independent of the notification page size."""
        connection = self._connect()
        try:
            where = "WHERE read_at IS NULL" if unread_only else ""
            return int(connection.execute(
                f"SELECT COUNT(*) FROM notifications {where}"
            ).fetchone()[0])
        finally:
            connection.close()

    def mark_inputs_applied(self, task_id: str, through_seq: int) -> dict[str, int]:
        """Advance the model-boundary cursor without moving it backwards."""
        if type(through_seq) is not int or through_seq < 0:
            raise TaskValidationError("through_seq must be a non-negative integer")
        with self._lock, self._transaction() as connection:
            self._row(connection, task_id)
            latest_user = connection.execute(
                "SELECT COALESCE(MAX(seq),0) FROM task_messages WHERE task_id=? AND kind='user'",
                (task_id,),
            ).fetchone()[0]
            if through_seq > int(latest_user):
                raise TaskValidationError("cannot apply an input that does not exist")
            current = connection.execute(
                "SELECT applied_through FROM task_input_cursors WHERE task_id=?", (task_id,)
            ).fetchone()
            applied = max(int(current[0]) if current else 0, through_seq)
            connection.execute(
                "INSERT INTO task_input_cursors(task_id,applied_through,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET applied_through=excluded.applied_through,"
                "updated_at=excluded.updated_at",
                (task_id, applied, now_iso()),
            )
            return self._input_status(connection, task_id)

    def input_status(self, task_id: str) -> dict[str, int]:
        connection = self._connect()
        try:
            self._row(connection, task_id)
            return self._input_status(connection, task_id)
        finally:
            connection.close()

    @staticmethod
    def _input_status(connection: sqlite3.Connection, task_id: str) -> dict[str, int]:
        cursor = connection.execute(
            "SELECT applied_through FROM task_input_cursors WHERE task_id=?", (task_id,)
        ).fetchone()
        applied = int(cursor[0]) if cursor else 0
        pending = connection.execute(
            "SELECT COUNT(*) FROM task_messages WHERE task_id=? AND kind='user' AND seq>?",
            (task_id, applied),
        ).fetchone()[0]
        return {"applied_through": applied, "pending_count": int(pending)}

    def add_message(self, task_id: str, *, kind: str, text: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if kind not in ("user", "assistant", "tool", "notice", "autonomy"):
            raise TaskValidationError("invalid task message kind")
        with self._lock, self._transaction() as connection:
            self._row(connection, task_id)
            cursor = connection.execute(
                "INSERT INTO task_messages(task_id,kind,text,data_json,created_at) VALUES(?,?,?,?,?)",
                (task_id, kind, text, json.dumps(data or {}, ensure_ascii=False), now_iso()),
            )
            sequence = int(cursor.lastrowid)
            if kind == "user":
                # The first snapshot after upgrading an older task establishes
                # a baseline; those records predate the delivery cursor.
                connection.execute(
                    "INSERT OR IGNORE INTO task_input_cursors(task_id,applied_through,updated_at) "
                    "VALUES(?,?,?)", (task_id, sequence - 1, now_iso()),
                )
            self._event(connection, task_id, "message", {"seq": sequence})
        return {"seq": sequence, "kind": kind, "text": text, **(data or {})}

    def message_for_request(self, task_id: str, request_id: str) -> dict[str, Any] | None:
        """Find an already accepted input before repeating attachment copies."""
        connection = self._connect()
        try:
            command = connection.execute(
                "SELECT task_id,operation,result_json FROM commands WHERE request_id=?", (request_id,)
            ).fetchone()
            if command is None:
                return None
            if command["task_id"] != task_id or command["operation"] != "message":
                raise TaskConflict("request id was used for another task command")
            sequence = json.loads(command["result_json"])["seq"]
            row = connection.execute(
                "SELECT seq,text,data_json FROM task_messages WHERE task_id=? AND seq=?", (task_id, sequence)
            ).fetchone()
            if row is None:
                raise TaskNotFound("saved task message no longer exists")
            return {"seq": row["seq"], "text": row["text"], **json.loads(row["data_json"])}
        finally:
            connection.close()

    def accept_user_message(self, task_id: str, *, text: str, request_id: str,
                            data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Persist one user input and return its stable message sequence.

        The request id is stored with the message so retrying an HTTP request
        cannot enqueue it twice. Input is accepted independently of execution;
        the caller decides which lifecycle transition, if any, to request.
        """
        if not request_id or len(request_id) > 120:
            raise TaskValidationError("invalid request id")
        with self._lock, self._transaction() as connection:
            self._row(connection, task_id)
            previous = connection.execute(
                "SELECT task_id,operation,result_json FROM commands WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if previous is not None:
                if previous["task_id"] != task_id or previous["operation"] != "message":
                    raise TaskConflict("request id was used for another task command")
                saved = json.loads(previous["result_json"])
                row = connection.execute(
                    "SELECT seq,kind,text,data_json,created_at FROM task_messages "
                    "WHERE task_id=? AND seq=?", (task_id, int(saved["seq"])),
                ).fetchone()
                if row is None:
                    raise TaskNotFound("saved task message no longer exists")
                if row["text"] != text or json.loads(row["data_json"]) != (data or {}):
                    raise TaskConflict("request id was used for different message content")
                return {"seq": row["seq"], "kind": row["kind"], "text": row["text"],
                        "created_at": row["created_at"], **json.loads(row["data_json"])}
            cursor = connection.execute(
                "INSERT INTO task_messages(task_id,kind,text,data_json,created_at) VALUES(?,?,?,?,?)",
                (task_id, "user", text, json.dumps(data or {}, ensure_ascii=False), now_iso()),
            )
            sequence = int(cursor.lastrowid)
            self._remember_command(connection, request_id, task_id, "message", {"seq": sequence})
            self._event(connection, task_id, "message", {"seq": sequence})
            return {"seq": sequence, "kind": "user", "text": text, **(data or {})}

    def save_run_messages(self, task_id: str, messages: list[dict[str, Any]], *,
                          incomplete: bool = False) -> None:
        """Persist provider-shaped messages while retaining tool-call identity."""
        with self._lock, self._transaction() as connection:
            self._row(connection, task_id)
            if incomplete:
                pending: dict[str, str] = {}
                for message in messages:
                    for call in message.get("tool_calls") or []:
                        pending[str(call.get("id") or "")] = str(call.get("name") or "")
                    if message.get("role") == "tool":
                        pending.pop(str(message.get("tool_call_id") or ""), None)
                messages = [*messages, *[
                    {"role": "tool", "tool_call_id": call_id, "name": name,
                     "content": "No result was recorded: operation outcome is unknown.",
                     "ok": False, "error": "unknown", "arguments": {}}
                    for call_id, name in pending.items()
                ]]
            for message in messages:
                role = message.get("role")
                if role not in ("user", "assistant", "tool", "notice"):
                    continue
                data = {key: message[key] for key in
                        ("tool_calls", "tool_call_id", "name", "arguments", "ok",
                         "error", "thinking", "usage") if message.get(key) is not None}
                connection.execute(
                    "INSERT INTO task_messages(task_id,kind,text,data_json,created_at) VALUES(?,?,?,?,?)",
                    (task_id, role, str(message.get("content") or ""),
                     json.dumps(data, ensure_ascii=False, default=str), now_iso()),
                )
            self._event(connection, task_id, "messages.saved",
                        {"count": len(messages), "incomplete": incomplete})

    def messages_for_model(self, task_id: str, *, include_images: bool = False) -> list[dict[str, Any]]:
        """Legal provider-neutral transcript; unresolved calls get an unknown result."""
        out: list[dict[str, Any]] = []
        pending: dict[str, dict[str, Any]] = {}
        user_answers: dict[str, str] = {}
        for item in self.messages(task_id):
            kind = item.get("kind")
            if kind == "user":
                if item.get("interaction_id"):
                    user_answers[str(item["interaction_id"])] = item.get("text", "")
                else:
                    out.append({"role": "user", "content": message_content(item.get("text", ""), item.get("attachments", []), include_images=include_images)})
            elif kind == "autonomy":
                out.append({"role": "user", "content": message_content("Internal work delegated by Ling within owner scope; not a new user instruction:\n" + item.get("text", ""), item.get("attachments", []), include_images=include_images)})
            elif kind == "assistant":
                calls = item.get("tool_calls") or []
                if item.get("text") or calls:
                    out.append({"role": "assistant", "content": item.get("text", ""),
                                "tool_calls": calls})
                    for call in calls:
                        pending[str(call.get("id") or "")] = call
            elif kind == "tool":
                call_id = str(item.get("tool_call_id") or "")
                pending.pop(call_id, None)
                out.append({"role": "tool", "tool_call_id": call_id,
                            "name": item.get("name", ""), "content": item.get("text", "")})
        for call_id, call in pending.items():
            out.append({"role": "tool", "tool_call_id": call_id,
                        "name": call.get("name", ""),
                        "content": "No result was recorded: outcome requires review."})
        for interaction_id, answer in user_answers.items():
            out.append({"role": "user", "content": answer})
        return out

    def messages(self, task_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            self._row(connection, task_id)
            rows = connection.execute(
                "SELECT seq,kind,text,data_json,created_at FROM task_messages WHERE task_id=? ORDER BY seq",
                (task_id,),
            ).fetchall()
            return [
                {"seq": row["seq"], "kind": row["kind"], "text": row["text"],
                 "created_at": row["created_at"], **json.loads(row["data_json"])}
                for row in rows
            ]
        finally:
            connection.close()

    def set_schedule(self, task_id: str, *, due_at: str, timezone: str, mode: str) -> dict[str, Any]:
        if mode not in ("auto", "confirm"):
            raise TaskValidationError("schedule mode must be auto or confirm")
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            parsed = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("timezone required")
            # The ISO-8601 timestamp already carries the resolved UTC offset,
            # so a display-only IANA label does not need a server tz database.
            # The browser computes the instant and sends the zone it displayed.
            if timezone.upper() not in ("UTC", "GMT") and not timezone.upper().startswith(
                ("UTC+", "UTC-", "GMT+", "GMT-")
            ):
                # Validate the IANA key when timezone data is available. A
                # Windows installation without tzdata can still use the explicit
                # offset from due_at and keeps the display key intact.
                try:
                    zone = ZoneInfo(timezone)
                except Exception as error:
                    raise ValueError(f"IANA timezone data is unavailable for {timezone}") from error
                if parsed.utcoffset() != parsed.astimezone(zone).utcoffset():
                    raise ValueError("due_at offset does not match the selected timezone")
        except Exception as error:
            raise TaskValidationError(f"invalid schedule time or timezone: {error}") from error
        with self._lock, self._transaction() as connection:
            row = self._row(connection, task_id)
            if row["state"] in ("cancelled", "completed"):
                raise TaskConflict("cannot schedule an inactive task")
            if row["archived"]:
                raise TaskConflict("cannot schedule an archived task")
            version = row["schedule_version"] + 1
            stamp = now_iso()
            connection.execute(
                """INSERT INTO schedules(task_id,due_at,timezone,mode,version,status,claimed_at,created_at,updated_at)
                VALUES(?,?,?,?,?,? ,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET due_at=excluded.due_at,timezone=excluded.timezone,
                mode=excluded.mode,version=excluded.version,status='pending',claimed_at=NULL,updated_at=excluded.updated_at""",
                (task_id, due_at, timezone, mode, version, "pending", None, stamp, stamp),
            )
            connection.execute("UPDATE tasks SET schedule_version=?,updated_at=? WHERE id=?",
                               (version, stamp, task_id))
            self._event(connection, task_id, "schedule.set", {"version": version, "mode": mode})
        return {"due_at": due_at, "timezone": timezone, "mode": mode, "version": version, "status": "pending"}

    def schedule(self, task_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            self._row(connection, task_id)
            row = connection.execute("SELECT * FROM schedules WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return None
            return {"due_at": row["due_at"], "timezone": row["timezone"], "mode": row["mode"],
                    "version": row["version"], "status": row["status"]}
        finally:
            connection.close()

    def clear_schedule(self, task_id: str) -> bool:
        with self._lock, self._transaction() as connection:
            self._row(connection, task_id)
            cursor = connection.execute("DELETE FROM schedules WHERE task_id=?", (task_id,))
            if cursor.rowcount:
                self._event(connection, task_id, "schedule.cleared", {})
            return bool(cursor.rowcount)

    def due_schedules(self, now: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT s.*,t.created_at AS task_created FROM schedules s JOIN tasks t ON t.id=s.task_id "
                "WHERE s.status='pending' AND s.due_at<=? AND t.state NOT IN ('cancelled','completed') "
                "AND t.archived=0 ORDER BY s.due_at,t.created_at", (now,)
            ).fetchall()
            return [{key: row[key] for key in row.keys()} for row in rows]
        finally:
            connection.close()

    def claim_schedule(self, task_id: str, *, request_id: str) -> dict[str, Any] | None:
        """Atomically turn one due schedule into a start request."""
        with self._lock, self._transaction() as connection:
            row = connection.execute(
                "SELECT s.*,t.state FROM schedules s JOIN tasks t ON t.id=s.task_id WHERE s.task_id=?",
                (task_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                return None
            if row["state"] in ("cancelled", "completed"):
                return None
            previous = connection.execute("SELECT * FROM commands WHERE request_id=?",
                                          (request_id,)).fetchone()
            start_operation = "queued" if row["mode"] == "auto" else "waiting_user"
            if previous is not None and previous["task_id"] != task_id:
                raise TaskConflict("request id was used for another schedule")
            connection.execute("UPDATE schedules SET status='claimed',claimed_at=?,updated_at=? WHERE task_id=?",
                               (now_iso(), now_iso(), task_id))
            self._event(connection, task_id, "schedule.claimed", {"mode": row["mode"]})
            result = {"task_id": task_id, "mode": row["mode"], "operation": start_operation}
            if previous is None:
                self._remember_command(connection, request_id, task_id, start_operation, result)
            return result

    def complete_schedule(self, task_id: str) -> None:
        with self._lock, self._transaction() as connection:
            connection.execute("UPDATE schedules SET status='consumed',updated_at=? WHERE task_id=?",
                               (now_iso(), task_id))

    def release_schedule(self, task_id: str) -> None:
        """Return a failed reservation to the due set for a later retry."""
        with self._lock, self._transaction() as connection:
            connection.execute(
                "UPDATE schedules SET status='pending',claimed_at=NULL,updated_at=? "
                "WHERE task_id=? AND status='claimed'",
                (now_iso(), task_id),
            )
