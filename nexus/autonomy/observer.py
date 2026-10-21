"""Dedicated local-text observation MCP service.

This module deliberately uses the small, hand-written stdio protocol already
used by Nexus instead of making an MCP SDK a new runtime dependency.  The
service owns only observation state: rules, baselines, durable events, and
bounded content evidence.  It does not own goals, messages, permissions, or
the Nexus task store.

Run it as::

    python -m nexus.autonomy.observer --data PATH --roots-json JSON

``roots-json`` is a JSON array of absolute paths explicitly authorized by the
owner.  The process holds an exclusive lock on its data directory, reads stdin
as a newline-delimited MCP stream, and stops scanning on stdin EOF.  Collection
also requires a fresh lease from the Nexus-side client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
CONTRACT_VERSION = "1"
IMPLEMENTATION = "nexus-local-text-observer/1"
SCHEMA_VERSION = 1

ALLOWED_SUFFIXES = frozenset({".txt", ".md", ".markdown"})
DEFAULT_INTERVAL = 1.0
MIN_INTERVAL = 0.1
MAX_INTERVAL = 3600.0
MIN_LEASE_TTL = 0.1
MAX_LEASE_TTL = 300.0
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_FILES_PER_WATCH = 512
MAX_WATCHES = 64
MAX_ROOTS = 32
MAX_DIRECTORIES_PER_SCAN = 1024
MAX_DIRECTORY_ENTRIES_PER_SCAN = 8192
MAX_EVENTS_PAGE = 200
MAX_EVENTS_PAGE_BYTES = 512 * 1024
MAX_REQUESTS = 4096
DEFAULT_STORAGE_QUOTA = 64 * 1024 * 1024
SCAN_TICK = 0.05


class ObserverError(RuntimeError):
    """A request cannot be applied or a source cannot be observed."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


class _SourceMissing(ObserverError):
    pass


