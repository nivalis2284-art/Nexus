"""In-memory delivery of live runtime output to connected UI streams."""

from __future__ import annotations

import queue
import threading
from typing import Any


class RuntimeUpdates:
    """Fan out current deltas and keep the latest state for each owner.

    This is deliberately not an event log: disconnected clients recover from
    the durable session/task records, while a live subscriber receives only
    events published after it subscribes.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: set[Any] = set()
        self._live: dict[tuple[str, str], dict[str, Any]] = {}
        self._revision = 0

    def subscribe(self, subscriber=None):
        """Register a receiver with ``put(event)``, or create a queue."""
        if subscriber is None:
            subscriber = queue.Queue()
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def live(self, scope: str, owner_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._live.get((scope, owner_id), {}))

    def publish(self, scope: str, owner_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            self._revision += 1
            item = {"scope": scope, "owner_id": owner_id, "revision": self._revision, **event}
            self._update_live(scope, owner_id, item)
            item["live"] = dict(self._live[(scope, owner_id)])
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(item)

    def refresh(self, scope: str, owner_id: str, *, clear: bool = False,
                **event: Any) -> None:
        payload = {"type": "refresh", **event}
        if clear:
            payload["clear"] = True
        self.publish(scope, owner_id, payload)

    def close(self) -> None:
        with self._lock:
            subscribers, self._subscribers = tuple(self._subscribers), set()
        for subscriber in subscribers:
            subscriber.put(None)

    def _update_live(self, scope: str, owner_id: str, event: dict[str, Any]) -> None:
        key = (scope, owner_id)
        live = dict(self._live.get(key, {}))
        kind = event.get("type")
        if kind == "delta":
            live["text"] = str(live.get("text") or "") + str(event.get("text") or "")
            if event.get("message_id"):
                live["message_id"] = event["message_id"]
        elif kind == "thinking":
            live["thinking"] = str(live.get("thinking") or "") + str(event.get("text") or "")
            if event.get("message_id"):
                live["message_id"] = event["message_id"]
        elif kind == "tool_start":
            # The completed assistant message is checkpointed before a tool
            # starts; its live text must not be repeated in the next step.
            live.pop("text", None)
            live.pop("thinking", None)
            live["tool"] = event.get("tool")
        elif kind == "tool_result":
            live.pop("text", None)
            live.pop("thinking", None)
            live.pop("tool", None)
        elif kind == "context":
            live["context"] = event.get("data") or {}
        elif kind == "error":
            live["error"] = str(event.get("message") or "模型运行失败")
        elif kind == "done":
            self._clear_ephemeral(live)
        elif kind == "refresh" and event.get("clear"):
            self._clear_ephemeral(live)
        live["revision"] = int(event.get("revision") or self._revision)
        self._live[key] = live

    @staticmethod
    def _clear_ephemeral(live: dict[str, Any]) -> None:
        """Drop per-step output while retaining the latest context."""
        for key in ("text", "thinking", "tool", "error"):
            live.pop(key, None)


def runtime_updates(state: dict[str, Any]) -> RuntimeUpdates:
    """Return the service-wide feed, creating it on first use."""
    feed = state.get("runtime_updates")
    if feed is None:
        feed = state["runtime_updates"] = RuntimeUpdates()
    return feed


__all__ = ["RuntimeUpdates", "runtime_updates"]
