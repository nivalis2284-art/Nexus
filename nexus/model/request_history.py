"""Seven-day SQLite history of actual model request bodies.

The transport owns when a request becomes real. This store only keeps the
facts passed to it: a copied JSON body, the caller's scope, and the request's
live status. Listing deliberately omits bodies so a history page stays cheap;
opening one request returns the complete body.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_TTL = timedelta(days=7)


class RequestHistory:
    """Thread-safe retained request records without a connection pool.

    Each operation opens its own SQLite connection while a process-wide lock
    serializes in-process writers and readers. ``before`` is the id of the last
    record already seen; the next page contains older ids.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        with self._db() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS model_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    owner_id TEXT,
                    kind TEXT,
                    model TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL,
                    scope TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS model_requests_owner "
                "ON model_requests(owner_id, kind, id)"
            )
        self.cleanup()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    @contextmanager
    def _db(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _cleanup(self, db: sqlite3.Connection) -> sqlite3.Cursor:
        cutoff = (self._now() - _TTL).isoformat(timespec="microseconds")
        return db.execute("DELETE FROM model_requests WHERE created_at < ?", (cutoff,))

    def cleanup(self) -> int:
        """Remove records older than seven UTC days and return the count."""
        with self._lock, self._db() as db:
            cursor = self._cleanup(db)
            return max(0, cursor.rowcount)

    def record(
        self,
        body: Any,
        scope: Mapping[str, Any] | None,
        model: str,
        protocol: str,
        status: str = "sent",
    ) -> int:
        """Persist one detached JSON body and return its id."""
        copied_body = deepcopy(body)
        copied_scope = deepcopy(dict(scope or {}))
        now = self._now().isoformat(timespec="microseconds")
        owner_id = copied_scope.get("owner_id")
        kind = copied_scope.get("kind")
        with self._lock, self._db() as db:
            self._cleanup(db)
            cursor = db.execute(
                """
                INSERT INTO model_requests
                    (created_at, updated_at, owner_id, kind, model, protocol,
                     status, body, scope)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    None if owner_id is None else str(owner_id),
                    None if kind is None else str(kind),
                    str(model),
                    str(protocol),
                    str(status),
                    json.dumps(
                        copied_body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                    ),
                    json.dumps(
                        copied_scope, ensure_ascii=False, separators=(",", ":"), allow_nan=False
                    ),
                ),
            )
            return int(cursor.lastrowid)

    def update(self, id: int, status: str, error: str = "") -> bool:
        """Update the lifecycle state; a missing id returns False."""
        with self._lock, self._db() as db:
            self._cleanup(db)
            cursor = db.execute(
                "UPDATE model_requests SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                (str(status), str(error), self._now().isoformat(timespec="microseconds"), id),
            )
            return cursor.rowcount > 0

    def list(
        self,
        owner_id: str | None = None,
        kind: str | None = None,
        limit: int = 50,
        before: int | None = None,
        plugin_id: str | None = None,
        dialogue_id: str | None = None,
        job_id: str | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        """Return newest-first summaries without request bodies."""
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        clauses: list[str] = []
        params: list[Any] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(str(owner_id))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(str(kind))
        for field, value in (('plugin_id', plugin_id), ('dialogue_id', dialogue_id), ('job_id', job_id), ('purpose', purpose)):
            if value is not None:
                clauses.append("json_extract(scope, '$." + field + "') = ?")
                params.append(str(value))
        if before is not None:
            clauses.append("id < ?")
            params.append(int(before))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit + 1)
        with self._lock, self._db() as db:
            self._cleanup(db)
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                SELECT id, created_at, updated_at, owner_id, kind, model, protocol,
                       status, error, scope
                FROM model_requests {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        requests = [self._summary(row) for row in rows]
        return {"requests": requests, "next": requests[-1]["id"] if more and requests else None}

    def get(self, id: int) -> dict[str, Any] | None:
        """Return one record including its complete JSON body."""
        with self._lock, self._db() as db:
            self._cleanup(db)
            db.row_factory = sqlite3.Row
            row = db.execute(
                """
                SELECT id, created_at, updated_at, owner_id, kind, model, protocol,
                       status, error, body, scope
                FROM model_requests WHERE id = ?
                """,
                (id,),
            ).fetchone()
        if row is None:
            return None
        result = self._summary(row)
        result["body"] = json.loads(row["body"])
        scope = result["scope"]
        result["materials"] = scope.get("materials", scope.get("request_materials", []))
        result["plugins"] = scope.get("plugins", scope.get("request_plugins", []))
        return result

    def delete_owner(self, owner_id: str) -> int:
        """Delete every retained request for one owner and return the count."""
        with self._lock, self._db() as db:
            self._cleanup(db)
            cursor = db.execute(
                "DELETE FROM model_requests WHERE owner_id = ?", (str(owner_id),)
            )
            return max(0, cursor.rowcount)

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "owner_id": row["owner_id"],
            "kind": row["kind"],
            "model": row["model"],
            "protocol": row["protocol"],
            "status": row["status"],
            "error": row["error"],
            "scope": json.loads(row["scope"]),
        }
