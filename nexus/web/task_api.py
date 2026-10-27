"""HTTP adapter for task commands, control, and event reads."""

from __future__ import annotations

import time
from typing import Any

from ..runtime.work import WorkOperations


class TaskHTTP:
    def __init__(self, state: dict[str, Any], json_response: Any) -> None:
        self.state = state
        self.respond = json_response
        self.work = WorkOperations(state)

    def list(self, handler, query: dict[str, list[str]]) -> None:
        include = query.get("archived", ["0"])[-1] in ("1", "true")
        self.respond(handler, 200, {"tasks": self.work.list(include_archived=include)})

    def detail(self, handler, task_id: str) -> None:
        self.respond(handler, 200, self.work.detail(task_id))

    def update(self, handler, task_id: str, body: dict[str, Any]) -> None:
        self.respond(handler, 200, self.work.update(task_id, body))

    def command(self, handler, task_id: str, body: dict[str, Any]) -> None:
        self.respond(handler, 200, self.work.command(task_id, body))

    def lifecycle(self, handler, task_id: str, body: dict[str, Any]) -> None:
        self.respond(handler, 200, self.work.lifecycle(task_id, body))

    def answer(self, handler, task_id: str, interaction_id: str, body: dict[str, Any]) -> None:
        self.respond(handler, 200, self.work.answer(task_id, interaction_id, body))

    def events(self, handler, task_id: str, query: dict[str, list[str]]) -> None:
        after = int(query.get("after", ["0"])[-1] or "0")
        limit = int(query.get("limit", ["200"])[-1] or "200")
        wait = min(20.0, max(0.0, float(query.get("wait", ["0"])[-1] or "0")))
        events = self.work.store.events(task_id, after=after, limit=limit)
        deadline = time.monotonic() + wait
        while not events and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            events = self.work.store.events(task_id, after=after, limit=limit)
        self.respond(handler, 200, {"events": events, "next": events[-1]["seq"] if events else after})

    def messages(self, handler, task_id: str, body: dict[str, Any]) -> None:
        self.respond(handler, 200, self.work.messages(task_id, body))

    def task_detail(self, task_id: str) -> dict[str, Any]:
        """Thin forwarder for callers that still read a task projection."""
        return self.work.detail(task_id)

    def delete(self, handler, task_id: str, body: dict[str, Any]) -> None:
        self.respond(handler, 200, self.work.delete(task_id, body))
