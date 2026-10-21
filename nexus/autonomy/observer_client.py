"""Client for the local-text observation MCP service.

The client owns one stdio server and reads the observation contract used by
Nexus: watch identity, specification versions, event evidence, cursors, and
lease health.  Additional service diagnostics are passed through without
becoming contract requirements, so an independently maintained implementation
can evolve them.
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
HEALTH_STATES = frozenset({
    "waiting_lease", "collecting", "lease_expired", "quota_exhausted", "stopped",
})


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

def _validate_watch(value: Any, *, require_resolved_path: bool = False) -> dict[str, Any]:
    watch = _Schema.obj(value, "watch")
    required = ("watch_id", "path", "spec_version", "status")
    missing = [name for name in required if name not in watch]
    if missing:
        raise ObserverClientError(f"observer watch is missing fields: {missing}")
    _Schema.string(watch["watch_id"], "watch.watch_id", allow_empty=False)
    _Schema.string(watch["path"], "watch.path", allow_empty=False)
    _Schema.integer(watch["spec_version"], "watch.spec_version", minimum=1)
    if watch["status"] not in WATCH_STATUSES:
        raise ObserverClientError(f"unknown observer watch status: {watch['status']!r}")
    if require_resolved_path:
        _Schema.string(watch.get("resolved_path"), "watch.resolved_path", allow_empty=False)
    return watch


def _validate_health(value: Any) -> dict[str, Any]:
    health = _Schema.obj(value, "health")
    required = ("state", "reason")
    missing = [name for name in required if name not in health]
    if missing:
        raise ObserverClientError(f"observer health is missing fields: {missing}")
    state = _Schema.string(health["state"], "health.state", allow_empty=False)
    if state not in HEALTH_STATES:
        raise ObserverClientError(f"unknown observer health state: {state!r}")
    _Schema.string(health["reason"], "health.reason")
    if "last_service_error" in health:
        error = health["last_service_error"]
        if error is not None:
            _Schema.string(error, "health.last_service_error")
    return health


def _validate_event(value: Any, *, expected_epoch: str) -> dict[str, Any]:
    event = _Schema.obj(value, "event")
    required = (
        "seq", "event_id", "watch_id", "spec_version", "kind", "observed_at",
        "path", "content", "hash", "truncated", "details",
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
    for name in ("content", "hash"):
        if event[name] is not None:
            _Schema.string(event[name], f"event.{name}")
    if "previous_hash" in event:
        previous_hash = event["previous_hash"]
        if previous_hash is not None:
            _Schema.string(previous_hash, "event.previous_hash")
    if "size" in event:
        size = event["size"]
        if size is not None:
            _Schema.integer(size, "event.size", minimum=0)
    _Schema.boolean(event["truncated"], "event.truncated")
    _Schema.obj(event["details"], "event.details")
    if "epoch" in event and event["epoch"] != expected_epoch:
        raise ObserverClientError("observer event epoch does not match its page")
    return event


def _validate_status(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("contract_version", "epoch", "health"):
        if key not in result:
            raise ObserverClientError(f"observer status is missing field: {key}")
    if _Schema.string(result["contract_version"], "contract_version") != CONTRACT_VERSION:
        raise ObserverClientError("unsupported observer contract version")
    _Schema.string(result["epoch"], "epoch", allow_empty=False)
    _validate_health(result["health"])
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
        _validate_watch(result["watch"], require_resolved_path=True)
        baseline = _Schema.obj(result["baseline"], "baseline")
        if "status" in baseline:
            _Schema.string(baseline["status"], "baseline.status", allow_empty=False)
        return result
    if name in ("watch_pause", "watch_resume", "watch_stop"):
        for key in ("epoch", "watch", "operation", "applied"):
            if key not in result:
                raise ObserverClientError(f"{name} is missing field: {key}")
        _Schema.string(result["epoch"], "epoch", allow_empty=False)
        _validate_watch(result["watch"])
        if result["operation"] != name.removeprefix("watch_"):
            raise ObserverClientError(f"{name} returned a mismatched operation")
        if "applied" in result:
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
        if result.get("gap") is not None:
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
        _validate_health(result["health"])
        return result
    raise ObserverClientError(f"unsupported observer tool: {name}")


class ObserverClient:
    """One observer stdio connection with contract-level result checks."""

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
                missing = self.TOOLS - names
                if missing:
                    raise ObserverClientError(
                        f"observer tool contract mismatch: missing {sorted(missing)}"
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