class _SourceUnstable(ObserverError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_int(value: Any) -> bool:
    return type(value) is int


def _positive_int(value: Any, name: str, *, maximum: int = 2**63 - 1) -> int:
    if not _is_int(value) or value <= 0 or value > maximum:
        raise ObserverError("invalid_arguments", f"{name} must be a positive integer")
    return value


def _non_negative_int(value: Any, name: str) -> int:
    if not _is_int(value) or value < 0:
        raise ObserverError("invalid_arguments", f"{name} must be a non-negative integer")
    return value


def _text(value: Any, name: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ObserverError("invalid_arguments", f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > maximum:
        raise ObserverError("invalid_arguments", f"{name} is too long")
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ObserverError("invalid_arguments", f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ObserverError("invalid_arguments", f"{name} must be finite")
    return result


def _canonical_args(args: dict[str, Any]) -> str:
    return _dumps(args)


def _bounded_utf8(raw: bytes) -> tuple[str, bool]:
    """Decode strict UTF-8 and retain a valid prefix no larger than the limit."""
    text = raw.decode("utf-8")
    if len(raw) <= MAX_EVIDENCE_BYTES:
        return text, False
    prefix = raw[:MAX_EVIDENCE_BYTES]
    for cut in range(4):
        try:
            return prefix[: len(prefix) - cut].decode("utf-8"), True
        except UnicodeDecodeError:
            continue
    # The full decode above established that the source is valid UTF-8.  A
    # four-byte cut is sufficient for every UTF-8 sequence boundary.
    return text, True


class AuthorizedRoots:
    """Resolve and enforce the owner-supplied filesystem boundary."""

    def __init__(self, roots: list[str]) -> None:
        if not isinstance(roots, list) or not roots:
            raise ObserverError("invalid_roots", "roots must be a non-empty JSON array")
        if len(roots) > MAX_ROOTS:
            raise ObserverError("invalid_roots", f"at most {MAX_ROOTS} roots are allowed")
        resolved: list[Path] = []
        seen: set[str] = set()
        for raw in roots:
            if not isinstance(raw, str) or not raw.strip():
                raise ObserverError("invalid_roots", "every root must be a non-empty string")
            if len(raw) > 4096:
                raise ObserverError("invalid_roots", "root path is too long")
            path = Path(raw).expanduser()
            if not path.is_absolute():
                raise ObserverError("invalid_roots", f"root is not absolute: {raw}")
            try:
                path = path.resolve(strict=True)
            except OSError as error:
                raise ObserverError("invalid_roots", f"cannot resolve root {raw}: {error}") from error
            if not (path.is_dir() or (path.is_file() and path.suffix.casefold() in ALLOWED_SUFFIXES)):
                raise ObserverError(
                    "invalid_roots",
                    f"root must be a directory or an allowed text file: {raw}",
                )
            key = os.path.normcase(str(path))
            if key not in seen:
                seen.add(key)
                resolved.append(path)
        self.paths = tuple(resolved)

    def contains(self, path: Path) -> bool:
        try:
            candidate = path.resolve(strict=False)
        except OSError:
            return False
        candidate_text = os.path.normcase(str(candidate))
        for root in self.paths:
            root_text = os.path.normcase(str(root))
            if root.is_file():
                if candidate_text == root_text:
                    return True
                continue
            if candidate_text == root_text:
                return True
            prefix = root_text.rstrip(os.sep) + os.sep
            if candidate_text.startswith(prefix):
                return True
        return False

    def resolve_watch_path(self, raw: str) -> Path:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ObserverError("out_of_scope", "watch path must be absolute")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ObserverError("source_missing", f"watch path is not readable: {error}") from error
        if not self.contains(resolved):
            raise ObserverError("out_of_scope", "watch path is outside the authorized roots")
        if resolved.is_file() and resolved.suffix.casefold() not in ALLOWED_SUFFIXES:
            raise ObserverError("unsupported_file", "only .txt, .md, and .markdown are supported")
        if not (resolved.is_file() or resolved.is_dir()):
            raise ObserverError("unsupported_path", "watch path must be a file or directory")
        return resolved

    def as_strings(self) -> list[str]:
        return [str(path) for path in self.paths]


class _DirectoryOwner:
    """Exclusive, crash-safe ownership of one observer data directory."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "observer.lock"
        self.handle = self.path.open("a+b")
        self._locked = False
        try:
            self.handle.seek(0, os.SEEK_END)
            if self.handle.tell() == 0:
                self.handle.write(b"\0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise ObserverError(
                        "owner_conflict", "another observer process owns this data directory"
                    ) from error
            else:
                import fcntl

                try:
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise ObserverError(
                        "owner_conflict", "another observer process owns this data directory"
                    ) from error
            self._locked = True
        except BaseException:
            self.handle.close()
            raise

    def release(self) -> None:
        if not self._locked:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                try:
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            self._locked = False
            self.handle.close()


class ObserverStore:
    """SQLite authority for rules, baselines, requests, and event evidence."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "observer.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

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

    def _guarded_scan(self, connection: sqlite3.Connection) -> None:
        """Reject a snapshot if control or lease changed during collection."""
        rows = connection.execute(
            "SELECT key,value FROM meta "
            "WHERE key IN ('collection_active','lease_expires_at')"
        ).fetchall()
        values = {str(item[0]): str(item[1]) for item in rows}
        expiry = _parse_time(values.get("lease_expires_at"))
        if (
            values.get("collection_active") != "1"
            or expiry is None
            or expiry <= datetime.now(UTC)
        ):
            raise ObserverError("lease_expired", "collection stopped before the scan committed")

    def _initialize(self) -> None:
        with self._transaction() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise ObserverError(
                    "schema_too_new", f"observer database version {version} is newer than supported"
                )
            statements = (
                """
                CREATE TABLE IF NOT EXISTS meta (
                  key TEXT PRIMARY KEY, value TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS watches (
                  watch_id TEXT PRIMARY KEY,
                  path TEXT NOT NULL, resolved_path TEXT NOT NULL,
                  recursive INTEGER NOT NULL DEFAULT 0, interval REAL NOT NULL,
                  spec_version INTEGER NOT NULL, control_version INTEGER NOT NULL,
                  status TEXT NOT NULL, baseline_done INTEGER NOT NULL DEFAULT 0,
                  gap_pending INTEGER NOT NULL DEFAULT 0, gap_started_at TEXT,
                  gap_reason TEXT NOT NULL DEFAULT '', last_scan_at TEXT,
                  last_error TEXT NOT NULL DEFAULT '', tombstone INTEGER NOT NULL DEFAULT 0,
                  stopped_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS baselines (
                  watch_id TEXT NOT NULL, path TEXT NOT NULL,
                  hash TEXT NOT NULL, content TEXT NOT NULL,
                  content_bytes INTEGER NOT NULL, truncated INTEGER NOT NULL DEFAULT 0,
                  size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, observed_at TEXT NOT NULL,
                  PRIMARY KEY (watch_id, path)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS events (
                  seq INTEGER PRIMARY KEY AUTOINCREMENT, epoch TEXT NOT NULL,
                  event_id TEXT NOT NULL UNIQUE, watch_id TEXT NOT NULL,
                  spec_version INTEGER NOT NULL, kind TEXT NOT NULL,
                  observed_at TEXT NOT NULL, path TEXT NOT NULL, content TEXT,
                  hash TEXT, previous_hash TEXT, size INTEGER,
                  truncated INTEGER NOT NULL DEFAULT 0, details_json TEXT NOT NULL DEFAULT '{}'
                )
                """,
                "CREATE INDEX IF NOT EXISTS events_epoch_seq ON events(epoch, seq)",
                """
                CREATE TABLE IF NOT EXISTS requests (
                  request_id TEXT PRIMARY KEY, tool TEXT NOT NULL,
                  args_hash TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
                )
                """,
            )
            for statement in statements:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            if self._meta(connection, "epoch") is None:
                self._set_meta(connection, "epoch", uuid.uuid4().hex)
            if self._meta(connection, "implementation") is None:
                self._set_meta(connection, "implementation", IMPLEMENTATION)
            if self._meta(connection, "last_control_version") is None:
                self._set_meta(connection, "last_control_version", "0")
            if self._meta(connection, "collection_active") is None:
                self._set_meta(connection, "collection_active", "0")

    @staticmethod
    def _meta(connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row is not None else None

    @staticmethod
    def _set_meta(connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def identity(self) -> tuple[str, str]:
        with self._transaction() as connection:
            epoch = self._meta(connection, "epoch") or uuid.uuid4().hex
            implementation = self._meta(connection, "implementation") or IMPLEMENTATION
            self._set_meta(connection, "epoch", epoch)
            self._set_meta(connection, "implementation", implementation)
        return epoch, implementation

    def replace_implementation(self, new_epoch: str) -> None:
        with self._transaction() as connection:
            old = self._meta(connection, "implementation") or ""
            self._set_meta(connection, "epoch", new_epoch)
            self._set_meta(connection, "implementation", IMPLEMENTATION)
            self._set_meta(connection, "replaced_from", old)
            self._set_meta(connection, "replacement_pending_gap", "1")
            self._mark_gap(connection, "implementation_replaced", _now())

    @staticmethod
    def _mark_gap(connection: sqlite3.Connection, reason: str, started_at: str) -> None:
        connection.execute(
            "UPDATE watches SET gap_pending=1, gap_started_at=COALESCE(gap_started_at, ?), "
            "gap_reason=CASE WHEN gap_pending=0 THEN ? ELSE gap_reason END "
            "WHERE status='active' AND baseline_done=1 AND tombstone=0",
            (started_at, reason),
        )

    def recover_startup(self) -> None:
        with self._transaction() as connection:
            active = self._meta(connection, "collection_active") == "1"
            if active:
                self._set_meta(connection, "collection_active", "0")
                self._set_meta(connection, "collection_stop_reason", "process_restart")
                self._set_meta(connection, "collection_stopped_at", _now())
                self._mark_gap(connection, "process_restart", _now())
            if self._meta(connection, "replacement_pending_gap") == "1":
                self._set_meta(connection, "replacement_pending_gap", "0")
                self._mark_gap(connection, "implementation_replaced", _now())

    def stop_collection(self, reason: str) -> None:
        with self._transaction() as connection:
            if self._meta(connection, "collection_active") == "1":
                self._set_meta(connection, "collection_active", "0")
                self._set_meta(connection, "collection_stop_reason", reason)
                self._set_meta(connection, "collection_stopped_at", _now())
                self._mark_gap(connection, reason, _now())

    def expire_collection(self, reason: str) -> None:
        with self._transaction() as connection:
            self._set_meta(connection, "collection_active", "0")
            self._set_meta(connection, "collection_stop_reason", reason)
            self._set_meta(connection, "collection_stopped_at", _now())
            self._mark_gap(connection, reason, _now())

    def start_collection(self, control_version: int, expires_at: str, ttl: float) -> dict[str, Any]:
        with self._transaction() as connection:
            last_control = int(self._meta(connection, "last_control_version") or "0")
            if control_version < last_control:
                raise ObserverError(
                    "stale_control_version",
                    f"control_version {control_version} is older than applied {last_control}",
                )
            self._set_meta(connection, "last_control_version", str(control_version))
            self._set_meta(connection, "lease_control_version", str(control_version))
            self._set_meta(connection, "lease_expires_at", expires_at)
            self._set_meta(connection, "collection_active", "1")
            self._set_meta(connection, "collection_stop_reason", "")
            self._set_meta(connection, "collection_stopped_at", "")
            connection.execute(
                "UPDATE watches SET status='active',updated_at=? "
                "WHERE status='pending_lease' AND tombstone=0",
                (_now(),),
            )
        return {
            "control_version": control_version,
            "expires_at": expires_at,
            "ttl": ttl,
        }

    def lease(self) -> tuple[str | None, bool]:
        with self._connect() as connection:
            expires = self._meta(connection, "lease_expires_at")
            active = self._meta(connection, "collection_active") == "1"
        return expires, active

    def storage_used(self) -> int:
        with self._connect() as connection:
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            total = page_count * page_size
        for suffix in ("-wal", "-shm"):
            try:
                total += (self.path.parent / (self.path.name + suffix)).stat().st_size
            except OSError:
                pass
        return total

    def watch_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM watches").fetchone()[0])

    def get_watch(self, watch_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
        return self._watch_dict(row) if row is not None else None

    def list_watches(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM watches ORDER BY created_at, watch_id"
            ).fetchall()
        return [self._watch_dict(row) for row in rows]

    @staticmethod
    def _watch_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "watch_id": row["watch_id"],
            "path": row["path"],
            "resolved_path": row["resolved_path"],
            "recursive": bool(row["recursive"]),
            "interval": float(row["interval"]),
            "spec_version": int(row["spec_version"]),
            "control_version": int(row["control_version"]),
            "status": row["status"],
            "baseline_done": bool(row["baseline_done"]),
            "last_scan_at": row["last_scan_at"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def baseline_rows(self, watch_id: str) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM baselines WHERE watch_id=? ORDER BY path", (watch_id,)
            ).fetchall()

    def request_result(self, request_id: str, tool: str, args_hash: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT tool,args_hash,result_json FROM requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        if row["tool"] != tool or row["args_hash"] != args_hash:
            raise ObserverError(
                "request_conflict", "request_id was already used for a different request"
            )
        try:
            result = json.loads(row["result_json"])
        except ValueError as error:
            raise ObserverError("corrupt_request", "stored request result is not JSON") from error
        if not isinstance(result, dict):
            raise ObserverError("corrupt_request", "stored request result is not an object")
        return result

    def put_request(self, request_id: str, tool: str, args_hash: str, result: dict[str, Any]) -> None:
        with self._transaction() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM requests").fetchone()[0])
            if count >= MAX_REQUESTS:
                oldest = connection.execute(
                    "SELECT request_id FROM requests ORDER BY created_at,request_id LIMIT 1"
                ).fetchone()
                if oldest is not None:
                    connection.execute("DELETE FROM requests WHERE request_id=?", (oldest[0],))
            connection.execute(
                "INSERT INTO requests(request_id,tool,args_hash,result_json,created_at) "
                "VALUES(?,?,?,?,?)",
                (request_id, tool, args_hash, _dumps(result), _now()),
            )

    def watch_put(self, watch_id: str, path: Path, recursive: bool, interval: float,
                  spec_version: int, control_version: int, lease_active: bool,
                  paused: bool = False) -> tuple[dict[str, Any], bool]:
        now = _now()
        reset_baseline = False
        with self._transaction() as connection:
            last_control = int(self._meta(connection, "last_control_version") or "0")
            if control_version < last_control:
                raise ObserverError(
                    "stale_control_version",
                    f"control_version {control_version} is older than applied {last_control}",
                )
            row = connection.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
            if row is None:
                count = int(connection.execute(
                    "SELECT COUNT(*) FROM watches WHERE status != 'stopped'"
                ).fetchone()[0])
                if count >= MAX_WATCHES:
                    raise ObserverError("watch_limit", f"at most {MAX_WATCHES} watches are allowed")
                status = "paused" if paused else ("active" if lease_active else "pending_lease")
                connection.execute(
                    "INSERT INTO watches(watch_id,path,resolved_path,recursive,interval,"
                    "spec_version,control_version,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (watch_id, str(path), str(path), int(recursive), interval,
                     spec_version, control_version, status, now, now),
                )
                reset_baseline = True
            else:
                if bool(row["tombstone"]) or row["status"] == "stopped":
                    raise ObserverError("watch_stopped", "a stopped watch is a tombstone")
                if spec_version < int(row["spec_version"]):
                    raise ObserverError(
                        "stale_spec_version",
                        f"spec_version {spec_version} is older than applied {row['spec_version']}",
                    )
                same_spec = (
                    str(path) == row["resolved_path"]
                    and int(recursive) == int(row["recursive"])
                    and abs(interval - float(row["interval"])) < 1e-9
                )
                if spec_version == int(row["spec_version"]) and not same_spec:
                    raise ObserverError(
                        "spec_conflict", "spec_version already names a different watch specification"
                    )
                reset_baseline = spec_version > int(row["spec_version"]) and (
                    str(path) != row["resolved_path"] or int(recursive) != int(row["recursive"])
                )
                status = row["status"]
                if status == "pending_lease" and lease_active:
                    status = "active"
                if spec_version == int(row["spec_version"]) and reset_baseline is False:
                    # Replaying the same semantic rule with a newer control
                    # version must not erase an active pause.
                    status = row["status"]
                connection.execute(
                    "UPDATE watches SET path=?,resolved_path=?,recursive=?,interval=?,"
                    "spec_version=?,control_version=?,status=?,updated_at=? WHERE watch_id=?",
                    (str(path), str(path), int(recursive), interval, spec_version,
                     control_version, status, now, watch_id),
                )
                if reset_baseline:
                    connection.execute("DELETE FROM baselines WHERE watch_id=?", (watch_id,))
                    connection.execute(
                        "UPDATE watches SET baseline_done=0,gap_pending=0,gap_started_at=NULL,"
                        "gap_reason='',last_error='' WHERE watch_id=?", (watch_id,),
                    )
            self._set_meta(connection, "last_control_version", str(control_version))
        watch = self.get_watch(watch_id)
        if watch is None:
            raise ObserverError("internal_error", "watch disappeared after write")
        return watch, reset_baseline

    def watch_control(self, watch_id: str, operation: str, spec_version: int,
                      control_version: int, lease_active: bool) -> dict[str, Any]:
        now = _now()
        with self._transaction() as connection:
            last_control = int(self._meta(connection, "last_control_version") or "0")
            if control_version < last_control:
                raise ObserverError(
                    "stale_control_version",
                    f"control_version {control_version} is older than applied {last_control}",
                )
            row = connection.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
            if row is None:
                raise ObserverError("watch_not_found", f"unknown watch: {watch_id}")
            if spec_version != int(row["spec_version"]):
                raise ObserverError(
                    "stale_spec_version",
                    f"expected spec_version {row['spec_version']}, got {spec_version}",
                )
            status = row["status"]
            if status == "stopped":
                if operation == "stop":
                    return self._watch_dict(row)
                raise ObserverError("watch_stopped", "a stopped watch is a tombstone")
            if operation == "pause":
                if status != "paused":
                    connection.execute(
                        "UPDATE watches SET status='paused',gap_pending=1,"
                        "gap_started_at=COALESCE(gap_started_at,?),gap_reason='watch_paused',"
                        "control_version=?,updated_at=? WHERE watch_id=?",
                        (now, control_version, now, watch_id),
                    )
                else:
                    connection.execute(
                        "UPDATE watches SET control_version=?,updated_at=? WHERE watch_id=?",
                        (control_version, now, watch_id),
                    )
            elif operation == "resume":
                if status != "paused":
                    raise ObserverError(
                        "watch_not_paused", "only a paused watch can be explicitly resumed"
                    )
                new_status = "active" if lease_active else "pending_lease"
                connection.execute(
                    "UPDATE watches SET status=?,control_version=?,updated_at=? WHERE watch_id=?",
                    (new_status, control_version, now, watch_id),
                )
            elif operation == "stop":
                connection.execute(
                    "UPDATE watches SET status='stopped',tombstone=1,stopped_at=?,gap_pending=0,"
                    "gap_started_at=NULL,gap_reason='',control_version=?,updated_at=? "
                    "WHERE watch_id=?",
                    (now, control_version, now, watch_id),
                )
            else:
                raise ObserverError("invalid_arguments", f"unknown operation: {operation}")
            self._set_meta(connection, "last_control_version", str(control_version))
        watch = self.get_watch(watch_id)
        if watch is None:
            raise ObserverError("internal_error", "watch disappeared after control")
        return watch

    def begin_scan(self, watch_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
        return self._watch_dict(row) if row is not None else None

    def scan_state(self, watch_id: str) -> tuple[bool, bool, str, str | None]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT baseline_done,gap_pending,gap_reason,gap_started_at "
                "FROM watches WHERE watch_id=?", (watch_id,),
            ).fetchone()
        if row is None:
            return False, False, "", None
        return bool(row[0]), bool(row[1]), str(row[2] or ""), row[3]

    def current_epoch(self) -> str:
        with self._connect() as connection:
            return self._meta(connection, "epoch") or ""

    def insert_scan(self, watch: dict[str, Any], epoch: str, events: list[dict[str, Any]],
                    upserts: list[dict[str, Any]], deletes: list[str],
                    baseline_done: bool, last_error: str, scanned_at: str) -> list[dict[str, Any]]:
        inserted: list[dict[str, Any]] = []
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT status,spec_version,path,tombstone FROM watches WHERE watch_id=?",
                (watch["watch_id"],),
            ).fetchone()
            if (
                current is None
                or current["status"] != "active"
                or bool(current["tombstone"])
                or int(current["spec_version"]) != int(watch["spec_version"])
                or current["path"] != watch["path"]
            ):
                raise ObserverError(
                    "control_changed", "watch changed while the snapshot was being collected"
                )
            self._guarded_scan(connection)
            for event in events:
                cursor = connection.execute(
                    "INSERT INTO events(epoch,event_id,watch_id,spec_version,kind,observed_at,"
                    "path,content,hash,previous_hash,size,truncated,details_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (epoch, event["event_id"], watch["watch_id"], watch["spec_version"],
                     event["kind"], event["observed_at"], event["path"], event.get("content"),
                     event.get("hash"), event.get("previous_hash"), event.get("size"),
                     int(bool(event.get("truncated"))), _dumps(event.get("details") or {})),
                )
                item = dict(event)
                item["seq"] = int(cursor.lastrowid)
                inserted.append(item)
            for item in upserts:
                connection.execute(
                    "INSERT INTO baselines(watch_id,path,hash,content,content_bytes,truncated,"
                    "size,mtime_ns,observed_at) VALUES(?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(watch_id,path) DO UPDATE SET hash=excluded.hash,"
                    "content=excluded.content,content_bytes=excluded.content_bytes,"
                    "truncated=excluded.truncated,size=excluded.size,mtime_ns=excluded.mtime_ns,"
                    "observed_at=excluded.observed_at",
                    (watch["watch_id"], item["path"], item["hash"], item["content"],
                     item["content_bytes"], int(bool(item["truncated"])), item["size"],
                     item["mtime_ns"], scanned_at),
                )
            for path in deletes:
                connection.execute(
                    "DELETE FROM baselines WHERE watch_id=? AND path=?", (watch["watch_id"], path)
                )
            connection.execute(
                "UPDATE watches SET baseline_done=?,last_scan_at=?,last_error=?,gap_pending=0,"
                "gap_started_at=NULL,gap_reason='',updated_at=? WHERE watch_id=?",
                (int(baseline_done), scanned_at, last_error, scanned_at, watch["watch_id"]),
            )
        return inserted

    def read_events(self, epoch: str, after: int, limit: int) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE epoch=? AND seq>? ORDER BY seq LIMIT ?",
                (epoch, after, limit + 1),
            ).fetchall()
            earliest = connection.execute(
                "SELECT MIN(seq) FROM events WHERE epoch=?", (epoch,)
            ).fetchone()[0]
            latest = connection.execute(
                "SELECT MAX(seq) FROM events WHERE epoch=?", (epoch,)
            ).fetchone()[0]
        events: list[dict[str, Any]] = []
        used = 0
        for row in rows[:limit]:
            item = self._event_dict(row)
            size = len(_dumps(item).encode("utf-8"))
            if events and used + size > MAX_EVENTS_PAGE_BYTES:
                break
            events.append(item)
            used += size
        has_more = len(rows) > len(events)
        next_cursor = events[-1]["seq"] if events else after
        gap: dict[str, Any] | None = None
        if earliest is not None and after < int(earliest) - 1:
            gap = {
                "reason": "retention_gap",
                "from_seq": after + 1,
                "to_seq": int(earliest) - 1,
            }
        if gap is None:
            for item in events:
                if item["kind"] == "gap":
                    details = item.get("details") or {}
                    gap = {
                        "reason": str(details.get("reason") or "coverage_gap"),
                        "from_seq": item["seq"],
                        "to_seq": item["seq"],
                    }
                    break
        return {
            "epoch": epoch,
            "events": events,
            "next_cursor": next_cursor,
            "has_more": has_more,
            "gap": gap,
            "earliest_seq": int(earliest) if earliest is not None else None,
            "latest_seq": int(latest) if latest is not None else None,
        }

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        try:
            details = json.loads(row["details_json"] or "{}")
        except ValueError:
            details = {}
        return {
            "seq": int(row["seq"]),
            "event_id": row["event_id"],
            "watch_id": row["watch_id"],
            "spec_version": int(row["spec_version"]),
            "kind": row["kind"],
            "observed_at": row["observed_at"],
            "path": row["path"],
            "content": row["content"],
            "hash": row["hash"],
            "previous_hash": row["previous_hash"],
            "size": int(row["size"]) if row["size"] is not None else None,
            "truncated": bool(row["truncated"]),
            "details": details if isinstance(details, dict) else {},
        }

    def counts(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT status,COUNT(*) FROM watches GROUP BY status"
            ).fetchall()
            total = int(connection.execute("SELECT COUNT(*) FROM watches").fetchone()[0])
            events = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        result = {"total": total, "active": 0, "paused": 0, "stopped": 0,
                  "pending_lease": 0, "events": events}
        for status, count in rows:
            if status in result:
                result[str(status)] = int(count)
        return result

    def event_range(self) -> dict[str, int | None]:
        epoch = self.current_epoch()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MIN(seq),MAX(seq) FROM events WHERE epoch=?", (epoch,)
            ).fetchone()
        return {
            "earliest_seq": int(row[0]) if row[0] is not None else None,
            "latest_seq": int(row[1]) if row[1] is not None else None,
        }


def _read_stable(path: Path) -> dict[str, Any]:
    """Read one source with a bounded retry for metadata changes."""
    last_error: Exception | None = None
    for _ in range(3):
        try:
            before = path.stat()
            if not path.is_file():
                raise _SourceMissing("source_missing", "path is no longer a regular file")
            if before.st_size > MAX_FILE_BYTES:
                raise ObserverError(
                    "too_large", f"file is larger than {MAX_FILE_BYTES} bytes",
                    size=before.st_size,
                )
            raw = path.read_bytes()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino, before.st_dev) != (
                after.st_size, after.st_mtime_ns, after.st_ino, after.st_dev
            ):
                last_error = _SourceUnstable("unstable", "file metadata changed while reading")
                continue
            if len(raw) > MAX_FILE_BYTES:
                raise ObserverError("too_large", f"file is larger than {MAX_FILE_BYTES} bytes")
            try:
                content, truncated = _bounded_utf8(raw)
            except UnicodeDecodeError as error:
                raise ObserverError("invalid_utf8", "file is not valid UTF-8") from error
            return {
                "path": str(path),
                "hash": _sha256(raw),
                "content": content,
                "content_bytes": len(content.encode("utf-8")),
                "truncated": truncated,
                "size": len(raw),
                "mtime_ns": after.st_mtime_ns,
            }
        except FileNotFoundError as error:
            raise _SourceMissing("source_missing", "source disappeared") from error
        except PermissionError as error:
            raise ObserverError("read_error", f"cannot read source: {error}") from error
        except OSError as error:
            last_error = error
            continue
    if isinstance(last_error, _SourceUnstable):
        raise last_error
    if isinstance(last_error, _SourceMissing):
        raise last_error
    raise _SourceUnstable("unstable", "source did not settle during bounded retries")


def _scan_files(watch: dict[str, Any], roots: AuthorizedRoots) -> tuple[list[Path], list[dict[str, Any]], bool, bool]:
    """Return files, errors, whether enumeration was complete, and root-missing."""
    root = Path(watch["resolved_path"])
    errors: list[dict[str, Any]] = []
    files: list[Path] = []
    if not root.exists():
        return [], errors, False, True
    if root.is_file():
        if root.suffix.casefold() not in ALLOWED_SUFFIXES:
            errors.append({"reason": "unsupported_file", "path": str(root)})
            return [], errors, False, False
        if not roots.contains(root):
            errors.append({"reason": "out_of_scope", "path": str(root)})
            return [], errors, False, False
        return [root], errors, True, False
    if not root.is_dir():
        errors.append({"reason": "unsupported_path", "path": str(root)})
        return [], errors, False, False

    stack = [root]
    visited: set[str] = set()
    complete = True
    entry_count = 0
    while stack:
        if len(visited) >= MAX_DIRECTORIES_PER_SCAN:
            errors.append({
                "reason": "directory_limit",
                "path": str(root),
                "message": f"more than {MAX_DIRECTORIES_PER_SCAN} directories",
            })
            return [], errors, False, False
        directory = stack.pop()
        try:
            resolved_dir = directory.resolve(strict=True)
        except OSError as error:
            errors.append({"reason": "read_error", "path": str(directory), "message": str(error)})
            complete = False
            continue
        if not roots.contains(resolved_dir):
            errors.append({"reason": "out_of_scope_link", "path": str(directory)})
            complete = False
            continue
        key = os.path.normcase(str(resolved_dir))
        if key in visited:
            continue
        visited.add(key)
        try:
            entries = list(os.scandir(resolved_dir))
        except OSError as error:
            errors.append({"reason": "read_error", "path": str(directory), "message": str(error)})
            complete = False
            continue
        for entry in entries:
            entry_count += 1
            if entry_count > MAX_DIRECTORY_ENTRIES_PER_SCAN:
                errors.append({
                    "reason": "entry_limit",
                    "path": str(root),
                    "message": f"more than {MAX_DIRECTORY_ENTRIES_PER_SCAN} directory entries",
                })
                return [], errors, False, False
            path = Path(entry.path)
            try:
                is_link = entry.is_symlink()
                if is_link:
                    target = path.resolve(strict=True)
                    if not roots.contains(target):
                        errors.append({"reason": "out_of_scope_link", "path": str(path)})
                        complete = False
                        continue
                    path = target
                if entry.is_dir(follow_symlinks=True):
                    if watch["recursive"]:
                        stack.append(path)
                    continue
                if entry.is_file(follow_symlinks=True) and path.suffix.casefold() in ALLOWED_SUFFIXES:
                    if not roots.contains(path):
                        errors.append({"reason": "out_of_scope_link", "path": str(path)})
                        complete = False
                        continue
                    files.append(path)
                    if len(files) > MAX_FILES_PER_WATCH:
                        errors.append({
                            "reason": "file_limit",
                            "path": str(root),
                            "message": f"more than {MAX_FILES_PER_WATCH} allowed files",
                        })
                        return [], errors, False, False
            except FileNotFoundError:
                # The entry disappeared between scandir and inspection.  A
                # later scan can distinguish a deletion from a transient race.
                complete = False
                errors.append({"reason": "source_missing", "path": str(path)})
            except OSError as error:
                complete = False
                errors.append({"reason": "read_error", "path": str(path), "message": str(error)})
    files.sort(key=lambda item: os.path.normcase(str(item)))
    return files, errors, complete, False


class ObserverService:
    """Lifecycle owner for the store, scanner, and lease."""

    def __init__(self, data_dir: Path, roots: list[str], interval: float = DEFAULT_INTERVAL,
                 storage_quota: int = DEFAULT_STORAGE_QUOTA) -> None:
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.data_dir.is_dir():
            raise ObserverError("invalid_data", "data path must be a directory")
        if interval < MIN_INTERVAL or interval > MAX_INTERVAL:
            raise ObserverError(
                "invalid_interval", f"interval must be between {MIN_INTERVAL} and {MAX_INTERVAL}"
            )
        if storage_quota <= 0:
            raise ObserverError("invalid_quota", "storage quota must be positive")
        self.roots = AuthorizedRoots(roots)
        self.interval = float(interval)
        self.storage_quota = int(storage_quota)
        self.owner = _DirectoryOwner(self.data_dir)
        try:
            self.store = ObserverStore(self.data_dir)
            epoch, implementation = self.store.identity()
            if implementation != IMPLEMENTATION:
                self.store.replace_implementation(uuid.uuid4().hex)
                epoch = self.store.current_epoch()
            self.store.recover_startup()
            self.epoch = epoch
            self._stop = threading.Event()
            self._wake = threading.Event()
            self._scan_lock = threading.RLock()
            self._control_lock = threading.RLock()
            self._last_scan: dict[str, float] = {}
            self._thread: threading.Thread | None = None
            self._last_service_error = ""
        except BaseException:
            self.owner.release()
            raise

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="nexus-observer", daemon=True)
        self._thread.start()

    def stop(self, reason: str = "stdin_eof") -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)
        self.store.stop_collection(reason)

    def close(self) -> None:
        self.stop("process_close")
        self.owner.release()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as error:  # noqa: BLE001 - the service must report, not die
                self._last_service_error = f"{type(error).__name__}: {error}"
            self._wake.wait(SCAN_TICK)
            self._wake.clear()

    def _lease_state(self) -> tuple[str | None, bool]:
        return self.store.lease()

    def _quota_reached(self) -> bool:
        return self.store.storage_used() >= self.storage_quota

    def _tick(self) -> None:
        expires, active = self._lease_state()
        expiry = _parse_time(expires)
        now = datetime.now(UTC)
        if active and (expiry is None or expiry <= now):
            self.store.expire_collection("lease_expired")
            return
        if not active or expiry is None or expiry <= now:
            return
        if self._quota_reached():
            self.store.expire_collection("storage_quota")
            return
        watches = self.store.list_watches()
        for watch in watches:
            if watch["status"] != "active":
                continue
            previous = self._last_scan.get(watch["watch_id"], 0.0)
            if time.monotonic() - previous < watch["interval"]:
                continue
            self.scan_watch(watch["watch_id"], force=False)

    def scan_all(self, *, force: bool = True) -> None:
        for watch in self.store.list_watches():
            if watch["status"] == "active":
                self.scan_watch(watch["watch_id"], force=force)

    def scan_watch(self, watch_id: str, *, force: bool) -> list[dict[str, Any]]:
        with self._control_lock, self._scan_lock:
            watch = self.store.begin_scan(watch_id)
            if watch is None or watch["status"] != "active":
                return []
            expires, active = self._lease_state()
            expiry = _parse_time(expires)
            if not active or expiry is None or expiry <= datetime.now(UTC):
                return []
            if self._quota_reached():
                return []
            now_mono = time.monotonic()
            previous = self._last_scan.get(watch_id, 0.0)
            if not force and now_mono - previous < watch["interval"]:
                return []
            self._last_scan[watch_id] = now_mono
            scanned_at = _now()
            baseline_done, gap_pending, gap_reason, gap_started_at = self.store.scan_state(watch_id)
            baseline_rows = self.store.baseline_rows(watch_id)
            baseline_map = {str(row["path"]): row for row in baseline_rows}
            events: list[dict[str, Any]] = []
            upserts: list[dict[str, Any]] = []
            deletes: list[str] = []
            errors: list[dict[str, Any]] = []
            files: list[Path] = []
            complete = True
            root_missing = False
            try:
                files, errors, complete, root_missing = _scan_files(watch, self.roots)
            except Exception as error:  # noqa: BLE001 - report the actual source failure
                errors.append({"reason": "scan_error", "path": watch["path"], "message": str(error)})
                complete = False
            if expiry <= datetime.now(UTC) or self._quota_reached():
                return []
            if root_missing:
                complete = False
            seen: set[str] = set()
            for path in files:
                if expiry <= datetime.now(UTC):
                    return []
                path_text = str(path)
                seen.add(path_text)
                try:
                    item = _read_stable(path)
                except _SourceMissing as error:
                    if path_text in baseline_map:
                        previous_hash = str(baseline_map[path_text]["hash"])
                        events.append(self._event(
                            watch, "deleted", path_text, previous_hash=previous_hash,
                            details={"reason": error.code},
                        ))
                        deletes.append(path_text)
                    else:
                        errors.append({"reason": error.code, "path": path_text})
                    continue
                except _SourceUnstable as error:
                    errors.append({"reason": error.code, "path": path_text, "message": str(error)})
                    continue
                except ObserverError as error:
                    errors.append({
                        "reason": error.code,
                        "path": path_text,
                        "message": str(error),
                        **error.details,
                    })
                    continue
                previous = baseline_map.get(path_text)
                if previous is None:
                    kind = "baseline" if not baseline_done else "created"
                    events.append(self._event(
                        watch, kind, path_text, content=item["content"], hash=item["hash"],
                        size=item["size"], truncated=item["truncated"],
                    ))
                elif str(previous["hash"]) != item["hash"]:
                    events.append(self._event(
                        watch, "modified", path_text, content=item["content"], hash=item["hash"],
                        previous_hash=str(previous["hash"]), size=item["size"],
                        truncated=item["truncated"],
                    ))
                upserts.append(item)
            if root_missing or complete:
                for path_text, previous in baseline_map.items():
                    if path_text not in seen:
                        events.append(self._event(
                            watch, "deleted", path_text, content=None, hash=None,
                            previous_hash=str(previous["hash"]),
                            details={"reason": "source_missing"},
                        ))
                        deletes.append(path_text)
            if gap_pending and baseline_done:
                events.insert(0, self._event(
                    watch, "gap", watch["path"], content=None, hash=None,
                    details={
                        "reason": gap_reason or "collection_gap",
                        "started_at": gap_started_at,
                        "ended_at": scanned_at,
                        "limitations": "changes during the unobserved interval may be unavailable",
                    },
                ))
            error_signature = ""
            if errors:
                bounded_errors = errors[:8]
                error_signature = _dumps(bounded_errors)
                if error_signature != watch.get("last_error", ""):
                    events.append(self._event(
                        watch, "error", watch["path"], content=None, hash=None,
                        details={"errors": bounded_errors},
                    ))
            new_baseline_done = bool(baseline_done or (complete and not errors))
            if expiry <= datetime.now(UTC) or self._quota_reached():
                return []
            try:
                inserted = self.store.insert_scan(
                    watch, self.epoch, events, upserts, deletes, new_baseline_done,
                    error_signature, scanned_at,
                )
            except ObserverError as error:
                if error.code == "lease_expired":
                    self.store.expire_collection("lease_expired")
                return []
            return inserted

    @staticmethod
    def _event(watch: dict[str, Any], kind: str, path: str, *, content: str | None,
               hash: str | None, previous_hash: str | None = None, size: int | None = None,
               truncated: bool = False, details: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "event_id": uuid.uuid4().hex,
            "watch_id": watch["watch_id"],
            "spec_version": watch["spec_version"],
            "kind": kind,
            "observed_at": _now(),
            "path": path,
            "content": content,
            "hash": hash,
            "previous_hash": previous_hash,
            "size": size,
            "truncated": truncated,
            "details": details or {},
        }

    def status(self) -> dict[str, Any]:
        expires, active = self.store.lease()
        expiry = _parse_time(expires)
        now = datetime.now(UTC)
        used = self.store.storage_used()
        if used >= self.storage_quota:
            state, reason = "quota_exhausted", "storage quota reached; collection stopped"
        elif not active:
            if expires is not None and expiry is not None and expiry <= now:
                state, reason = "lease_expired", "collection lease expired"
            else:
                state, reason = "waiting_lease", "no active collection lease"
        elif expires is None:
            state, reason = "waiting_lease", "no collection lease has been granted"
        elif expiry is None or expiry <= now:
            state, reason = "lease_expired", "collection lease expired"
        elif self._stop.is_set():
            state, reason = "stopped", "observer process is stopping"
        else:
            state, reason = "collecting", ""
        counts = self.store.counts()
        return {
            "contract_version": CONTRACT_VERSION,
            "implementation": IMPLEMENTATION,
            "epoch": self.epoch,
            "health": {
                "state": state,
                "reason": reason,
                "scanning": state == "collecting",
                "lease_expires_at": expires,
                "storage": {"used_bytes": used, "quota_bytes": self.storage_quota},
                "watches": {key: counts[key] for key in
                            ("total", "active", "paused", "stopped", "pending_lease")},
                "last_service_error": self._last_service_error,
            },
            "limits": {
                "min_interval": MIN_INTERVAL,
                "max_interval": MAX_INTERVAL,
                "max_file_bytes": MAX_FILE_BYTES,
                "max_files_per_watch": MAX_FILES_PER_WATCH,
                "max_watches": MAX_WATCHES,
                "max_evidence_bytes": MAX_EVIDENCE_BYTES,
                "max_events_page": MAX_EVENTS_PAGE,
                "max_roots": MAX_ROOTS,
                "max_directories_per_scan": MAX_DIRECTORIES_PER_SCAN,
                "max_directory_entries_per_scan": MAX_DIRECTORY_ENTRIES_PER_SCAN,
            },
            "roots": self.roots.as_strings(),
            "event_range": self.store.event_range(),
            "owner_pid": os.getpid(),
            "limitations": (
                "polling snapshots only; changes between scans, including transient versions, "
                "may be unavailable"
            ),
        }

    def watches_list(self) -> dict[str, Any]:
        return {"epoch": self.epoch, "watches": self.store.list_watches()}

    def watch_put(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {"watch_id", "path", "recursive", "interval", "spec_version",
                   "control_version", "request_id"}
        self._check_keys(args, allowed, required={
            "watch_id", "path", "spec_version", "control_version", "request_id",
        })
        watch_id = _text(args["watch_id"], "watch_id", maximum=200)
        path_raw = _text(args["path"], "path", maximum=4096)
        spec_version = _positive_int(args["spec_version"], "spec_version")
        control_version = _positive_int(args["control_version"], "control_version")
        request_id = _text(args["request_id"], "request_id", maximum=200)
        recursive = args.get("recursive", False)
        if not isinstance(recursive, bool):
            raise ObserverError("invalid_arguments", "recursive must be boolean")
        interval = _number(args.get("interval", self.interval), "interval")
        # A watch cannot make the process poll faster than its configured bound.
        interval = max(interval, self.interval)
        if interval < MIN_INTERVAL or interval > MAX_INTERVAL:
            raise ObserverError(
                "invalid_interval", f"interval must be between {MIN_INTERVAL} and {MAX_INTERVAL}"
            )
        args_hash = _canonical_args(args)
        cached = self.store.request_result(request_id, "watch_put", args_hash)
        if cached is not None:
            return cached
        with self._control_lock:
            cached = self.store.request_result(request_id, "watch_put", args_hash)
            if cached is not None:
                return cached
            path = self.roots.resolve_watch_path(path_raw)
            expires, active = self.store.lease()
            expiry = _parse_time(expires)
            lease_active = bool(active and expiry is not None and expiry > datetime.now(UTC))
            existing = self.store.get_watch(watch_id)
            paused = bool(existing and existing["status"] == "paused")
            watch, reset_baseline = self.store.watch_put(
                watch_id, path, recursive, interval, spec_version, control_version,
                lease_active, paused,
            )
            inserted: list[dict[str, Any]] = []
            if lease_active and watch["status"] == "active":
                inserted = self.scan_watch(watch_id, force=True)
            watch = self.store.get_watch(watch_id) or watch
            if not watch["baseline_done"]:
                baseline_status = "not_collected"
            elif reset_baseline:
                baseline_status = "established"
            else:
                baseline_status = "preserved"
            result = {
                "epoch": self.epoch,
                "watch": watch,
                "baseline": {
                    "status": baseline_status,
                    "event_count": len(inserted),
                },
            }
            self.store.put_request(request_id, "watch_put", args_hash, result)
            return result

    def watch_control(self, operation: str, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {"watch_id", "spec_version", "control_version", "request_id"}
        self._check_keys(args, allowed, required=allowed)
        watch_id = _text(args["watch_id"], "watch_id", maximum=200)
        spec_version = _positive_int(args["spec_version"], "spec_version")
        control_version = _positive_int(args["control_version"], "control_version")
        request_id = _text(args["request_id"], "request_id", maximum=200)
        tool = f"watch_{operation}"
        args_hash = _canonical_args(args)
        cached = self.store.request_result(request_id, tool, args_hash)
        if cached is not None:
            return cached
        with self._control_lock:
            cached = self.store.request_result(request_id, tool, args_hash)
            if cached is not None:
                return cached
            expires, active = self.store.lease()
            expiry = _parse_time(expires)
            lease_active = bool(active and expiry is not None and expiry > datetime.now(UTC))
            watch = self.store.watch_control(
                watch_id, operation, spec_version, control_version, lease_active
            )
            result = {
                "epoch": self.epoch,
                "watch": watch,
                "operation": operation,
                "applied": True,
            }
            self.store.put_request(request_id, tool, args_hash, result)
            return result

    def lease_renew(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {"control_version", "ttl"}
        self._check_keys(args, allowed, required=allowed)
        control_version = _positive_int(args["control_version"], "control_version")
        ttl = _number(args["ttl"], "ttl")
        if ttl < MIN_LEASE_TTL or ttl > MAX_LEASE_TTL:
            raise ObserverError(
                "invalid_lease", f"ttl must be between {MIN_LEASE_TTL} and {MAX_LEASE_TTL}"
            )
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=ttl)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self._control_lock:
            lease = self.store.start_collection(control_version, expires_at, ttl)
            self._wake.set()
        return {"epoch": self.epoch, "lease": lease, "health": self.status()["health"]}

    def events_read(self, args: dict[str, Any]) -> dict[str, Any]:
        allowed = {"after", "limit"}
        self._check_keys(args, allowed, required=allowed)
        after = _non_negative_int(args["after"], "after")
        limit = _positive_int(args["limit"], "limit", maximum=MAX_EVENTS_PAGE)
        page = self.store.read_events(self.epoch, after, limit)
        if page.get("gap") is None:
            page.pop("gap", None)
        return page

    @staticmethod
    def _check_keys(args: dict[str, Any], allowed: set[str], *, required: set[str]) -> None:
        if not isinstance(args, dict):
            raise ObserverError("invalid_arguments", "arguments must be an object")
        unknown = set(args) - allowed
        if unknown:
            raise ObserverError("invalid_arguments", f"unknown arguments: {sorted(unknown)}")
        missing = required - set(args)
        if missing:
            raise ObserverError("invalid_arguments", f"missing arguments: {sorted(missing)}")


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "status",
        "description": "Observer health, limits, epoch, and applied watch counts.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "watches_list",
        "description": "List durable watch rules, versions, and actual status.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "watch_put",
        "description": "Create or replace a bounded watch rule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "watch_id": {"type": "string"},
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
                "interval": {"type": "number"},
                "spec_version": {"type": "integer"},
                "control_version": {"type": "integer"},
                "request_id": {"type": "string"},
            },
            "required": ["watch_id", "path", "spec_version", "control_version", "request_id"],
            "additionalProperties": False,
        },
    },
    *[
        {
            "name": f"watch_{operation}",
            "description": f"{operation.title()} a durable watch rule.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "watch_id": {"type": "string"},
                    "spec_version": {"type": "integer"},
                    "control_version": {"type": "integer"},
                    "request_id": {"type": "string"},
                },
                "required": ["watch_id", "spec_version", "control_version", "request_id"],
                "additionalProperties": False,
            },
        }
        for operation in ("pause", "resume", "stop")
    ],
    {
        "name": "events_read",
        "description": "Read a bounded page of durable events and content evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {"after": {"type": "integer"}, "limit": {"type": "integer"}},
            "required": ["after", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lease_renew",
        "description": "Renew the collection lease; without one the observer does not scan.",
        "inputSchema": {
            "type": "object",
            "properties": {"control_version": {"type": "integer"}, "ttl": {"type": "number"}},
            "required": ["control_version", "ttl"],
            "additionalProperties": False,
        },
    },
]


