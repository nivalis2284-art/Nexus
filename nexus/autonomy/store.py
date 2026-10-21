"""Owner-controlled autonomy facts in the existing SQLite data directory.

There are no task foreign keys: deleting a work item or replacing an observer
must not erase Ling's reasons, messages or the owner's withdrawal.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..task import TaskConflict, TaskValidationError


def iso(value: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if value is None else value,
                                  UTC).isoformat(timespec="milliseconds")


def uid(prefix: str) -> str:
    return prefix + "_" + uuid.uuid4().hex[:20]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


DEFAULTS = {
    "read_roots": [], "output_root": "", "token_limit": 0,
    "max_output_tokens": 2048, "fallback_seconds": 21600,
    "contact_after_seconds": 86400, "observer_interval": 2,
    "allowed_mcp_tools": [], "min_review_seconds": 30,
    "observer_command": [],
}


class AutonomyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._local = threading.local()
        with self.transaction() as db:
            for statement in (
                "CREATE TABLE IF NOT EXISTS autonomy_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
                """CREATE TABLE IF NOT EXISTS autonomy_records(
                    id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,
                    version INTEGER NOT NULL,data TEXT NOT NULL,at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
                """CREATE TABLE IF NOT EXISTS autonomy_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,source_key TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL,data TEXT NOT NULL,at TEXT NOT NULL,
                    consumed_by TEXT)""",
                """CREATE TABLE IF NOT EXISTS autonomy_reviews(
                    id TEXT PRIMARY KEY,status TEXT NOT NULL,control_version INTEGER NOT NULL,
                    user_revision INTEGER NOT NULL,event_ids TEXT NOT NULL,
                    data TEXT NOT NULL,at TEXT NOT NULL,finished_at TEXT,error TEXT NOT NULL DEFAULT '')""",
                """CREATE TABLE IF NOT EXISTS autonomy_messages(
                    id TEXT PRIMARY KEY,review_id TEXT UNIQUE NOT NULL,text TEXT NOT NULL,
                    sources TEXT NOT NULL,at TEXT NOT NULL,read_at TEXT)""",
                """CREATE TABLE IF NOT EXISTS autonomy_tokens(
                    id TEXT PRIMARY KEY,status TEXT NOT NULL,reserved INTEGER NOT NULL,
                    charged INTEGER NOT NULL DEFAULT 0,usage TEXT,at TEXT NOT NULL)""",
            ):
                db.execute(statement)
            if self.meta("control", db) is None:
                self.set_meta("control", {"state": "disabled", "version": 1,
                    "user_revision": 0, "last_user_at": time.time(),
                    "last_review_at": 0, "retry_at": 0, "error": "", "failure_count": 0}, db)
                self.set_meta("settings", DEFAULTS, db)

    @contextmanager
    def transaction(self):
        with self.lock:
            existing = getattr(self._local, "db", None)
            if existing is not None:
                savepoint = "nested_"+uuid.uuid4().hex
                existing.execute("SAVEPOINT "+savepoint)
                try:
                    yield existing
                    existing.execute("RELEASE "+savepoint)
                except BaseException:
                    existing.execute("ROLLBACK TO "+savepoint)
                    existing.execute("RELEASE "+savepoint)
                    raise
                return
            db = sqlite3.connect(self.path, timeout=20)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=20000")
            try:
                db.execute("BEGIN IMMEDIATE")
                self._local.db = db
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                self._local.db = None
                db.close()

    def meta(self, key, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.meta(key, connection)
        row = db.execute("SELECT value FROM autonomy_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta(self, key, value, db):
        db.execute("INSERT INTO autonomy_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, dumps(value)))

    def settings(self):
        return {**DEFAULTS, **(self.meta("settings") or {})}

    def control(self):
        return self.meta("control")

    def configure(self, values):
        if not isinstance(values, dict) or set(values) - set(DEFAULTS):
            raise TaskValidationError("未知的自主设置")
        settings = {**self.settings(), **values}
        for name, minimum, maximum in (
            ("token_limit", 0, 10**12), ("max_output_tokens", 128, 65536),
            ("fallback_seconds", 1, 365*86400), ("contact_after_seconds", 0, 365*86400),
            ("observer_interval", .1, 3600), ("min_review_seconds", 1, 86400),
        ):
            value = settings[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
                raise TaskValidationError(f"{name} 超出允许范围")
        for name in ("token_limit", "max_output_tokens"):
            if type(settings[name]) is not int:
                raise TaskValidationError(f"{name} 必须是整数")
        roots = settings["read_roots"]
        if not isinstance(roots, list) or len(roots) > 20 or any(not isinstance(x, str) or not x.strip() for x in roots):
            raise TaskValidationError("请选择最多20个明确授权的资料目录")
        paths = [Path(x).expanduser().resolve() for x in roots]
        if any(not p.is_dir() for p in paths):
            raise TaskValidationError("资料目录不存在")
        settings["read_roots"] = list(dict.fromkeys(str(p) for p in paths))
        output = settings["output_root"]
        if not isinstance(output, str):
            raise TaskValidationError("成果目录无效")
        settings["output_root"] = str(Path(output).expanduser().resolve()) if output.strip() else ""
        allowed = settings["allowed_mcp_tools"]
        if not isinstance(allowed, list) or len(allowed) > 100 or any(not isinstance(x, str) or not x.startswith("mcp__") for x in allowed):
            raise TaskValidationError("MCP 白名单必须使用完整工具名")
        command = settings["observer_command"]
        if not isinstance(command, list) or len(command) > 20 or any(not isinstance(x, str) or not x.strip() for x in command):
            raise TaskValidationError("观察器替换命令必须是明确的参数列表，不能使用 shell 字符串")
        with self.transaction() as db:
            ctl = self.meta("control", db)
            # Scope changes invalidate drafts/dispatch, never reset usage.
            ctl["version"] += 1
            self.set_meta("control", ctl, db)
            self.set_meta("settings", settings, db)
        return settings

    def command(self, operation, expected_version=None):
        states = {"enable": "enabled", "pause": "paused", "resume": "enabled", "terminate": "terminated"}
        if operation not in states:
            raise TaskValidationError("未知自主控制命令")
        with self.transaction() as db:
            ctl = self.meta("control", db)
            if expected_version is not None and expected_version != ctl["version"]:
                raise TaskConflict("自主状态已更新，请刷新后重试")
            settings = self.meta("settings", db)
            if operation in ("enable", "resume"):
                if not settings["read_roots"] or not settings["output_root"] or settings["token_limit"] <= 0:
                    raise TaskValidationError("启用前须明确资料范围、成果目录和 Token 额度")
                if operation == "resume" and ctl["state"] != "paused":
                    raise TaskConflict("只能恢复已暂停的自主活动")
                if ctl["state"] == "terminated" and operation == "resume":
                    raise TaskConflict("已终止，请明确重新启用")
            ctl.update(state=states[operation], version=ctl["version"]+1, error="", retry_at=0, failure_count=0)
            self.set_meta("control", ctl, db)
            if operation == "terminate":
                db.execute("UPDATE autonomy_records SET status='cancelled',version=version+1,updated_at=? WHERE kind IN ('plan','action') AND status IN ('pending','active')", (iso(),))
        return ctl

    def note_user(self, text, source_key):
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM autonomy_events WHERE source_key=?", (source_key,)).fetchone():
                return
            ctl = self.meta("control", db)
            ctl.update(user_revision=ctl["user_revision"]+1, last_user_at=time.time())
            self.set_meta("control", ctl, db)
            self.add_event(source_key, "owner_message", {"text": text}, db)

    def add_event(self, key, kind, data, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.add_event(key, kind, data, connection)
        db.execute("INSERT OR IGNORE INTO autonomy_events(source_key,kind,data,at) VALUES(?,?,?,?)",
                   (key, kind, dumps(data), iso()))
        return self.event(db.execute("SELECT seq FROM autonomy_events WHERE source_key=?", (key,)).fetchone()[0], db)

    def event(self, seq, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.event(seq, connection)
        row = db.execute("SELECT * FROM autonomy_events WHERE seq=?", (seq,)).fetchone()
        return {**dict(row), "id": f"event:{row['seq']}", "data": json.loads(row["data"])} if row else None

    def events(self, *, pending=False, limit=100):
        with self.transaction() as db:
            rows = db.execute("SELECT seq FROM autonomy_events " + ("WHERE consumed_by IS NULL " if pending else "") + "ORDER BY seq DESC LIMIT ?", (limit,)).fetchall()
            return [self.event(r[0], db) for r in reversed(rows)]

    def get(self, kind, record_id, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.get(kind, record_id, connection)
        row = db.execute("SELECT * FROM autonomy_records WHERE kind=? AND id=?", (kind, record_id)).fetchone()
        return self._record(row) if row else None

    @staticmethod
    def _record(row):
        return {**json.loads(row["data"]), **{k: row[k] for k in ("id", "kind", "status", "version", "at", "updated_at")}}

    def records(self, kind, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.records(kind, connection)
        return [self._record(r) for r in db.execute("SELECT * FROM autonomy_records WHERE kind=? ORDER BY at,id", (kind,))]

    def put(self, kind, record_id, data, status, db, expected_version=None):
        collision = db.execute("SELECT kind FROM autonomy_records WHERE id=?", (record_id,)).fetchone()
        if collision and collision[0] != kind:
            raise TaskConflict("状态标识已被另一类记录使用")
        old = self.get(kind, record_id, db)
        if expected_version is not None and expected_version != (old["version"] if old else 0):
            raise TaskConflict(f"{kind} 的版本已改变")
        if old and old["status"] == "withdrawn" and status != "withdrawn":
            raise TaskConflict("所有者撤回的事项不能被自主恢复")
        version = old["version"]+1 if old else 1
        db.execute("""INSERT INTO autonomy_records VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET status=excluded.status,version=excluded.version,
            data=excluded.data,updated_at=excluded.updated_at""",
            (record_id, kind, status, version, dumps(data), old["at"] if old else iso(), iso()))
        return self.get(kind, record_id, db)

    def withdraw(self, goal_id, expected_version):
        with self.transaction() as db:
            goal = self.get("goal", goal_id, db)
            if not goal:
                raise TaskValidationError("未找到关注事项")
            self.put("goal", goal_id, goal, "withdrawn", db, expected_version)
            for kind in ("plan", "watch", "action"):
                for item in self.records(kind, db):
                    if item.get("goal_id") == goal_id and item["status"] not in ("done", "cancelled", "stopped"):
                        self.put(kind, item["id"], item, "stopped" if kind == "watch" else "cancelled", db)
            ctl = self.meta("control", db)
            ctl["version"] += 1
            self.set_meta("control", ctl, db)

    def budget(self, db=None):
        if db is None:
            with self.transaction() as connection:
                return self.budget(connection)
        row = db.execute("SELECT COALESCE(SUM(charged),0),COALESCE(SUM(CASE WHEN status='reserved' THEN reserved ELSE 0 END),0),COALESCE(SUM(status='unknown'),0) FROM autonomy_tokens").fetchone()
        limit = self.meta("settings", db)["token_limit"]
        return {"limit": limit, "spent": row[0], "reserved": row[1],
                "remaining": max(0, limit-row[0]-row[1]), "unknown": row[2]}

    def reserve(self, amount):
        with self.transaction() as db:
            if self.meta("control", db)["state"] != "enabled":
                raise PermissionError("自主活动未启用或已暂停")
            if amount > self.budget(db)["remaining"]:
                raise PermissionError("自主 Token 额度不足；请调整总额度或等待所有者处理")
            ticket = uid("tok")
            db.execute("INSERT INTO autonomy_tokens(id,status,reserved,at) VALUES(?,'reserved',?,?)", (ticket, amount, iso()))
            return ticket

    def settle(self, ticket, usage):
        if usage is not None and not isinstance(usage, dict):
            usage = {k: getattr(usage, k, None) for k in ("input_tokens", "output_tokens", "total_tokens")}
        total = usage.get("total_tokens") if usage else None
        if total is None and usage and all(usage.get(k) is not None for k in ("input_tokens", "output_tokens")):
            total = usage["input_tokens"]+usage["output_tokens"]
        with self.transaction() as db:
            row = db.execute("SELECT * FROM autonomy_tokens WHERE id=?", (ticket,)).fetchone()
            if not row or row["status"] != "reserved":
                return
            known = type(total) is int and total >= 0
            db.execute("UPDATE autonomy_tokens SET status=?,charged=?,usage=? WHERE id=?",
                       ("settled" if known else "unknown", total if known else row["reserved"], dumps(usage), ticket))

    def recover(self):
        with self.transaction() as db:
            db.execute("UPDATE autonomy_tokens SET status='unknown',charged=reserved WHERE status='reserved'")
            db.execute("UPDATE autonomy_reviews SET status='interrupted',finished_at=?,error='服务中断；未提交决定未执行' WHERE status='running'", (iso(),))

    def begin_review(self, now=None):
        now = time.time() if now is None else now
        with self.transaction() as db:
            ctl, settings = self.meta("control", db), self.meta("settings", db)
            if ctl["state"] != "enabled" or ctl["retry_at"] > now:
                return None
            if db.execute("SELECT 1 FROM autonomy_reviews WHERE status='running'").fetchone():
                return None
            if now-ctl["last_review_at"] < settings["min_review_seconds"]:
                return None
            pending = db.execute("SELECT seq FROM autonomy_events WHERE consumed_by IS NULL ORDER BY seq LIMIT 100").fetchall()
            if not pending:
                return None
            rid = uid("review")
            db.execute("INSERT INTO autonomy_reviews(id,status,control_version,user_revision,event_ids,data,at) VALUES(?,'running',?,?,?,?,?)",
                       (rid, ctl["version"], ctl["user_revision"], dumps([r[0] for r in pending]), "{}", iso(now)))
            return {"id": rid, "control_version": ctl["version"], "user_revision": ctl["user_revision"],
                    "events": [self.event(r[0], db) for r in pending]}

    def fail_review(self, rid, error, delay=60):
        with self.transaction() as db:
            db.execute("UPDATE autonomy_reviews SET status='interrupted',finished_at=?,error=? WHERE id=? AND status='running'", (iso(), str(error)[:2000], rid))
            ctl = self.meta("control", db)
            if delay >= 60:
                ctl.update(error=str(error)[:2000], retry_at=time.time()+delay)
            elif ctl["state"] == "enabled":
                ctl["retry_at"] = time.time()+delay
            if delay >= 60:
                ctl["failure_count"] = ctl.get("failure_count", 0)+1
                if ctl["failure_count"] >= 3:
                    ctl.update(state="paused", version=ctl["version"]+1,
                               error="连续三轮审议未能提交有效决定，已暂停；"+str(error)[:1000])
            self.set_meta("control", ctl, db)

    def commit_review(self, review, decision):
        """All proposals are checked by the runtime before this atomic commit."""
        with self.transaction() as db:
            ctl = self.meta("control", db)
            row = db.execute("SELECT * FROM autonomy_reviews WHERE id=?", (review["id"],)).fetchone()
            if not row or row["status"] != "running":
                raise TaskConflict("审议已完成或中断")
            if ctl["state"] != "enabled" or ctl["version"] != review["control_version"] or ctl["user_revision"] != review["user_revision"]:
                raise TaskConflict("控制或交流背景改变，请按最新状态重新考虑")
            for kind, name in (("goal", "goals"), ("plan", "plans"), ("watch", "watches")):
                for item in decision.get(name, []):
                    old = self.get(kind, item["id"], db)
                    self.put(kind, item["id"], item, item["status"], db, item.get("expected_version", old["version"] if old else 0))
            ended = {g["id"] for g in self.records("goal", db) if g["status"] in ("completed", "abandoned", "withdrawn")}
            for kind in ("plan", "watch"):
                for item in self.records(kind, db):
                    if item.get("goal_id") in ended and item["status"] in ("pending", "active", "paused"):
                        self.put(kind, item["id"], item, "stopped" if kind == "watch" else "cancelled", db)
            for stop in decision.get("cancel_actions", []):
                action = self.get("action", stop["id"], db)
                if not action:
                    raise TaskConflict("要停止的自主行动不存在")
                self.put("action", stop["id"], {**action, "stop_reason": stop["reason"]}, "cancelled", db, stop["expected_version"])
            for index, action in enumerate(decision.get("actions", [])):
                goal = self.get("goal", action["goal_id"], db)
                if not goal or goal["status"] in ("withdrawn", "completed", "abandoned"):
                    raise TaskConflict("行动对应事项已失效")
                key = action["key"]
                if self.get("action", key, db):
                    continue
                self.put("action", key, {**action, "review_id": review["id"], "goal_version": goal["version"],
                         "scope": self.meta("settings", db)}, "pending", db)
            message = decision.get("message")
            if message and message.get("text"):
                if time.time()-ctl["last_user_at"] < self.meta("settings", db)["contact_after_seconds"]:
                    raise TaskConflict("所有者刚联系过；请重新判断主动交流时机")
                # A repeated exact message is retained as a silent decision.
                duplicate = db.execute("SELECT 1 FROM autonomy_messages WHERE text=?", (message["text"],)).fetchone()
                if not duplicate:
                    db.execute("INSERT INTO autonomy_messages VALUES(?,?,?,?,?,NULL)",
                               (uid("msg"), review["id"], message["text"], dumps(message["sources"]), iso()))
            for seq in json.loads(row["event_ids"]):
                db.execute("UPDATE autonomy_events SET consumed_by=? WHERE seq=? AND consumed_by IS NULL", (review["id"], seq))
            db.execute("UPDATE autonomy_reviews SET status='done',data=?,finished_at=? WHERE id=?", (dumps(decision), iso(), review["id"]))
            ctl.update(last_review_at=time.time(), error="", retry_at=0, failure_count=0)
            self.set_meta("control", ctl, db)

    def messages(self):
        with self.transaction() as db:
            return [{**dict(r), "sources": json.loads(r["sources"])} for r in db.execute("SELECT * FROM autonomy_messages ORDER BY at,id")]

    def read_message(self, message_id):
        with self.transaction() as db:
            db.execute("UPDATE autonomy_messages SET read_at=COALESCE(read_at,?) WHERE id=?", (iso(), message_id))

    def snapshot(self):
        with self.transaction() as db:
            reviews = [{"id": r["id"], "at": r["at"], "status": r["status"],
                        "error": r["error"], **json.loads(r["data"])}
                       for r in db.execute("SELECT * FROM autonomy_reviews ORDER BY at DESC LIMIT 40")]
            return {"control": self.meta("control", db), "settings": self.meta("settings", db),
                    "budget": self.budget(db), "goals": self.records("goal", db),
                    "plans": self.records("plan", db), "watches": self.records("watch", db),
                    "actions": self.records("action", db), "decisions": reviews,
                    "requests": self.records("request", db)}
