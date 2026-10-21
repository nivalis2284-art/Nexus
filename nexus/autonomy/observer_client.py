"""Dedicated client for the Nexus local-text observation MCP service.

This client owns exactly one stdio server.  It is intentionally separate from
the general MCP registry so observation calls never pass through display-time
tool text clipping and always retain the complete structured result.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Self

from ..mcp import MCPError, StdioServer

CONTRACT_VERSION = "1"
MAX_EVENTS_PAGE = 200
EVENT_KINDS = frozenset({"baseline", "created", "modified", "deleted", "gap", "error"})
WATCH_STATUSES = frozenset({"pending_lease", "active", "paused", "stopped"})


class ObserverClientError(RuntimeError):
    """The observer could not be reached or returned an invalid response."""

    def __init__(self, message: str, code: str | None = None,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class _Schema:
    @staticmethod
    def obj(value: Any, name: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ObserverClientError(f"observer response field {name} must be an object")
        return value

    @staticmethod
    def string(value: Any, name: str, *, allow_empty: bool = True) -> str:
        if not isinstance(value, str) or (not allow_empty and not value):
            raise ObserverClientError(f"observer response field {name} must be a string")
        return value

    @staticmethod
    def integer(value: Any, name: str, *, minimum: int | None = None) -> int:
        if type(value) is not int or (minimum is not None and value < minimum):
            raise ObserverClientError(f"observer response field {name} must be an integer")
        return value

    @staticmethod
    def boolean(value: Any, name: str) -> bool:
        if type(value) is not bool:
            raise ObserverClientError(f"observer response field {name} must be boolean")
        return value

    @staticmethod
    def number(value: Any, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ObserverClientError(f"observer response field {name} must be a number")
        return float(value)

    @classmethod
    def nullable_string(cls, value: Any, name: str) -> str | None:
        if value is None:
            return None
        return cls.string(value, name)

    @classmethod
    def nullable_integer(cls, value: Any, name: str) -> int | None:
        if value is None:
            return None
        return cls.integer(value, name)


def _validate_watch(value: Any) -> dict[str, Any]:
    watch = _Schema.obj(value, "watch")
    required = (
        "watch_id", "path", "resolved_path", "recursive", "interval",
        "spec_version", "control_version", "status", "baseline_done",
        "last_scan_at", "last_error", "created_at", "updated_at",
    )
    missing = [name for name in required if name not in watch]
    if missing:
        raise ObserverClientError(f"observer watch is missing fields: {missing}")
    _Schema.string(watch["watch_id"], "watch.watch_id", allow_empty=False)
    _Schema.string(watch["path"], "watch.path", allow_empty=False)
    _Schema.string(watch["resolved_path"], "watch.resolved_path", allow_empty=False)
    _Schema.boolean(watch["recursive"], "watch.recursive")
    _Schema.number(watch["interval"], "watch.interval")
    _Schema.integer(watch["spec_version"], "watch.spec_version", minimum=1)
    _Schema.integer(watch["control_version"], "watch.control_version", minimum=1)
    if watch["status"] not in WATCH_STATUSES:
        raise ObserverClientError(f"unknown observer watch status: {watch['status']!r}")
    _Schema.boolean(watch["baseline_done"], "watch.baseline_done")
    _Schema.nullable_string(watch["last_scan_at"], "watch.last_scan_at")
    _Schema.string(watch["last_error"], "watch.last_error")
    _Schema.string(watch["created_at"], "watch.created_at", allow_empty=False)
    _Schema.string(watch["updated_at"], "watch.updated_at", allow_empty=False)
    return watch


def _validate_health(value: Any) -> dict[str, Any]:
    health = _Schema.obj(value, "health")
    required = (
        "state", "reason", "scanning", "lease_expires_at", "storage",
        "watches", "last_service_error",
    )
    missing = [name for name in required if name not in health]
    if missing:
        raise ObserverClientError(f"observer health is missing fields: {missing}")
    _Schema.string(health["state"], "health.state", allow_empty=False)
    _Schema.string(health["reason"], "health.reason")
    _Schema.boolean(health["scanning"], "health.scanning")
    _Schema.nullable_string(health["lease_expires_at"], "health.lease_expires_at")
    storage = _Schema.obj(health["storage"], "health.storage")
    _Schema.integer(storage.get("used_bytes"), "health.storage.used_bytes", minimum=0)
    _Schema.integer(storage.get("quota_bytes"), "health.storage.quota_bytes", minimum=1)
    watches = _Schema.obj(health["watches"], "health.watches")
    for key in ("total", "active", "paused", "stopped", "pending_lease"):
        _Schema.integer(watches.get(key), f"health.watches.{key}", minimum=0)
    _Schema.string(health["last_service_error"], "health.last_service_error")
    return health


def _validate_event(value: Any, *, expected_epoch: str) -> dict[str, Any]:
    event = _Schema.obj(value, "event")
    required = (
        "seq", "event_id", "watch_id", "spec_version", "kind", "observed_at",
        "path", "content", "hash", "previous_hash", "size", "truncated", "details",
    )
    missing = [name for name in required if name not in event]
    if missing:
        raise ObserverClientError(f"observer event is missing fields: {missing}")
    _Schema.integer(event["seq"], "event.seq", minimum=1)
    _Schema.string(event["event_id"], "event.event_id", allow_empty=False)
    _Schema.string(event["watch_id"], "event.watch_id", allow_empty=False)
    _Schema.integer(event["spec_version"], "event.spec_version", minimum=1)
    if event["kind"] not in EVENT_KINDS:
        raise ObserverClientError(f"unknown observer event kind: {event['kind']!r}")
    _Schema.string(event["observed_at"], "event.observed_at", allow_empty=False)
    _Schema.string(event["path"], "event.path", allow_empty=False)
    _Schema.nullable_string(event["content"], "event.content")
    _Schema.nullable_string(event["hash"], "event.hash")
    _Schema.nullable_string(event["previous_hash"], "event.previous_hash")
    _Schema.nullable_integer(event["size"], "event.size")
    _Schema.boolean(event["truncated"], "event.truncated")
    _Schema.obj(event["details"], "event.details")
    if "epoch" in event and event["epoch"] != expected_epoch:
        raise ObserverClientError("observer event epoch does not match its page")
    return event


def _validate_status(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("contract_version", "implementation", "epoch", "health", "limits",
                "roots", "event_range", "owner_pid"):
        if key not in result:
            raise ObserverClientError(f"observer status is missing field: {key}")
    if _Schema.string(result["contract_version"], "contract_version") != CONTRACT_VERSION:
        raise ObserverClientError("unsupported observer contract version")
    _Schema.string(result["implementation"], "implementation", allow_empty=False)
    _Schema.string(result["epoch"], "epoch", allow_empty=False)
    _validate_health(result["health"])
    limits = _Schema.obj(result["limits"], "limits")
    for key in ("min_interval", "max_interval", "max_file_bytes", "max_files_per_watch",
                "max_watches", "max_evidence_bytes", "max_events_page", "max_roots",
                "max_directories_per_scan", "max_directory_entries_per_scan"):
        _Schema.number(limits.get(key), f"limits.{key}")
    roots = result["roots"]
    if not isinstance(roots, list) or not all(isinstance(item, str) for item in roots):
        raise ObserverClientError("observer roots must be a list of strings")
    event_range = _Schema.obj(result["event_range"], "event_range")
    _Schema.nullable_integer(event_range.get("earliest_seq"), "event_range.earliest_seq")
    _Schema.nullable_integer(event_range.get("latest_seq"), "event_range.latest_seq")
    _Schema.integer(result["owner_pid"], "owner_pid", minimum=1)
    _Schema.string(result.get("limitations"), "limitations", allow_empty=False)
    return result


def _validate_result(name: str, result: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ObserverClientError("observer result must be a JSON object")
    if name == "status":
        return _validate_status(result)
    if name == "watches_list":
        if "epoch" not in result or "watches" not in result:
            raise ObserverClientError("watches_list is missing required fields")
        _Schema.string(result["epoch"], "epoch", allow_empty=False)
        if not isinstance(result["watches"], list):
            raise ObserverClientError("watches_list.watches must be a list")
        for item in result["watches"]:
            _validate_watch(item)
        return result
    if name == "watch_put":
        for key in ("epoch", "watch", "baseline"):
            if key not in result:
                raise ObserverClientError(f"watch_put is missing field: {key}")
        _Schema.string(result["epoch"], "epoch", allow_empty=False)
        _validate_watch(result["watch"])
        baseline = _Schema.obj(result["baseline"], "baseline")
        _Schema.string(baseline.get("status"), "baseline.status", allow_empty=False)
        _Schema.integer(baseline.get("event_count"), "baseline.event_count", minimum=0)
        return result
    if name in ("watch_pause", "watch_resume", "watch_stop"):
        for key in ("epoch", "watch", "operation", "applied"):
            if key not in result:
                raise ObserverClientError(f"{name} is missing field: {key}")
        _Schema.string(result["epoch"], "epoch", allow_empty=False)
        _validate_watch(result["watch"])
        if result["operation"] != name.removeprefix("watch_"):
            raise ObserverClientError(f"{name} returned a mismatched operation")
        _Schema.boolean(result["applied"], "applied")
        return result
    if name == "events_read":
        for key in ("epoch", "events", "next_cursor", "has_more"):
            if key not in result:
                raise ObserverClientError(f"events_read is missing field: {key}")
        epoch = _Schema.string(result["epoch"], "epoch", allow_empty=False)
        if not isinstance(result["events"], list):
            raise ObserverClientError("events_read.events must be a list")
        if len(result["events"]) > MAX_EVENTS_PAGE:
            raise ObserverClientError("events_read returned more than the supported page")
        previous = args["after"]
        for raw in result["events"]:
            event = _validate_event(raw, expected_epoch=epoch)
            if event["seq"] <= previous:
                raise ObserverClientError("events_read is not strictly ordered after the cursor")
            previous = event["seq"]
        next_cursor = _Schema.integer(result["next_cursor"], "next_cursor", minimum=0)
        if result["events"] and next_cursor != result["events"][-1]["seq"]:
            raise ObserverClientError("events_read.next_cursor does not match the last event")
        if not result["events"] and next_cursor != args["after"]:
            raise ObserverClientError("empty events_read page advanced its cursor")
        _Schema.boolean(result["has_more"], "has_more")
        if "gap" in result and result["gap"] is not None:
            gap = _Schema.obj(result["gap"], "gap")
            _Schema.string(gap.get("reason"), "gap.reason", allow_empty=False)
            _Schema.integer(gap.get("from_seq"), "gap.from_seq", minimum=0)
            _Schema.integer(gap.get("to_seq"), "gap.to_seq", minimum=0)
        return result
    if name == "lease_renew":
        for key in ("epoch", "lease", "health"):
            if key not in result:
                raise ObserverClientError(f"lease_renew is missing field: {key}")
        _Schema.string(result["epoch"], "epoch", allow_empty=False)
        lease = _Schema.obj(result["lease"], "lease")
        _Schema.integer(lease.get("control_version"), "lease.control_version", minimum=1)
        _Schema.number(lease.get("ttl"), "lease.ttl")
        _Schema.string(lease.get("expires_at"), "lease.expires_at", allow_empty=False)
        _validate_health(result["health"])
        return result
    raise ObserverClientError(f"unsupported observer tool: {name}")


class ObserverClient:
    """One dedicated observer stdio connection with validated JSON results."""

    TOOLS = frozenset({
        "status", "watches_list", "watch_put", "watch_pause", "watch_resume",
        "watch_stop", "events_read", "lease_renew",
    })

    def __init__(self, data_dir: Path, roots: list[str], *, interval: float = 1.0,
                 command: list[str] | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.roots = list(roots)
        self.interval = float(interval)
        if not self.roots or not all(isinstance(item, str) and item for item in self.roots):
            raise ObserverClientError("roots must be a non-empty list of paths")
        if self.interval < 0.1 or self.interval > 3600:
            raise ObserverClientError("interval must be between 0.1 and 3600 seconds")
        if command is None:
            base = [sys.executable, "-m", "nexus.autonomy.observer"]
        else:
            if not command or not all(isinstance(item, str) and item for item in command):
                raise ObserverClientError("command must be a non-empty list of strings")
            base = list(command)
        roots_json = json.dumps(self.roots, ensure_ascii=False)
        argv = base + [
            "--data", str(self.data_dir),
            "--roots-json", roots_json,
            "--interval", str(self.interval),
        ]
        self._server = StdioServer(
            "nexus-autonomy-observer",
            argv[0],
            argv[1:],
            timeout=20,
        )
        self._started = False
        self._state_lock = threading.RLock()

    def start(self) -> None:
        with self._state_lock:
            if self._started:
                return
            try:
                self._server.start()
                info = self._server.initialize()
                if (
                    not isinstance(info, dict)
                    or info.get("protocolVersion") != "2025-06-18"
                    or not isinstance(info.get("serverInfo"), dict)
                ):
                    raise ObserverClientError("observer initialize returned an invalid result")
                declarations = self._server.list_tools()
                names = {item.name for item in declarations}
                if names != self.TOOLS:
                    raise ObserverClientError(
                        f"observer tool contract mismatch: expected {sorted(self.TOOLS)}, got {sorted(names)}"
                    )
            except (MCPError, OSError, ObserverClientError) as error:
                self._server.close()
                if isinstance(error, ObserverClientError):
                    raise
                raise ObserverClientError(f"cannot start observer: {error}") from error
            self._started = True

    def close(self) -> None:
        with self._state_lock:
            self._started = False
            self._server.close()

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in self.TOOLS:
            raise ObserverClientError(f"unsupported observer tool: {name}")
        if not isinstance(args, dict):
            raise ObserverClientError("observer arguments must be an object")
        with self._state_lock:
            if not self._started:
                raise ObserverClientError("observer client is not started")
        try:
            payload = self._server._request(
                "tools/call", {"name": name, "arguments": args}
            )
        except MCPError as error:
            raise ObserverClientError(f"observer connection failed: {error}") from error
        except (OSError, ValueError) as error:
            raise ObserverClientError(f"observer connection failed: {error}") from error
        blocks = payload.get("content")
        is_error = payload.get("isError", False)
        if type(is_error) is not bool:
            raise ObserverClientError("observer isError field must be boolean")
        if not isinstance(blocks, list) or len(blocks) != 1:
            raise ObserverClientError("observer response must contain exactly one text block")
        block = blocks[0]
        if not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str):
            raise ObserverClientError("observer response must contain exactly one JSON text block")
        try:
            result = json.loads(block["text"])
        except ValueError as error:
            raise ObserverClientError("observer returned invalid JSON text") from error
        if not isinstance(result, dict):
            raise ObserverClientError("observer response must be a JSON object")
        if is_error:
            error = result.get("error")
            if not isinstance(error, dict):
                raise ObserverClientError("observer error response has no error object")
            code = error.get("code")
            message = error.get("message")
            details = error.get("details", {})
            if not isinstance(code, str) or not code or not isinstance(message, str) or not message:
                raise ObserverClientError("observer error response has an invalid code or message")
            if not isinstance(details, dict):
                raise ObserverClientError("observer error details must be an object")
            raise ObserverClientError(message, code=code, details=details)
        if "error" in result:
            raise ObserverClientError("observer returned an error object without isError")
        return _validate_result(name, result, args)