def _tool_error(error: ObserverError) -> dict[str, Any]:
    return {"error": {"code": error.code, "message": str(error), "details": error.details}}


class MCPStdioServer:
    """Minimal newline-delimited MCP server for the observer tools."""

    def __init__(self, service: ObserverService) -> None:
        self.service = service

    def _write(self, payload: dict[str, Any]) -> None:
        # MCP bytes are UTF-8 regardless of Windows' active console code page.
        # Source text may contain arbitrary Unicode even when stdout is cp1252.
        sys.stdout.buffer.write((_dumps(payload) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()

    def _result(self, request_id: Any, result: dict[str, Any], *, is_error: bool = False) -> None:
        self._write({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": _dumps(result)}],
                "isError": bool(is_error),
            },
        })

    def _error(self, request_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})

    def _call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "status":
            self.service._check_keys(args, set(), required=set())
            return self.service.status()
        if name == "watches_list":
            self.service._check_keys(args, set(), required=set())
            return self.service.watches_list()
        if name == "watch_put":
            return self.service.watch_put(args)
        if name == "watch_pause":
            return self.service.watch_control("pause", args)
        if name == "watch_resume":
            return self.service.watch_control("resume", args)
        if name == "watch_stop":
            return self.service.watch_control("stop", args)
        if name == "events_read":
            return self.service.events_read(args)
        if name == "lease_renew":
            return self.service.lease_renew(args)
        raise ObserverError("unknown_tool", f"unknown observer tool: {name}")

    def serve(self) -> int:
        for raw in sys.stdin.buffer:
            if not raw.strip():
                continue
            try:
                message = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                self._error(None, -32700, "parse error")
                continue
            if not isinstance(message, dict):
                self._error(None, -32600, "invalid request")
                continue
            request_id = message.get("id")
            method = message.get("method")
            if request_id is None:
                continue
            if method == "initialize":
                self._write({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "nexus-autonomy-observer", "version": CONTRACT_VERSION},
                    },
                })
            elif method == "tools/list":
                self._write({
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {"tools": TOOL_DEFINITIONS},
                })
            elif method == "ping":
                self._write({"jsonrpc": "2.0", "id": request_id, "result": {}})
            elif method == "tools/call":
                params = message.get("params") or {}
                if not isinstance(params, dict):
                    self._error(request_id, -32602, "params must be an object")
                    continue
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    self._error(request_id, -32602, "name and arguments are required")
                    continue
                try:
                    result = self._call(name, arguments)
                except ObserverError as error:
                    self._result(request_id, _tool_error(error), is_error=True)
                except Exception as error:  # noqa: BLE001 - report as a tool failure
                    self._result(
                        request_id,
                        {"error": {"code": "internal_error", "message": str(error)}},
                        is_error=True,
                    )
                else:
                    self._result(request_id, result)
            else:
                self._error(request_id, -32601, f"unknown method: {method}")
        return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Nexus local-text observation MCP service")
    parser.add_argument("--data", required=True, help="owner data directory")
    parser.add_argument("--roots-json", required=True, help="JSON array of absolute roots")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--storage-quota", type=int, default=DEFAULT_STORAGE_QUOTA)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        roots = json.loads(args.roots_json)
        if not isinstance(roots, list):
            raise ObserverError("invalid_roots", "--roots-json must be a JSON array")
        service = ObserverService(
            Path(args.data), roots, interval=args.interval, storage_quota=args.storage_quota
        )
    except (ObserverError, OSError, ValueError) as error:
        print(f"observer: {error}", file=sys.stderr, flush=True)
        return 2
    try:
        service.start()
        return MCPStdioServer(service).serve()
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
