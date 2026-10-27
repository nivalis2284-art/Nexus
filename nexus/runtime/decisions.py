"""Durable main-conversation requests; answers never block unrelated messages."""

from __future__ import annotations

import json
import uuid

from ..task import TaskConflict, TaskValidationError
from ..task.models import now_iso


class MainDecisions:
    def __init__(self, store, session_id):
        self.store, self.session_id = store, session_id
        with store._lock, store._transaction() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS main_requests (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL,
                payload_json TEXT NOT NULL, status TEXT NOT NULL, answer_json TEXT,
                created_at TEXT NOT NULL)""")
            db.execute("UPDATE main_requests SET status='unknown' WHERE status='dispatching'")

    def pending(self):
        with self.store._transaction() as db:
            return [
                self._value(row)
                for row in db.execute(
                    "SELECT * FROM main_requests WHERE session_id=? AND status IN ('pending','unknown') ORDER BY created_at",
                    (self.session_id,),
                )
            ]

    @staticmethod
    def _value(row):
        value = dict(row)
        value["payload"] = json.loads(value.pop("payload_json"))
        value["answer"] = json.loads(value.pop("answer_json") or "null")
        return value

    def create(self, kind, payload):
        packed = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.store._lock, self.store._transaction() as db:
            row = db.execute(
                "SELECT * FROM main_requests WHERE session_id=? AND kind=? AND payload_json=? AND status='pending'",
                (self.session_id, kind, packed),
            ).fetchone()
            if row is None:
                request_id = "main-" + uuid.uuid4().hex
                db.execute(
                    "INSERT INTO main_requests VALUES(?,?,?,?,?,?,?)",
                    (request_id, self.session_id, kind, packed, "pending", None, now_iso()),
                )
                row = db.execute("SELECT * FROM main_requests WHERE id=?", (request_id,)).fetchone()
            return self._value(row)

    def answer(self, request_id, answer):
        with self.store._lock, self.store._transaction() as db:
            row = db.execute(
                "SELECT * FROM main_requests WHERE id=? AND session_id=?",
                (request_id, self.session_id),
            ).fetchone()
            if row is None:
                raise TaskConflict("request belongs to another conversation")
            if row["kind"] == "approval" and type(answer) is not bool:
                raise TaskValidationError("approval answer must be boolean")
            if row["kind"] == "question" and not (
                isinstance(answer, str)
                or isinstance(answer, list)
                and all(isinstance(x, str) for x in answer)
            ):
                raise TaskValidationError("question answer must be text or a list of text")
            packed = json.dumps(answer, ensure_ascii=False)
            if row["status"] != "pending":
                if row["answer_json"] == packed and row["status"] != "unknown":
                    return self._value(row)
                raise TaskConflict("request already resolved or execution outcome unknown")
            db.execute(
                "UPDATE main_requests SET answer_json=?,status='answered' WHERE id=?",
                (packed, request_id),
            )
            value = self._value(row)
            value.update(answer=answer, status="answered")
            return value

    def review_unknown(self, request_id):
        with self.store._lock, self.store._transaction() as db:
            row = db.execute(
                "SELECT status FROM main_requests WHERE id=? AND session_id=?",
                (request_id, self.session_id),
            ).fetchone()
            if row is None or row["status"] not in ("unknown", "reviewed"):
                raise TaskConflict("only an unknown result can be acknowledged")
            db.execute("UPDATE main_requests SET status='reviewed' WHERE id=?", (request_id,))
        return {"resolved": True, "status": "reviewed"}

    def _matching(self, kind, predicate):
        with self.store._transaction() as db:
            rows = db.execute(
                "SELECT * FROM main_requests WHERE session_id=? AND kind=? AND status='answered' ORDER BY created_at",
                (self.session_id, kind),
            ).fetchall()
            return next((self._value(row) for row in rows if predicate(self._value(row))), None)

    def granted(self, tool, arguments, workspace):
        return (
            self._matching(
                "approval",
                lambda r: (
                    r["answer"] is True
                    and r["payload"]["tool"] == tool
                    and r["payload"]["arguments"] == arguments
                    and r["payload"].get("workspace") == workspace
                ),
            )
            is not None
        )

    def dispatch(self, tool, arguments, workspace):
        with self.store._lock:
            request = self._matching(
                "approval",
                lambda r: (
                    r["answer"] is True
                    and r["payload"]["tool"] == tool
                    and r["payload"]["arguments"] == arguments
                    and r["payload"].get("workspace") == workspace
                ),
            )
            if not request:
                return None
            with self.store._transaction() as db:
                db.execute(
                    "UPDATE main_requests SET status='dispatching' WHERE id=? AND status='answered'",
                    (request["id"],),
                )
            return request["id"]

    def finish(self, request_id, unknown=False):
        if request_id:
            with self.store._lock, self.store._transaction() as db:
                db.execute(
                    "UPDATE main_requests SET status=? WHERE id=?",
                    ("unknown" if unknown else "done", request_id),
                )

    def consume_question(self, questions):
        with self.store._lock:
            request = self._matching("question", lambda r: r["payload"]["questions"] == questions)
            if not request:
                return None
            self.finish(request["id"])
            answer = request["answer"]
            return [answer] if isinstance(answer, str) else answer
