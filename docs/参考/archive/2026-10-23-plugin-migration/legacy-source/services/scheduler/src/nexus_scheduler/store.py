"""Durable scheduler rules and delivery state.

This is a focused extraction of the schedule/watch half of Nexus automation.
There are no runs or agent records here: the loop posts wake payloads to Nexus
and only advances a rule after Nexus accepts the callback.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "interval_enabled": True,
    "interval_minutes": 60,
    "planning_enabled": True,
    "planning_time": "09:00",
    "watch_interval_minutes": 5,
}

SCHEDULE_KINDS = ("once", "interval", "daily", "weekly")
SCHEDULE_PURPOSES = ("task", "wake", "planning")
IMPORT_MARKER = "imported"


def now_local() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return _iso(now_local())


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _clock(value: str) -> time:
    return time.fromisoformat(str(value))


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive integer") from error
    if number <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return number


class SchedulerStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._lock, self._db() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    prompt TEXT NOT NULL DEFAULT '',
                    kind TEXT NOT NULL,
                    due_at TEXT,
                    interval_minutes INTEGER,
                    time TEXT,
                    weekdays TEXT NOT NULL DEFAULT '[]',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    notify INTEGER NOT NULL DEFAULT 0,
                    origin TEXT NOT NULL DEFAULT 'agent',
                    purpose TEXT NOT NULL DEFAULT 'task',
                    next_run_at TEXT
                );
                CREATE TABLE IF NOT EXISTS watches (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    url TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL DEFAULT 5,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    origin TEXT NOT NULL DEFAULT 'agent',
                    last_text TEXT,
                    last_checked_at TEXT,
                    next_check_at TEXT,
                    error TEXT
                );
                """
            )
            self._initialize(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self, connection: sqlite3.Connection) -> None:
        first_settings = connection.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
        if first_settings:
            values = {**DEFAULT_SETTINGS, "enabled": False}
            connection.executemany(
                "INSERT INTO settings(key,value) VALUES(?,?)",
                [(key, json.dumps(value)) for key, value in values.items()],
            )
            self._ensure_builtin_schedules(connection, values)
        connection.commit()

    def _ensure_builtin_schedules(
        self, connection: sqlite3.Connection, settings: dict[str, Any]
    ) -> None:
        now = now_local()
        builtins = (
            ("builtin-interval", "Regular check-in", "wake", "interval",
             bool(settings["interval_enabled"]),
             now + timedelta(minutes=settings["interval_minutes"])),
            ("builtin-planning", "Daily planning", "planning", "daily",
             bool(settings["planning_enabled"]),
             self._next_planning(now, settings["planning_time"])),
        )
        for schedule_id, title, purpose, kind, enabled, next_run in builtins:
            existing = connection.execute(
                "SELECT 1 FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            if existing is not None:
                continue
            connection.execute(
                "INSERT INTO schedules(id,title,prompt,kind,due_at,interval_minutes,time,"
                "weekdays,enabled,notify,origin,purpose,next_run_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    schedule_id, title, "", kind, None,
                    60 if kind == "interval" else None,
                    None if kind == "interval" else settings["planning_time"],
                    "[]", int(enabled), 0, "user", purpose, _iso(next_run),
                ),
            )

    @staticmethod
    def _next_planning(now: datetime, planning_time: str) -> datetime:
        target = now.replace(
            hour=_clock(planning_time).hour,
            minute=_clock(planning_time).minute,
            second=0,
            microsecond=0,
        )
        return target if target > now else target + timedelta(days=1)

    @staticmethod
    def _settings(connection: sqlite3.Connection) -> dict[str, Any]:
        values = {key: value for key, value in DEFAULT_SETTINGS.items()}
        for row in connection.execute("SELECT key,value FROM settings"):
            if row["key"] in values:
                values[row["key"]] = json.loads(row["value"])
        return values

    def settings(self) -> dict[str, Any]:
        with self._lock, self._db() as connection:
            return self._settings(connection)

    def configure(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_SETTINGS)
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
        with self._lock, self._db() as connection:
            current = self._settings(connection)
            changed = {**current, **values}
            changed["interval_minutes"] = _positive_int(
                changed["interval_minutes"], "interval_minutes"
            )
            changed["watch_interval_minutes"] = _positive_int(
                changed["watch_interval_minutes"], "watch_interval_minutes"
            )
            changed["planning_time"] = _clock(changed["planning_time"]).strftime("%H:%M")
            changed["enabled"] = bool(changed["enabled"])
            changed["interval_enabled"] = bool(changed["interval_enabled"])
            changed["planning_enabled"] = bool(changed["planning_enabled"])
            for key, value in changed.items():
                connection.execute(
                    "INSERT INTO settings(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value)),
                )
            now = now_local()
            if any(values.get(key) is True for key in ("interval_enabled", "planning_enabled")):
                self._ensure_builtin_schedules(connection, changed)
            interval_changed = any(
                key in values for key in ("interval_enabled", "interval_minutes")
            )
            interval = connection.execute(
                "SELECT * FROM schedules WHERE id='builtin-interval'"
            ).fetchone()
            if interval is not None and interval_changed:
                next_run = self._next_interval(now, changed["interval_minutes"])
                connection.execute(
                    "UPDATE schedules SET interval_minutes=?,enabled=?,next_run_at=? "
                    "WHERE id='builtin-interval'",
                    (
                        changed["interval_minutes"], int(changed["interval_enabled"]),
                        _iso(next_run) if changed["interval_enabled"] else None,
                    ),
                )
            planning_changed = any(
                key in values for key in ("planning_enabled", "planning_time")
            )
            planning = connection.execute(
                "SELECT * FROM schedules WHERE id='builtin-planning'"
            ).fetchone()
            if planning is not None and planning_changed:
                next_run = self._next_planning(now, changed["planning_time"])
                connection.execute(
                    "UPDATE schedules SET time=?,enabled=?,next_run_at=? "
                    "WHERE id='builtin-planning'",
                    (
                        changed["planning_time"], int(changed["planning_enabled"]),
                        _iso(next_run) if changed["planning_enabled"] else None,
                    ),
                )
            connection.commit()
            return changed

    @staticmethod
    def _next_interval(now: datetime, minutes: int) -> datetime:
        return now + timedelta(minutes=minutes)

    @staticmethod
    def _schedule(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "title": row["title"], "prompt": row["prompt"],
            "kind": row["kind"], "due_at": row["due_at"],
            "interval_minutes": row["interval_minutes"], "time": row["time"],
            "weekdays": json.loads(row["weekdays"] or "[]"),
            "enabled": bool(row["enabled"]), "notify": bool(row["notify"]),
            "origin": row["origin"], "purpose": row["purpose"],
            "next_run_at": row["next_run_at"],
        }

    def schedules(self) -> list[dict[str, Any]]:
        with self._lock, self._db() as connection:
            rows = connection.execute("SELECT * FROM schedules ORDER BY next_run_at,id")
            return [self._schedule(row) for row in rows]

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        with self._lock, self._db() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            return self._schedule(row) if row is not None else None

    def schedule_wakeup(
        self, title: str, reason: str, continuation: str, *,
        run_at: str | None = None, delay_minutes: int | None = None,
        id: str | None = None,
    ) -> dict[str, Any]:
        """Save a one-off intention for the agent's future self."""
        title, reason, continuation = title.strip(), reason.strip(), continuation.strip()
        if not title or not reason or not continuation:
            raise ValueError("title, reason and continuation must not be empty")
        if (run_at is None) == (delay_minutes is None):
            raise ValueError("provide exactly one of run_at or delay_minutes")
        now = now_local()
        if run_at is not None:
            due = datetime.fromisoformat(run_at)
            if due.tzinfo is None:
                raise ValueError("run_at must include a UTC offset")
        else:
            if (not isinstance(delay_minutes, int) or isinstance(delay_minutes, bool)
                    or delay_minutes <= 0):
                raise ValueError("delay_minutes must be a positive integer")
            due = now + timedelta(minutes=delay_minutes)
        due = due.replace(microsecond=0)
        if due <= now:
            raise ValueError("wake time must be in the future")
        with self._lock:
            if id is not None:
                existing = self.get_schedule(id)
                if existing is None:
                    raise ValueError("schedule not found; omit id to create a new wake")
                if (id.startswith("builtin-") or
                        (existing["kind"], existing["purpose"], existing["origin"],
                         existing["notify"]) != ("once", "wake", "agent", False)):
                    raise ValueError("only an agent's one-off silent-capable wake can be updated")
            return self.save_schedule({
                "id": id, "title": title,
                "prompt": f"[自主预约理由]\n{reason}\n\n[下次继续]\n{continuation}",
                "kind": "once", "due_at": _iso(due), "origin": "agent",
                "purpose": "wake", "notify": False, "enabled": True,
            })

    def save_schedule(self, values: dict[str, Any]) -> dict[str, Any]:
        values = {key: value for key, value in values.items() if value is not None}
        schedule_id = str(values.get("id") or uuid.uuid4().hex)
        with self._lock, self._db() as connection:
            existing = connection.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            current = self._schedule(existing) if existing is not None else {
                "id": schedule_id, "title": "", "prompt": "", "kind": "once",
                "due_at": None, "interval_minutes": None, "time": None,
                "weekdays": [], "enabled": True, "notify": False,
                "origin": "agent", "purpose": "task", "next_run_at": None,
            }
            merged = {**current, **values, "id": schedule_id}
            kind = merged["kind"]
            if kind not in SCHEDULE_KINDS:
                raise ValueError(f"unsupported schedule kind: {kind}")
            if merged["purpose"] not in SCHEDULE_PURPOSES:
                raise ValueError(f"unsupported schedule purpose: {merged['purpose']}")
            if merged["origin"] not in ("user", "agent"):
                raise ValueError("origin must be user or agent")
            if kind == "interval" or merged["interval_minutes"] is not None:
                merged["interval_minutes"] = _positive_int(
                    merged["interval_minutes"], "interval_minutes"
                )
            if kind in ("daily", "weekly"):
                merged["time"] = _clock(merged["time"]).strftime("%H:%M")
            if kind == "weekly":
                weekdays = merged["weekdays"] or []
                if not isinstance(weekdays, list) or any(
                    not isinstance(day, int) or isinstance(day, bool) or not 0 <= day <= 6
                    for day in weekdays
                ):
                    raise ValueError("weekdays must be a list of integers from 0 to 6")
                merged["weekdays"] = sorted(set(weekdays))
                if not merged["weekdays"]:
                    raise ValueError("weekly schedules need at least one weekday")
            else:
                merged["weekdays"] = []
            if kind == "once":
                raw_due = merged["due_at"]
                due = _parse(raw_due)
                if due is None:
                    raise ValueError("once schedules require due_at")
                if isinstance(raw_due, str) and datetime.fromisoformat(raw_due).tzinfo is None:
                    raise ValueError("once schedules require due_at with a UTC offset")
                merged["due_at"] = _iso(due)
            else:
                merged["due_at"] = None
            merged["enabled"] = bool(merged["enabled"])
            merged["notify"] = bool(merged["notify"])
            merged["next_run_at"] = self._next_run(merged, now_local())
            connection.execute(
                "INSERT INTO schedules(id,title,prompt,kind,due_at,interval_minutes,time,"
                "weekdays,enabled,notify,origin,purpose,next_run_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,prompt=excluded.prompt,"
                "kind=excluded.kind,due_at=excluded.due_at,"
                "interval_minutes=excluded.interval_minutes,time=excluded.time,"
                "weekdays=excluded.weekdays,enabled=excluded.enabled,"
                "notify=excluded.notify,origin=excluded.origin,purpose=excluded.purpose,"
                "next_run_at=excluded.next_run_at",
                (
                    schedule_id, str(merged["title"]), str(merged["prompt"]), kind,
                    merged["due_at"], merged["interval_minutes"], merged["time"],
                    json.dumps(merged["weekdays"]), int(merged["enabled"]),
                    int(merged["notify"]), merged["origin"], merged["purpose"],
                    merged["next_run_at"],
                ),
            )
            self._sync_builtin_settings(connection, schedule_id, merged)
            connection.commit()
            row = connection.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            return self._schedule(row)

    def _sync_builtin_settings(
        self, connection: sqlite3.Connection, schedule_id: str, values: dict[str, Any]
    ) -> None:
        changes: dict[str, Any] = {}
        if schedule_id == "builtin-interval":
            changes = {"interval_enabled": values["enabled"]}
            if values["kind"] == "interval" and values["interval_minutes"] is not None:
                changes["interval_minutes"] = values["interval_minutes"]
        elif schedule_id == "builtin-planning":
            changes = {"planning_enabled": values["enabled"]}
            if values["time"] is not None:
                changes["planning_time"] = values["time"]
        for key, value in changes.items():
            connection.execute(
                "UPDATE settings SET value=? WHERE key=?", (json.dumps(value), key)
            )

    def delete_schedule(self, schedule_id: str) -> bool:
        with self._lock, self._db() as connection:
            cursor = connection.execute("DELETE FROM schedules WHERE id=?", (schedule_id,))
            field = {
                "builtin-interval": "interval_enabled",
                "builtin-planning": "planning_enabled",
            }.get(schedule_id)
            if field:
                connection.execute("UPDATE settings SET value='false' WHERE key=?", (field,))
            connection.commit()
            return bool(cursor.rowcount)

    def _next_run(self, schedule: dict[str, Any], after: datetime) -> str | None:
        if not schedule["enabled"]:
            return None
        kind = schedule["kind"]
        if kind == "once":
            due = _parse(schedule["due_at"])
            return _iso(due) if due is not None else None
        if kind == "interval":
            return _iso(after + timedelta(minutes=int(schedule["interval_minutes"])))
        target_time = _clock(schedule["time"])
        target = after.replace(
            hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0
        )
        if kind == "daily":
            if target <= after:
                target += timedelta(days=1)
            return _iso(target)
        weekdays = schedule["weekdays"]
        if not weekdays:
            return None
        for offset in range(8):
            candidate = target + timedelta(days=offset)
            if candidate > after and candidate.weekday() in weekdays:
                return _iso(candidate)
        return None

    def due_schedules(self, now: str) -> list[dict[str, Any]]:
        current = _parse(now)
        with self._lock, self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_run_at IS NOT NULL "
                "ORDER BY next_run_at,id"
            ).fetchall()
            due = []
            for row in rows:
                next_run = _parse(row["next_run_at"])
                if next_run is not None and next_run <= current:
                    due.append(self._schedule(row))
            return due

    def advance_schedule(
        self, schedule_id: str, now: str, *, expected_next_run_at: str | None = None,
    ) -> dict[str, Any] | None:
        current = _parse(now)
        with self._lock, self._db() as connection:
            row = connection.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            if row is None:
                return None
            schedule = self._schedule(row)
            # An agent can rearm this same ID while its old callback is being
            # accepted. Complete only the occurrence actually sent by tick.
            if (expected_next_run_at is not None
                    and schedule["next_run_at"] != expected_next_run_at):
                return schedule
            if schedule["kind"] == "once":
                connection.execute(
                    "UPDATE schedules SET enabled=0,next_run_at=NULL WHERE id=?", (schedule_id,)
                )
            else:
                schedule["next_run_at"] = None
                next_run = self._next_run(schedule, current)
                connection.execute(
                    "UPDATE schedules SET next_run_at=? WHERE id=?", (next_run, schedule_id)
                )
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM schedules WHERE id=?", (schedule_id,)
            ).fetchone()
            return self._schedule(updated)

    @staticmethod
    def _watch(row: sqlite3.Row, include_text: bool = True) -> dict[str, Any]:
        value = {
            "id": row["id"], "title": row["title"], "url": row["url"],
            "interval_minutes": row["interval_minutes"], "enabled": bool(row["enabled"]),
            "origin": row["origin"], "last_checked_at": row["last_checked_at"],
            "next_check_at": row["next_check_at"], "error": row["error"],
        }
        if include_text:
            value["last_text"] = row["last_text"]
        return value

    def watches(self) -> list[dict[str, Any]]:
        with self._lock, self._db() as connection:
            rows = connection.execute("SELECT * FROM watches ORDER BY title,id")
            return [self._watch(row) for row in rows]

    def get_watch(self, watch_id: str) -> dict[str, Any] | None:
        with self._lock, self._db() as connection:
            row = connection.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
            return self._watch(row) if row is not None else None

    def save_watch(self, values: dict[str, Any]) -> dict[str, Any]:
        values = {key: value for key, value in values.items() if value is not None}
        watch_id = str(values.get("id") or uuid.uuid4().hex)
        with self._lock, self._db() as connection:
            existing = connection.execute(
                "SELECT * FROM watches WHERE id=?", (watch_id,)
            ).fetchone()
            current = self._watch(existing) if existing is not None else {
                "id": watch_id, "title": "", "url": "", "interval_minutes": 5,
                "enabled": True, "origin": "agent", "last_text": None,
                "last_checked_at": None, "next_check_at": None, "error": None,
            }
            merged = {**current, **values, "id": watch_id}
            merged["url"] = str(merged["url"]).strip()
            if not merged["url"]:
                raise ValueError("watch url is required")
            merged["interval_minutes"] = _positive_int(
                merged["interval_minutes"], "interval_minutes"
            )
            if merged["origin"] not in ("user", "agent"):
                raise ValueError("origin must be user or agent")
            merged["enabled"] = bool(merged["enabled"])
            if not merged["enabled"]:
                merged["next_check_at"] = None
            elif "next_check_at" in values:
                next_check = _parse(merged["next_check_at"])
                merged["next_check_at"] = _iso(next_check) if next_check is not None else None
            elif existing is None or (
                "interval_minutes" in values and "next_check_at" not in values
            ):
                merged["next_check_at"] = _iso(
                    now_local() + timedelta(minutes=merged["interval_minutes"])
                )
            if (
                existing is not None and "next_check_at" not in values
                and "interval_minutes" not in values
            ):
                merged["next_check_at"] = current["next_check_at"]
            if merged["url"] != current["url"]:
                merged.update(last_text=None, last_checked_at=None, error=None)
            if merged["enabled"] and (
                existing is None or not current["enabled"]
                or merged["url"] != current["url"]
            ):
                merged["next_check_at"] = _iso(now_local())
            connection.execute(
                "INSERT INTO watches(id,title,url,interval_minutes,enabled,origin,last_text,"
                "last_checked_at,next_check_at,error) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,"
                "interval_minutes=excluded.interval_minutes,enabled=excluded.enabled,"
                "origin=excluded.origin,last_text=excluded.last_text,"
                "last_checked_at=excluded.last_checked_at,"
                "next_check_at=excluded.next_check_at,error=excluded.error",
                (
                    watch_id, str(merged["title"]), merged["url"],
                    merged["interval_minutes"], int(merged["enabled"]), merged["origin"],
                    merged["last_text"], merged["last_checked_at"],
                    merged["next_check_at"], merged["error"],
                ),
            )
            connection.commit()
            row = connection.execute("SELECT * FROM watches WHERE id=?", (watch_id,)).fetchone()
            return self._watch(row)

    def delete_watch(self, watch_id: str) -> bool:
        with self._lock, self._db() as connection:
            cursor = connection.execute("DELETE FROM watches WHERE id=?", (watch_id,))
            connection.commit()
            return bool(cursor.rowcount)

    def due_watches(self, now: str) -> list[dict[str, Any]]:
        current = _parse(now)
        with self._lock, self._db() as connection:
            rows = connection.execute(
                "SELECT * FROM watches WHERE enabled=1 AND next_check_at IS NOT NULL "
                "ORDER BY next_check_at,id"
            ).fetchall()
            due = []
            for row in rows:
                next_check = _parse(row["next_check_at"])
                if next_check is not None and next_check <= current:
                    due.append(self._watch(row))
            return due

    def record_watch(
        self, watch_id: str, text: str | None, error: str | None, now: str
    ) -> dict[str, Any] | None:
        current = _parse(now)
        with self._lock, self._db() as connection:
            row = connection.execute(
                "SELECT * FROM watches WHERE id=?", (watch_id,)
            ).fetchone()
            if row is None:
                return None
            next_check = _iso(current + timedelta(minutes=int(row["interval_minutes"])))
            connection.execute(
                "UPDATE watches SET last_text=COALESCE(?,last_text),last_checked_at=?,"
                "next_check_at=?,error=? WHERE id=?",
                (text, _iso(current), next_check, error, watch_id),
            )
            connection.commit()
            updated = connection.execute(
                "SELECT * FROM watches WHERE id=?", (watch_id,)
            ).fetchone()
            return self._watch(updated)

    def snapshot(self) -> dict[str, Any]:
        with self._lock, self._db() as connection:
            settings = self._settings(connection)
            schedules = [
                self._schedule(row) for row in connection.execute(
                    "SELECT * FROM schedules ORDER BY next_run_at,id"
                )
            ]
            watches = [
                self._watch(row, include_text=False) for row in connection.execute(
                    "SELECT * FROM watches ORDER BY title,id"
                )
            ]
            return {"settings": settings, "schedules": schedules, "watches": watches}

    def export(self) -> dict[str, Any]:
        with self._lock, self._db() as connection:
            return {
                "settings": self._settings(connection),
                "schedules": [
                    self._schedule(row) for row in connection.execute(
                        "SELECT * FROM schedules ORDER BY next_run_at,id"
                    )
                ],
                "watches": [
                    self._watch(row) for row in connection.execute(
                        "SELECT * FROM watches ORDER BY title,id"
                    )
                ],
            }

    def imported(self) -> bool:
        with self._lock, self._db() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key=?", (IMPORT_MARKER,)
            ).fetchone()
            return row is not None and json.loads(row["value"]) is True

    def import_state(self, values: dict[str, Any]) -> bool:
        """Copy an existing automation snapshot once, preserving ids and baselines."""
        with self._lock, self._db() as connection:
            marker = connection.execute(
                "SELECT value FROM meta WHERE key=?", (IMPORT_MARKER,)
            ).fetchone()
            if marker is not None and json.loads(marker["value"]) is True:
                return False
            settings = values.get("settings") or {}
            schedules = values.get("schedules") or []
            watches = values.get("watches") or []
            for key, value in settings.items():
                if key in DEFAULT_SETTINGS:
                    connection.execute(
                        "INSERT INTO settings(key,value) VALUES(?,?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, json.dumps(value)),
                    )
            for item in schedules:
                connection.execute(
                    "INSERT INTO schedules(id,title,prompt,kind,due_at,interval_minutes,time,"
                    "weekdays,enabled,notify,origin,purpose,next_run_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title,"
                    "prompt=excluded.prompt,kind=excluded.kind,due_at=excluded.due_at,"
                    "interval_minutes=excluded.interval_minutes,time=excluded.time,"
                    "weekdays=excluded.weekdays,enabled=excluded.enabled,"
                    "notify=excluded.notify,origin=excluded.origin,purpose=excluded.purpose,"
                    "next_run_at=excluded.next_run_at",
                    (
                        str(item["id"]), str(item.get("title") or ""),
                        str(item.get("prompt") or ""), str(item.get("kind") or "once"),
                        item.get("due_at"), item.get("interval_minutes"),
                        item.get("time"), json.dumps(item.get("weekdays") or []),
                        int(bool(item.get("enabled", True))), int(bool(item.get("notify"))),
                        str(item.get("origin") or "agent"),
                        str(item.get("purpose") or "task"), item.get("next_run_at"),
                    ),
                )
            for item in watches:
                connection.execute(
                    "INSERT INTO watches(id,title,url,interval_minutes,enabled,origin,last_text,"
                    "last_checked_at,next_check_at,error) VALUES(?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,"
                    "interval_minutes=excluded.interval_minutes,enabled=excluded.enabled,"
                    "origin=excluded.origin,last_text=excluded.last_text,"
                    "last_checked_at=excluded.last_checked_at,"
                    "next_check_at=excluded.next_check_at,error=excluded.error",
                    (
                        str(item["id"]), str(item.get("title") or ""),
                        str(item.get("url") or ""), int(item.get("interval_minutes") or 5),
                        int(bool(item.get("enabled", True))),
                        str(item.get("origin") or "agent"), item.get("last_text"),
                        item.get("last_checked_at"), item.get("next_check_at"),
                        item.get("error"),
                    ),
                )
            connection.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (IMPORT_MARKER, json.dumps(True)),
            )
            connection.commit()
            return True
