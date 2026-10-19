"""Persistent facts written on behalf of one running task."""

from __future__ import annotations

import json
import threading
from typing import Any

from ..task import Evidence, Step, TaskConflict, TaskStore, TaskValidationError, now_iso
from ..tools.registry import PreparedCall


class TaskCallbacks:
    """Tool and Agent bridge into the task authority."""

    def __init__(
        self, store: TaskStore, task_id: str, run_id: str, *, cancel: threading.Event
    ) -> None:
        self.store = store
        self.task_id = task_id
        self.run_id = run_id
        self.cancel = cancel
        self._lock = threading.RLock()
        self._attempts: list[dict[str, Any]] = []
        self._operations: dict[str, str] = {}
        self._finished = False
        self.waiting: dict[str, Any] | None = None

    # --- task work -------------------------------------------------------

    def update_work(self, changes: dict[str, Any]) -> dict[str, Any]:
        if self.cancel.is_set():
            raise TaskConflict("task is being suspended")
        if "steps" in changes:
            steps = []
            for raw in changes["steps"]:
                step = Step.from_dict(raw)
                steps.append(step.to_dict())
            changes = {**changes, "steps": steps}
        task = self.store.get(self.task_id)
        if "steps" in changes or "decisions" in changes:
            self._failure_streak = None
        updated = self.store.update_progress(
            self.task_id, expected_version=task.progress_version, changes=changes
        )
        return {"progress_version": updated.progress_version}

    def write_todos(self, items: list[dict[str, Any]]) -> None:
        """Mirror an ordinary todo list into the authoritative task steps."""
        task = self.store.get(self.task_id)
        steps = []
        for item in items:
            old = next((step for step in task.steps if step.content == item["content"]), None)
            steps.append({
                "content": item["content"],
                "status": item["status"],
                # todo completion is an execution claim, never a verification.
                "verification": old.verification if old is not None else "unverified",
                "evidence": list(old.evidence) if old is not None else [],
            })
        self.store.update_progress(
            self.task_id, expected_version=task.progress_version, changes={"steps": steps}
        )

    def add_evidence(self, raw: dict[str, Any]) -> dict[str, Any]:
        evidence = Evidence.from_dict(raw)
        updated = self.store.add_evidence(self.task_id, evidence)
        return {"evidence_count": len(updated.evidence), "progress_version": updated.progress_version}

    def record_attempt(self, raw: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._attempts.append(dict(raw))
            task = self.store.get(self.task_id)
            current = list(task.decisions)
            current.append({
                "kind": "attempt", "tool": raw["tool"], "error": raw["error"],
                "attempt": raw["attempt"], "premise": raw.get("premise", ""),
                "new_information": raw.get("new_information", []),
                "excluded": raw.get("excluded", []), "next_reason": raw.get("next_reason", ""),
            })
            self.store.update_progress(
                self.task_id, expected_version=task.progress_version,
                changes={"decisions": current[-100:]},
            )
            return {"attempt_count": len(self._attempts)}

    def finish(self, raw: dict[str, Any]) -> dict[str, Any]:
        task = self.store.get(self.task_id)
        if not task.steps:
            raise TaskValidationError("completion requires at least one recorded task step")
        if self.waiting is not None or self.store.pending_interactions(self.task_id):
            raise TaskValidationError("cannot finish while a user interaction is pending")
        unknown = self.store.unfinished_operations(self.task_id)
        if unknown:
            raise TaskValidationError(
                "cannot finish while an operation result is unknown; resolve the operation first"
            )
        if any(item.status != "completed" for item in task.steps):
            raise TaskValidationError("cannot finish while steps remain incomplete")
        if task.blocked:
            raise TaskValidationError("cannot finish while a blocker is recorded")
        evidence_ids = set(raw.get("evidence_ids") or [])
        existing_ids = {str(index + 1) for index in range(len(task.evidence))}
        if evidence_ids - existing_ids:
            raise TaskValidationError("completion cites evidence that does not exist")
        verified = [item for item in task.evidence if item.kind == "verification" and item.ok]
        limitations = raw.get("unresolved") or []
        if not verified:
            raise TaskValidationError("task completion needs a successful verification observation")
        if any(step.verification != "verified" for step in task.steps):
            raise TaskValidationError("every task step must be verified before completion")
        updated = self.store.update_progress(
            self.task_id, expected_version=task.progress_version,
            changes={"work_summary": raw["summary"]},
        )
        completed = self.store.command(
            self.task_id, "completed", request_id=f"finish-{self.run_id}",
            expected_version=updated.control_version,
        )
        self._finished = True
        return {"state": completed.state, "evidence_ids": sorted(evidence_ids),
                "limitations": limitations, "progress_version": updated.progress_version}

    def resolve_operation(self, raw: dict[str, Any]) -> dict[str, Any]:
        self.store.resolve_operation(
            raw["operation_id"], conclusion=raw["conclusion"], observation=raw["observation"]
        )
        return {"operation_id": raw["operation_id"], "conclusion": raw["conclusion"]}

    # --- operations ------------------------------------------------------

    def effect_for(self, prepared: PreparedCall, context: Any) -> dict[str, Any] | None:
        context._pending_tool_name = prepared.spec.name
        callback = getattr(context, "file_effect", None)
        if callback is not None:
            effect = callback(prepared.arguments, context)
            if effect is not None:
                return effect
        return None

    def before_dispatch(self, prepared: PreparedCall, context: Any) -> str | None:
        """Record an intent only for tools whose effects cannot be read-only."""
        if prepared.spec.name in ("task_update", "task_evidence", "task_finish",
                                  "task_attempt", "task_resolve_operation"):
            # These tools update task state themselves. Treating them as an
            # arbitrary side effect would make the operation ledger refer to its
            # own writer and could block the only route out of a failed attempt.
            return None
        if prepared.spec.read_only:
            return None
        signature = (prepared.spec.name, json.dumps(prepared.arguments, sort_keys=True, ensure_ascii=False))
        recent = getattr(self, "_failure_streak", None)
        if recent and recent[0] == signature and not recent[2]:
            raise TaskConflict(
                "The same call failed repeatedly with no newly recorded evidence. "
                "Read the error, investigate the cause, or record a changed premise before retrying."
            )
        if self.cancel.is_set():
            raise TaskConflict("task is suspended; tool was not dispatched")
        effect = self.effect_for(prepared, context)
        arguments = json.loads(json.dumps(prepared.arguments, ensure_ascii=False, default=str))
        operation_id = self.store.operation_begin(
            run_id=self.run_id,
            tool_call_id=str(context._task_tool_call_id),
            tool_name=prepared.spec.name,
            arguments=arguments,
            effect=effect,
        )
        self._operations[str(context._task_tool_call_id)] = operation_id
        return operation_id

    def prepare_batch(self, calls: list[dict[str, Any]]) -> None:
        """Persist all calls in an assistant batch before running the first."""
        if not calls:
            return
        self._batch_operations = self.store.operation_begin_for_batch(
            run_id=self.run_id, calls=calls,
        )
        self._batch_calls = [str(call.get("id") or "") for call in calls]

    def dispatch_claimed(self, tool_call_id: str) -> None:
        calls = getattr(self, "_batch_calls", [])
        operations = getattr(self, "_batch_operations", [])
        if tool_call_id not in calls:
            raise TaskConflict("tool call does not belong to the prepared batch")
        index = calls.index(tool_call_id)
        self.store.mark_operation_dispatched(operations[index])
        self._operations[tool_call_id] = operations[index]

    def before_call(self, call: dict[str, Any]) -> None:
        """Carry the provider call identity into the worker thread."""
        self._current_call_id = str(call.get("id") or "")

    def after_dispatch(self, tool_call_id: str, outcome: Any) -> None:
        operation_id = self._operations.get(tool_call_id)
        if operation_id is None:
            return
        self.store.operation_finish(
            operation_id, ok=bool(outcome.ok), content=outcome.content,
            data=getattr(outcome, "data", None),
        )
        if not outcome.ok:
            operation = self.store.get_operation(operation_id)
            signature = (operation["tool_name"], json.dumps(operation["arguments"], sort_keys=True, ensure_ascii=False))
            prior = getattr(self, "_failure_streak", None)
            count = prior[1] + 1 if prior and prior[0] == signature and not prior[2] else 1
            self._failure_streak = (signature, count, False)
            if count >= 3:
                raise TaskConflict(
                    "Repeated identical failure with no new evidence; stop this path and report the blocker."
                )
        else:
            self._failure_streak = None

    def wrap_execute(self, prepared: Any, context: Any, runner: Any) -> Any:
        """Run the already-recorded effect and commit its result."""
        tool_call_id = str(context._task_tool_call_id)
        outcome = runner(prepared, context)
        self.after_dispatch(tool_call_id, outcome)
        return outcome

    def persistence_failure(self, error: BaseException) -> None:
        self.cancel.set()
        try:
            self.store.add_message(
                self.task_id, kind="notice",
                text=f"无法保存任务检查点，已停止后续派发：{type(error).__name__}: {error}",
            )
        except Exception:
            pass

    def pending_answer(self, interaction_id: str, answer: Any) -> None:
        """Record the user's decision only for the still-current task version."""
        task = self.store.get(self.task_id)
        self.store.interaction_answer(
            interaction_id, answer=answer, expected_version=task.control_version
        )

    def save_message(self, message: dict[str, Any]) -> None:
        # Messages are committed as one coherent batch at a run boundary. A
        # partial assistant/tool pair must not become the next run's input.
        return

    def save_run_messages(self, messages: list[dict[str, Any]]) -> None:
        self.store.save_run_messages(self.task_id, messages)

    def tool_message(
        self, tool_call_id: str, name: str, arguments: dict[str, Any],
        content: str, ok: bool, error: str = "", data: dict[str, Any] | None = None,
    ) -> None:
        self.after_dispatch(tool_call_id, type("Outcome", (), {
            "ok": ok, "content": content, "data": data or {}
        })())

    def prepare_dispatch(self, tool_call_id: str, prepared: Any, context: Any) -> None:
        """Persist intent immediately before the side effect enters a worker."""
        context._task_tool_call_id = tool_call_id or getattr(self, "_current_call_id", "")
        self.before_dispatch(prepared, context)

    def context_text(self) -> str:
        task = self.store.get(self.task_id)
        lines = [
            f"Task: {task.title}", f"State: {task.state}",
            f"Original goal: {task.goal}", f"Current goal: {task.effective_goal or task.goal}",
            f"Constraints: {task.constraints or '(none recorded)'}",
        ]
        if task.steps:
            lines.append("Steps:")
            for index, step in enumerate(task.steps, start=1):
                lines.append(f"- {index}. [{step.status}/{step.verification}] {step.content}")
        if task.decisions:
            lines.append("Decisions: " + json.dumps(task.decisions[-20:], ensure_ascii=False))
        if task.evidence:
            lines.append("Evidence: " + json.dumps(
                [item.to_dict() for item in task.evidence[-20:]], ensure_ascii=False
            ))
        if task.blocked:
            lines.append("Blocked: " + task.blocked)
        if task.next_step:
            lines.append("Next step: " + task.next_step)
        if task.work_summary:
            lines.append("Working checkpoint: " + task.work_summary)
        if self._attempts:
            lines.append("Recent attempts: " + json.dumps(self._attempts[-20:], ensure_ascii=False))
        unknown = self.store.unfinished_operations(self.task_id)
        if unknown:
            lines.append("UNKNOWN OPERATIONS: " + json.dumps(unknown[-20:], ensure_ascii=False))
        return "\n".join(lines)

    def approve(self, request: Any) -> None:
        approved = self.store.answered_approval(
            self.task_id, tool=request.tool, arguments=request.arguments
        )
        if approved is not None:
            self.store.consume_interaction(approved["id"])
            if approved["answer"] is True:
                return
            from ..tools.registry import TaskSuspension
            raise TaskSuspension("The user declined this exact tool action.", kind="declined")
        payload = {"tool": request.tool, "value": request.value, "arguments": request.arguments}
        interaction_id = self.store.interaction_create(
            task_id=self.task_id, run_id=self.run_id, kind="approval", payload=payload,
            control_version=self.store.get(self.task_id).control_version,
        )
        self.waiting = {"interaction_id": interaction_id, "kind": "approval", "with_background": True}
        from ..core.agent import ApprovalRequired
        raise ApprovalRequired(request)

    def ask(self, questions: list[dict[str, Any]]) -> None:
        answered = self.store.answered_question(self.task_id, questions=questions)
        if answered is not None:
            self.store.consume_interaction(answered["id"])
            return answered["answer"]
        interaction_id = self.store.interaction_create(
            task_id=self.task_id, run_id=self.run_id, kind="question",
            payload={"questions": questions},
            control_version=self.store.get(self.task_id).control_version,
        )
        self.waiting = {"interaction_id": interaction_id, "kind": "question", "with_background": True}
        from ..core.agent import QuestionRequired
        raise QuestionRequired(questions)
