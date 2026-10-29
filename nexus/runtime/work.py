"""Work operations over the existing task store and controller, without HTTP."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from ..attachments import AttachmentStore
from ..task import TaskConflict, TaskValidationError
from ..task.presentation import task_presentation
from .input import accept_input
from .stream import runtime_updates


class WorkOperations:
    """Business operations on work items; the HTTP layer only parses and replies."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

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

    def list(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        tasks = self.store.list(include_archived=include_archived)
        queued = set(self.controller.queued_ids())
        items = []
        children = self.store.child_ids()
        for task in tasks:
            if task.id in children:
                continue
            value = task.to_dict()
            value["active"] = self.controller.is_active(task.id)
            value["queued"] = task.id in queued
            value["pending_interactions"] = self.store.pending_interactions(task.id)
            value.update(task_presentation(self.store, self.controller, task))
            items.append(value)
        return items

    def detail(self, task_id: str) -> dict[str, Any]:
        """Shared task projection used by pages and control helpers."""
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
            **task_presentation(self.store, self.controller, task),
        }

    def update(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
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
        return item.to_dict()

    def command(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
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
        elif operation in ("archive", "unarchive"):
            with self._report_edit():
                item = getattr(self.controller, operation)(
                    task_id, request_id=request_id, expected_version=expected)
                self._sync_reports(task_id, "hide" if operation == "archive" else "show")
        else:
            raise TaskValidationError(f"unknown task operation: {operation}")
        self._forget_user_task_pause(task_id)
        return item.to_dict()

    def lifecycle(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        operation = str(body.get("operation") or "").strip()
        request_id = str(body.get("request_id") or "").strip()
        expected = body.get("expected_version")
        if not request_id or type(expected) is not int:
            raise TaskValidationError("request_id and expected_version are required")
        item = self.store.edit_lifecycle(task_id, operation, request_id=request_id,
                                        expected_version=expected)
        if operation == "mark_paused":
            self._forget_user_task_pause(task_id)
        return item.to_dict()

    def answer(self, task_id: str, interaction_id: str, body: dict[str, Any]) -> dict[str, Any]:
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
        return {**result, "pending": pending, "resumed": resumed}

    def messages(self, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return accept_input(self.store, self.controller, self.state["store"], task_id, body)

    def delete(self, task_id: str, body: dict[str, Any]) -> dict[str, bool]:
        expected = body.get("expected_version")
        if type(expected) is not int or body.get("confirm") is not True:
            raise TaskValidationError("permanent deletion requires expected_version and confirm=true")
        if self.controller.is_active(task_id) or task_id in self.controller.queued_ids():
            raise TaskConflict("task still owns execution resources")
        with self._report_edit():
            self.store.delete(task_id, expected_version=expected)
            self._sync_reports(task_id, "drop")
        AttachmentStore(self.state["store"].home).delete_owner("tasks", task_id)
        if self.state.get("request_history"):
            self.state["request_history"].delete_owner(task_id)
        runtime_updates(self.state).publish("task", task_id, {"type": "refresh", "deleted": True})
        return {"deleted": True}

    @contextmanager
    def _report_edit(self):
        busy = getattr(self.state.get("ling"), "busy", None)
        if busy is not None and not busy.acquire(blocking=False):
            raise TaskConflict("主交流正在处理输入，请等待结束后再整理工作记录。")
        try:
            yield
        finally:
            if busy is not None:
                busy.release()

    def _main_session(self):
        """The conversation the owner talks in; task events are recorded there."""
        interaction = self.state.get("ling")
        sessions = self.state.get("sessions")
        session_id = getattr(interaction, "session_id", None)
        if not sessions or not session_id:
            return None
        return sessions.load(session_id)

    def _sync_reports(self, task_id: str, action: str) -> int:
        """Carry a task's session events along with the task itself.

        hide/show only flip a flag, so the events keep their place in the
        conversation and come back untouched when the work is restored; drop
        removes them for a permanent deletion.
        """
        sessions = self.state.get("sessions")
        if sessions is None:
            return 0
        with sessions._lock:
            session = self._main_session()
            if session is None:
                return 0
            changed = 0
            removed = set()
            for index, item in enumerate(session.items):
                if item.kind == "work_report" and (item.data.get("report") or {}).get("task_id") == task_id:
                    if action == "drop":
                        changed += 1
                        removed.add(index)
                        continue
                    want = action == "hide"
                    if bool(item.data.get("hidden")) != want:
                        if want:
                            item.data["hidden"] = True
                        else:
                            item.data.pop("hidden", None)
                        changed += 1
            if changed:
                session.remove_items(removed)
                sessions.rewrite(session)
        if changed:
            runtime_updates(self.state).refresh("ling", session.id)
        return changed

    def _forget_user_task_pause(self, task_id: str) -> None:
        """Publish owner controls for interested plugins."""
        manager = self.state.get("plugins")
        if manager:
            manager.publish("task.owner_control", {"task_id": task_id})


__all__ = ["WorkOperations"]
