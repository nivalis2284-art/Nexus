"""HTTP adapter for task commands, control, and event reads."""

from __future__ import annotations

import time
from typing import Any

from ..attachments import AttachmentStore
from ..runtime.input import accept_input
from ..task import TaskConflict, TaskValidationError


class TaskHTTP:
    def __init__(self, state: dict[str, Any], json_response: Any) -> None:
        self.state = state
        self.respond = json_response

    @property
    def store(self):
        value = self.state.get("tasks")
        if value is None:
            raise TaskValidationError("task service is not initialized")
        return value

    @property
    def controller(self):
        value = self.state.get("task_controller")
        if value is None:
            raise TaskValidationError("task service is not initialized")
        return value

    def list(self, handler, query: dict[str, list[str]]) -> None:
        include = query.get("archived", ["0"])[-1] in ("1", "true")
        tasks = self.store.list(include_archived=include)
        queued = set(self.controller.queued_ids())
        items = []
        children = self.store.child_ids()
        for task in tasks:
            if task.id in children:
                continue
            value = task.to_dict()
            value["active"] = self.controller.is_active(task.id)
            value["queued"] = task.id in queued
            value["pending_interactions"] = len(self.store.pending_interactions(task.id))
            value.update(self._presentation(task))
            items.append(value)
        self.respond(handler, 200, {"tasks": items})

    def detail(self, handler, task_id: str) -> None:
        self.respond(handler, 200, self.task_detail(task_id))

    def update(self, handler, task_id: str, body: dict[str, Any]) -> None:
        expected = body.get("expected_version")
        if type(expected) is not int:
            raise TaskValidationError("expected_version is required")
        changes = {k: body[k] for k in ("title", "goal", "constraints", "effective_goal", "workspace") if k in body}
        model_fields = {k: body[k] for k in ("provider_id", "model_id", "effort") if k in body}
        if model_fields:
            from ..ling import validate_work_model
            current = self.store.get(task_id)
            changes.update(validate_work_model(
                self.state, task_id, model_fields.get("provider_id", current.provider_id),
                model_fields.get("model_id", current.model_id), model_fields.get("effort", current.effort)))
        if {"goal", "effective_goal", "constraints", "workspace"} & set(changes):
            if self.store.get(task_id).state in ("running", "pausing", "queued", "waiting_user"):
                raise TaskConflict("pause work before changing its goal or workspace")
            if "goal" in changes and "effective_goal" not in changes:
                changes["effective_goal"] = changes["goal"]
        if "steps" in body:
            if not isinstance(body["steps"], list):
                raise TaskValidationError("steps must be a list")
            item = self.store.update_task(
                task_id, expected_version=expected, changes=changes, steps=body["steps"],
            )
        else:
            item = self.store.update_task(task_id, expected_version=expected, changes=changes)
        self.respond(handler, 200, item.to_dict())

    def command(self, handler, task_id: str, body: dict[str, Any]) -> None:
        operation = str(body.get("operation") or "").strip()
        request_id = str(body.get("request_id") or "").strip()
        if not request_id:
            raise TaskValidationError("request_id is required")
        expected = body.get("expected_version")
        if operation in ("start", "resume"):
            item = self.controller.submit(task_id, request_id=request_id, expected_version=expected)
        elif operation == "pause":
            item = self.controller.pause(task_id, request_id=request_id, expected_version=expected)
        elif operation == "cancel":
            item = self.controller.cancel(task_id, request_id=request_id, expected_version=expected)
        elif operation == "reopen":
            item = self.controller.reopen(task_id, request_id=request_id, expected_version=expected)
        elif operation == "archive":
            item = self.controller.archive(task_id, request_id=request_id, expected_version=expected)
        elif operation == "unarchive":
            item = self.controller.unarchive(task_id, request_id=request_id, expected_version=expected)
        else:
            raise TaskValidationError(f"unknown task operation: {operation}")
        self._forget_user_task_pause(task_id)
        self.respond(handler, 200, item.to_dict())

    def lifecycle(self, handler, task_id: str, body: dict[str, Any]) -> None:
        operation = str(body.get("operation") or "").strip()
        request_id = str(body.get("request_id") or "").strip()
        expected = body.get("expected_version")
        if not request_id or type(expected) is not int:
            raise TaskValidationError("request_id and expected_version are required")
        item = self.store.edit_lifecycle(task_id, operation, request_id=request_id,
                                        expected_version=expected)
        if operation == "mark_paused":
            self._forget_user_task_pause(task_id)
        self.respond(handler, 200, item.to_dict())

    def answer(self, handler, task_id: str, interaction_id: str, body: dict[str, Any]) -> None:
        expected = body.get("expected_version")
        if type(expected) is not int:
            raise TaskValidationError("expected_version is required")
        interaction = self.store.interaction(interaction_id)
        if interaction["task_id"] != task_id:
            raise TaskConflict("interaction belongs to another task")
        if interaction["kind"] not in ("approval", "question"):
            raise TaskConflict("interaction is not a tool approval or question")
        result = self.store.interaction_answer(
            interaction_id, answer=body.get("answer"), expected_version=expected
        )
        interaction = self.store.pending_interactions(task_id)
        pending = next((item for item in interaction if item["id"] == interaction_id), None)
        resumed = False
        if body.get("resume") is True:
            self.controller.resume(task_id, request_id="answer-" + interaction_id)
            resumed = True
        self.respond(handler, 200, {**result, "pending": pending, "resumed": resumed})

    def events(self, handler, task_id: str, query: dict[str, list[str]]) -> None:
        after = int(query.get("after", ["0"])[-1] or "0")
        limit = int(query.get("limit", ["200"])[-1] or "200")
        wait = min(20.0, max(0.0, float(query.get("wait", ["0"])[-1] or "0")))
        events = self.store.events(task_id, after=after, limit=limit)
        deadline = time.monotonic() + wait
        while not events and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
            events = self.store.events(task_id, after=after, limit=limit)
        self.respond(handler, 200, {"events": events, "next": events[-1]["seq"] if events else after})

    def messages(self, handler, task_id: str, body: dict[str, Any]) -> None:
        result = accept_input(self.store, self.controller, self.state["store"], task_id, body)
        self.respond(handler, 200, result)

    def task_detail(self, task_id: str) -> dict[str, Any]:
        """Shared task projection used by pages and control helpers."""
        from ..runtime.stream import runtime_updates
        task = self.store.get(task_id)
        jobs = self.store.task_jobs(task_id)
        accessor = getattr(self.controller.run, "active_jobs", None)
        if callable(accessor):
            live = accessor(task_id)
            if live is not None:
                for value in live.list():
                    jobs = [item for item in jobs if item["id"] != value["id"]]
                    jobs.append({**value, "live": True})
        return {
            **task.to_dict(),
            **(self.store.child_relation(task_id) or {}),
            "children": self.store.children(task_id, parent_kind="task"),
            "pending_interactions": self.store.pending_interactions(task_id),
            "messages": self.store.messages(task_id),
            "input_status": self.store.input_status(task_id),
            "unfinished_operations": self.store.unfinished_operations(task_id),
            "jobs": jobs, "active": self.controller.is_active(task_id),
            "queued": task_id in self.controller.queued_ids(),
            "live": runtime_updates(self.state).live("task", task_id),
            **self._presentation(task),
        }

    def _presentation(self, task: Any) -> dict[str, str]:
        from ..task.presentation import task_presentation
        return task_presentation(self.store, self.controller, task)

    def delete(self, handler, task_id: str, body: dict[str, Any]) -> None:
        expected = body.get("expected_version")
        if type(expected) is not int or body.get("confirm") is not True:
            raise TaskValidationError("permanent deletion requires expected_version and confirm=true")
        if self.controller.is_active(task_id) or task_id in self.controller.queued_ids():
            raise TaskConflict("task still owns execution resources")
        self.store.delete(task_id, expected_version=expected)
        AttachmentStore(self.state["store"].home).delete_owner("tasks", task_id)
        if self.state.get("request_history"):
            self.state["request_history"].delete_owner(task_id)
        from ..runtime.stream import runtime_updates
        runtime_updates(self.state).publish("task", task_id, {"type": "refresh", "deleted": True})
        self.respond(handler, 200, {"deleted": True})

    def _forget_user_task_pause(self, task_id: str) -> None:
        """Publish owner controls for interested plugins."""
        manager = self.state.get("plugins")
        if manager:
            manager.publish("task.owner_control", {"task_id": task_id})
