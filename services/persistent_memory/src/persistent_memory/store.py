"""SQLite schema, configuration, and transactional version storage."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports (
    session_id TEXT PRIMARY KEY,
    records_json TEXT NOT NULL,
    records_hash TEXT NOT NULL,
    raw_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    records_hash TEXT NOT NULL,
    raw_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    batch_count INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    group_count INTEGER NOT NULL DEFAULT 0,
    edge_count INTEGER NOT NULL DEFAULT 0,
    usage_json TEXT NOT NULL DEFAULT '',
    elapsed_seconds REAL,
    records_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS events (
    version_id TEXT NOT NULL,
    id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    text TEXT NOT NULL,
    at TEXT NOT NULL,
    before TEXT NOT NULL,
    after TEXT NOT NULL,
    uncertainty TEXT NOT NULL,
    source_ids TEXT NOT NULL,
    PRIMARY KEY (version_id, id)
);
CREATE TABLE IF NOT EXISTS groups (
    version_id TEXT NOT NULL,
    id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    dimension TEXT NOT NULL DEFAULT 'context',
    summary TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    event_ids TEXT NOT NULL,
    PRIMARY KEY (version_id, id)
);
CREATE TABLE IF NOT EXISTS edges (
    version_id TEXT NOT NULL,
    id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    reason TEXT NOT NULL,
    source_refs TEXT NOT NULL,
    PRIMARY KEY (version_id, id)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    version_id TEXT NOT NULL,
    query TEXT NOT NULL,
    selected TEXT NOT NULL,
    content TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL,
    feedback TEXT NOT NULL DEFAULT '',
    usage_json TEXT NOT NULL DEFAULT '',
    elapsed_seconds REAL
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "memory.sqlite3"
        self.secret_path = self.data_dir / "secret.json"
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def config(self) -> dict[str, Any]:
        defaults = {
            "base_url": "",
            "model": "",
            "timeout": 20.0,
            "build_timeout": 120.0,
            "build_batch_size": 12,
        }
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM config").fetchall()
        for row in rows:
            defaults[row["key"]] = json.loads(row["value"])
        defaults["api_key"] = self.secret("api_key")
        return defaults

    def secret(self, name: str) -> str:
        if not self.secret_path.exists():
            return ""
        try:
            payload = json.loads(self.secret_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        value = payload.get(name, "") if isinstance(payload, dict) else ""
        return value if isinstance(value, str) else ""

    def configure(self, values: dict[str, Any]) -> dict[str, Any]:
        allowed = {"base_url", "model", "timeout", "build_timeout", "build_batch_size", "api_key"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError("unknown configuration fields: " + ", ".join(sorted(unknown)))
        public = {key: value for key, value in values.items() if key != "api_key"}
        if "base_url" in public:
            public["base_url"] = str(public["base_url"]).strip().rstrip("/")
        if "model" in public:
            public["model"] = str(public["model"]).strip()
        if "timeout" in public:
            public["timeout"] = min(25.0, max(1.0, float(public["timeout"])))
        if "build_timeout" in public:
            public["build_timeout"] = max(1.0, float(public["build_timeout"]))
        if "build_batch_size" in public:
            public["build_batch_size"] = max(1, int(public["build_batch_size"]))
        with self.connect() as conn:
            for key, value in public.items():
                conn.execute(
                    "INSERT INTO config(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
        if "api_key" in values:
            secret = {"api_key": str(values["api_key"])}
            temporary = self.secret_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(secret), encoding="utf-8")
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, self.secret_path)
        return self.config()

    def get_state(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def import_records(
        self, session_id: str, normalized: list[dict[str, Any]], digest: str,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO imports(session_id, records_json, records_hash, raw_count, imported_at) "
                "VALUES(?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "records_json=excluded.records_json, records_hash=excluded.records_hash, "
                "raw_count=excluded.raw_count, imported_at=excluded.imported_at",
                (session_id, json.dumps(normalized, ensure_ascii=False), digest,
                 len(normalized), _now()),
            )
        return {"session_id": session_id, "records_hash": digest, "raw_count": len(normalized)}

    def imported(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM imports WHERE session_id=?", (session_id,)).fetchone()
        if not row:
            return None
        return {
            "session_id": row["session_id"],
            "records": json.loads(row["records_json"]),
            "records_hash": row["records_hash"],
            "raw_count": row["raw_count"],
            "imported_at": row["imported_at"],
        }

    def latest_import(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM imports ORDER BY imported_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return self.imported(row["session_id"]) if row else None

    def versions(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT id, session_id, records_hash, raw_count, status, created_at, error, "
                "batch_count, event_count, group_count, edge_count, usage_json, "
                "elapsed_seconds FROM versions "
                "ORDER BY created_at, id"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["usage"] = json.loads(item.pop("usage_json")) if item["usage_json"] else None
            result.append(item)
        return result

    def version(self, version_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        item["usage"] = json.loads(item.pop("usage_json")) if item["usage_json"] else None
        return item

    def version_records(self, version_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT records_json, raw_count FROM versions WHERE id=?", (version_id,)
            ).fetchone()
        if row is None:
            raise ValueError("version not found")
        return json.loads(row["records_json"])[:row["raw_count"]]

    def create_pending(self, version_id: str, session_id: str, digest: str, count: int) -> None:
        with self.connect() as conn:
            imported = conn.execute(
                "SELECT records_json FROM imports WHERE session_id=?", (session_id,)
            ).fetchone()
            if imported is None:
                raise ValueError("session records were not imported")
            conn.execute(
                "INSERT INTO versions(id, session_id, records_hash, raw_count, status, created_at, "
                "records_json) VALUES(?, ?, ?, ?, 'building', ?, ?)",
                (version_id, session_id, digest, count, _now(), imported["records_json"]),
            )

    def writable(self) -> Iterator[sqlite3.Connection]:
        return self.connect()

    def finish_version(
        self, version_id: str, events: list[dict[str, Any]], groups: list[dict[str, Any]],
        edges: list[dict[str, Any]], batch_count: int, *, usage: Any, elapsed_seconds: float,
    ) -> None:
        with self.connect() as conn:
            for row in events:
                conn.execute(
                    "INSERT INTO events(version_id,id,ordinal,title,summary,text,at,before,after,"
                    "uncertainty,source_ids) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (version_id, row["id"], row["ordinal"], row["title"], row["summary"],
                     row["text"], row["at"], row["before"], row["after"],
                     row["uncertainty"], json.dumps(row["source_ids"], ensure_ascii=False)),
                )
            for row in groups:
                conn.execute(
                    "INSERT INTO groups(version_id,id,ordinal,title,dimension,summary,start_at,"
                    "end_at,event_ids) VALUES(?,?,?,?,?,?,?,?,?)",
                    (version_id, row["id"], row["ordinal"], row["title"],
                     row.get("dimension", "context"), row["summary"],
                     row["start_at"], row["end_at"],
                     json.dumps(row["event_ids"], ensure_ascii=False)),
                )
            for row in edges:
                conn.execute(
                    "INSERT INTO edges(version_id,id,source_id,target_id,relation,reason,"
                    "source_refs) VALUES(?,?,?,?,?,?,?)",
                    (version_id, row["id"], row["source_id"], row["target_id"],
                     row["relation"], row["reason"],
                     json.dumps(row["source_refs"], ensure_ascii=False)),
                )
            conn.execute(
                "UPDATE versions SET status='ready', batch_count=?, event_count=?, "
                "group_count=?, edge_count=?, error='', usage_json=?, elapsed_seconds=? "
                "WHERE id=?",
                (batch_count, len(events), len(groups), len(edges),
                 json.dumps(usage, ensure_ascii=False) if usage is not None else "",
                 elapsed_seconds, version_id),
            )

    def fail_version(self, version_id: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM events WHERE version_id=?", (version_id,))
            conn.execute("DELETE FROM groups WHERE version_id=?", (version_id,))
            conn.execute("DELETE FROM edges WHERE version_id=?", (version_id,))
            conn.execute(
                "UPDATE versions SET status='failed', error=? WHERE id=?", (error, version_id)
            )

    def activate(self, version_id: str) -> None:
        version = self.version(version_id)
        if not version or version["status"] != "ready":
            raise ValueError("version is not complete")
        self.set_state("active_version", version_id)

    def events(self, version_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE version_id=? ORDER BY ordinal, id", (version_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_ids"] = json.loads(item["source_ids"])
            result.append(item)
        return result

    def groups(self, version_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM groups WHERE version_id=? ORDER BY ordinal, id", (version_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["event_ids"] = json.loads(item["event_ids"])
            result.append(item)
        return result

    def edges(self, version_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM edges WHERE version_id=? ORDER BY id", (version_id,)
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["source_refs"] = json.loads(item["source_refs"])
            result.append(item)
        return result

    def save_run(self, row: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO runs(id,version_id,query,selected,content,mode,created_at,feedback,"
                "usage_json,elapsed_seconds) VALUES(?,?,?,?,?,?,?,'',?,?)",
                (row["id"], row["version_id"], row["query"],
                 json.dumps(row["selected"], ensure_ascii=False), row["content"],
                 row["mode"], _now(),
                 json.dumps(row.get("usage"), ensure_ascii=False) if row.get("usage") is not None else "",
                 row.get("elapsed_seconds")),
            )

    def runs(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC, id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["selected"] = json.loads(item["selected"])
            item["usage"] = json.loads(item.pop("usage_json")) if item["usage_json"] else None
            result.append(item)
        return result

    def feedback(self, values: dict[str, Any]) -> dict[str, Any]:
        run_id = str(values.get("run_id", "")).strip()
        if not run_id:
            raise ValueError("run_id is required")
        payload = json.dumps(values, ensure_ascii=False)
        with self.connect() as conn:
            result = conn.execute("UPDATE runs SET feedback=? WHERE id=?", (payload, run_id))
        if not result.rowcount:
            raise ValueError("run not found")
        return {"run_id": run_id, "feedback": values}

    def counts(self) -> dict[str, int]:
        active = self.get_state("active_version")
        if not active:
            return {"events": 0, "groups": 0, "edges": 0, "imports": 0}
        with self.connect() as conn:
            counts = {
                table: conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE version_id=?", (active,)
                ).fetchone()[0]
                for table in ("events", "groups", "edges")
            }
            counts["imports"] = conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        return counts


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
