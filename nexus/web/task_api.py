"""HTTP adapter for task commands, control, schedules, and event reads."""

from __future__ import annotations

import time
import uuid
from typing import Any

from ..attachments import AttachmentStore, IMAGE_MIMES
from ..task import TaskConflict, TaskNotFound, TaskValidationError


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

    def status(self, error: BaseException) -> int:
        if isinstance(error, TaskNotFound):
            return 404
        if isinstance(error, TaskConflict):
            return 409
        return 400

    def list(self, handler, query: dict[str, list[str]]) -> None:
        include = query.get("archived", ["0"])[-1] in ("1", "true")
        tasks = self.store.list(include_archived=include)
        active = self.controller.active_task
        queued = set(self.controller.queued_ids())
        items = []
        for task in tasks:
            value = task.to_dict()
            value["schedule"] = self.store.schedule(task.id)
            value["active"] = task.id == active
            value["queued"] = task.id in queued
            value["pending_interactions"] = len(self.store.pending_interactions(task.id))
            items.append(value)
        self.respond(handler, 200, {"tasks": items})

    def detail(self, handler, task_id: str) -> None:
        self.respond(handler, 200, self.task_detail(task_id))

    def create(self, handler, body: dict[str, Any]) -> None:
        title = str(body.get("title") or "").strip()
        goal = str(body.get("goal") or "").strip()
        if not title or not goal:
            raise TaskValidationError("title and goal are required")
        workspace = str(body.get("workspace") or self.state["store"].load().workspace)
        provider = str(body.get("provider_id") or self.state["store"].load().provider.id)
        model = str(body.get("model_id") or self.state["store"].load().model.get("id", ""))
        effort = body.get("effort")
        item = self.store.create(
            title=title, goal=goal, constraints=str(body.get("constraints") or ""),
            workspace=workspace, provider_id=provider, model_id=model,
            effort=str(effort) if effort else None,
            request_id=str(body.get("request_id") or "") or None,
        )
        self.respond(handler, 201, item.to_dict())

    def update(self, handler, task_id: str, body: dict[str, Any]) -> None:
        expected = body.get("expected_version")
        if type(expected) is not int:
            raise TaskValidationError("expected_version is required")
        changes = {k: body[k] for k in ("title", "goal", "constraints", "effective_goal",
                                        "workspace", "provider_id", "model_id", "effort")
                   if k in body}
        if {"goal", "effective_goal", "constraints", "workspace", "provider_id", "model_id", "effort"} & set(changes):
            current = self.store.get(task_id)
            if current.state in ("running", "pausing", "queued", "waiting_user"):
                raise TaskConflict("pause the active task before changing its goal or environment")
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
        self.respond(handler, 200, item.to_dict())

    def lifecycle(self, handler, task_id: str, body: dict[str, Any]) -> None:
        operation = str(body.get("operation") or "").strip()
        request_id = str(body.get("request_id") or "").strip()
        expected = body.get("expected_version")
        if not request_id or type(expected) is not int:
            raise TaskValidationError("request_id and expected_version are required")
        item = self.store.edit_lifecycle(task_id, operation, request_id=request_id,
                                        expected_version=expected)
        self.respond(handler, 200, item.to_dict())

    def answer(self, handler, task_id: str, interaction_id: str, body: dict[str, Any]) -> None:
        expected = body.get("expected_version")
        if type(expected) is not int:
            raise TaskValidationError("expected_version is required")
        interaction = self.store.interaction(interaction_id)
        if interaction["task_id"] != task_id:
            raise TaskConflict("interaction belongs to another task")
        if interaction["kind"] not in ("approval", "question"):
            raise TaskConflict("use the start confirmation endpoint for this interaction")
        result = self.store.interaction_answer(
            interaction_id, answer=body.get("answer"), expected_version=expected
        )
        interaction = self.store.pending_interactions(task_id)
        pending = next((item for item in interaction if item["id"] == interaction_id), None)
        resumed = False
        if body.get("resume") is True and result["operation_id"] is None:
            self.controller.resume(task_id, request_id="answer-" + interaction_id)
            resumed = True
        self.respond(handler, 200, {**result, "pending": pending, "resumed": resumed})

    def start_confirmation(self, handler, task_id: str, interaction_id: str, body: dict[str, Any]) -> None:
        expected = body.get("expected_version")
        if type(expected) is not int:
            raise TaskValidationError("expected_version is required")
        if body.get("answer") is not True:
            raise TaskValidationError("start_confirmation must be accepted explicitly")
        interaction = self.store.interaction(interaction_id)
        if interaction["task_id"] != task_id:
            raise TaskConflict("interaction belongs to another task")
        if interaction["kind"] != "start_confirmation":
            raise TaskConflict("interaction is not a start confirmation")
        self.store.interaction_answer(interaction_id, answer=True, expected_version=expected)
        current = self.store.get(task_id)
        self.controller.submit(task_id, request_id="confirm-" + uuid.uuid4().hex,
                               expected_version=current.control_version)
        self.respond(handler, 200, {"task_id": task_id, "accepted": True})

    def task_control(self, handler, body: dict[str, Any]) -> None:
        """Deterministic short controls, independent from the current model run."""
        task_id = str(body.get("task_id") or "").strip()
        instruction = str(body.get("text") or body.get("instruction") or "").strip()
        request_id = str(body.get("request_id") or "").strip()
        if not instruction or not request_id:
            raise TaskValidationError("text and request_id are required")
        words = instruction.lower().strip().rstrip("。.!！?？ ")
        for prefix in ("创建任务：", "新建任务：", "create task:"):
            if instruction.lower().startswith(prefix):
                goal = instruction[len(prefix):].strip()
                if not goal:
                    raise TaskValidationError("task goal is required")
                settings = self.state["store"].load()
                item = self.store.create(
                    title=goal[:80], goal=goal, workspace=settings.workspace,
                    provider_id=settings.provider.id,
                    model_id=str(settings.model.get("id") or ""),
                    request_id=request_id,
                )
                self.respond(handler, 200, {"operation": "create", "task": item.to_dict()})
                return
        if not task_id:
            candidates = [item for item in self.store.list() if item.title in instruction]
            if len(candidates) != 1:
                raise TaskConflict(
                    "Select a task explicitly; the instruction does not identify exactly one task."
                )
            task_id = candidates[0].id
            words = instruction.replace(candidates[0].title, "", 1).lower().strip().rstrip("。.!！?？ ")
        else:
            self.store.get(task_id)
        short = {
            "暂停": "pause", "挂起": "pause", "pause": "pause", "stop": "pause",
            "继续": "resume", "恢复": "resume", "resume": "resume",
            "取消": "cancel", "cancel": "cancel",
        }
        if words in ("继续这个任务", "继续当前任务", "continue this task"):
            short[words] = "resume"
        if words in short:
            operation = short[words]
            if operation == "pause":
                task = self.controller.pause(task_id, request_id=request_id)
            elif operation == "resume":
                task = self.controller.resume(task_id, request_id=request_id)
            else:
                task = self.controller.cancel(task_id, request_id=request_id)
            self.respond(handler, 200, {"operation": operation, "task": task.to_dict()})
            return
        raise TaskValidationError(
            "This control needs an unambiguous command. Use the task controls to avoid acting on the wrong task."
        )

    def schedule(self, handler, task_id: str, body: dict[str, Any]) -> None:
        result = self.store.set_schedule(
            task_id, due_at=str(body.get("due_at") or ""),
            timezone=str(body.get("timezone") or "UTC"), mode=str(body.get("mode") or "")
        )
        self.respond(handler, 200, result)

    def clear_schedule(self, handler, task_id: str) -> None:
        removed = self.store.clear_schedule(task_id)
        self.respond(handler, 200, {"removed": removed})

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
        """Accept user input without replacing a pending decision or run."""
        text = str(body.get("text") or "").strip()
        request_id = str(body.get("request_id") or "").strip()
        if (not text and not body.get("attachments")) or not request_id:
            raise TaskValidationError("text and request_id are required")
        task = self.store.get(task_id)
        previous = self.store.message_for_request(task_id, request_id)
        if previous is None and task.state in ("cancelled", "completed"):
            raise TaskConflict("re-enable or create a new task before adding work")
        # Persist the user's message before a worker can start and consume it.
        attachments = AttachmentStore(self.state["store"].home).resolve("tasks", task_id, body.get("attachments", []))
        if any(item["mime"] in IMAGE_MIMES for item in attachments):
            provider = next((item for item in self.state["store"].providers() if item.id == task.provider_id), None)
            model = next((item for item in provider.models if item.get("id") == task.model_id), {}) if provider else {}
            if not model.get("capabilities", {}).get("images"):
                raise TaskValidationError("任务绑定的模型未声明图片能力，请更换模型或移除图片")
            if any(item["mime"] in IMAGE_MIMES and item["size"] > 5 * 1024 * 1024 for item in attachments):
                raise TaskValidationError("图片上限为 5 MiB")
        message = self.store.accept_user_message(
            task_id, text=text, request_id=request_id, data={"attachments": attachments},
        )
        delivery = "pending"
        if previous is None and task.state == "paused" and body.get("resume", True) is True:
            task = self.controller.submit(task_id, request_id=request_id + "-run",
                                          expected_version=task.control_version)
        self.respond(handler, 200, {
            "task_id": task_id, "text": text, "request_id": request_id,
            "seq": message["seq"], "delivery": delivery,
        })

    def task_detail(self, task_id: str) -> dict[str, Any]:
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
            **task.to_dict(), "schedule": self.store.schedule(task_id),
            "pending_interactions": self.store.pending_interactions(task_id),
            "messages": self.store.messages(task_id),
            "input_status": self.store.input_status(task_id),
            "unfinished_operations": self.store.unfinished_operations(task_id),
            "jobs": jobs, "active": task_id == self.controller.active_task,
            "queued": task_id in self.controller.queued_ids(),
        }

    def add_task_message(self, task_id: str, text: str) -> None:
        """Append ordinary task input without starting a second run."""
        task = self.store.get(task_id)
        if task.state in ("cancelled", "completed"):
            raise TaskConflict("task is inactive")
        self.store.add_message(task_id, kind="user", text=text)

    def delete(self, handler, task_id: str, body: dict[str, Any]) -> None:
        expected = body.get("expected_version")
        if type(expected) is not int or body.get("confirm") is not True:
            raise TaskValidationError("permanent deletion requires expected_version and confirm=true")
        if task_id == self.controller.active_task or task_id in self.controller.queued_ids():
            raise TaskConflict("task still owns execution resources")
        self.store.delete(task_id, expected_version=expected)
        AttachmentStore(self.state["store"].home).delete_owner("tasks", task_id)
        self.respond(handler, 200, {"deleted": True})
