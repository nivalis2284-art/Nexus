"""Persistent facts written on behalf of one running task."""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from ..attachments import message_content
from ..task import Evidence, Step, TaskConflict, TaskStore, TaskValidationError
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
        self._operation_scope = ""
        self._finished = False
        self.waiting: dict[str, Any] | None = None
        self._failure_streak: tuple[tuple[str, str], int, bool] | None = None
        self._saved_messages: dict[int, dict[str, Any]] = {}
        self._progress_marker: tuple[int, int, int] | None = None

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
            recent = self._failure_streak
            if recent is not None and raw.get("new_information"):
                self._failure_streak = (recent[0], recent[1], True)
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
        limitations = raw.get("unresolved") or []
        if not evidence_ids:
            raise TaskValidationError("task completion needs a cited verification observation")
        cited_verified = [
            item for index, item in enumerate(task.evidence, start=1)
            if str(index) in evidence_ids and item.kind == "verification" and item.ok
        ]
        if not cited_verified:
            raise TaskValidationError(
                "task completion needs a cited successful verification observation"
            )
        if any(step.verification != "verified" for step in task.steps):
            raise TaskValidationError("every task step must be verified before completion")
        updated = self.store.update_progress(
            self.task_id, expected_version=task.progress_version,
            changes={
                "work_summary": raw["summary"],
                "decisions": [
                    *task.decisions,
                    {
                        "kind": "completion", "summary": raw["summary"],
                        "evidence_ids": sorted(evidence_ids, key=int),
                        "unresolved": list(limitations),
                    },
                ][-100:],
            },
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
            raw["operation_id"], conclusion=raw["conclusion"],
            observation=raw["observation"], task_id=self.task_id,
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
        except (OSError, ValueError, TaskConflict) as notice_error:
            self._persistence_notice_error = notice_error

    def pending_answer(self, interaction_id: str, answer: Any) -> None:
        """Record a decision only for one of this task's pending interactions."""
        interaction = self.store.interaction(interaction_id)
        if interaction["task_id"] != self.task_id:
            raise TaskConflict("interaction belongs to another task")
        if interaction["kind"] not in ("approval", "question"):
            raise TaskConflict("interaction cannot answer a task action")
        task = self.store.get(self.task_id)
        self.store.interaction_answer(
            interaction_id, answer=answer, expected_version=task.control_version
        )

    def on_message(self, message: dict[str, Any]) -> None:
        """Checkpoint one completed model/tool exchange immediately.

        A tool result is recorded only after dispatch returns, so this is an
        existing safe boundary. Provider messages are saved with their call
        identity so the end-of-run save cannot duplicate them.
        """
        identity = id(message)
        if identity in self._saved_messages:
            return
        saved = dict(message)
        try:
            self.store.save_run_messages(self.task_id, [saved])
        except BaseException as error:
            self.persistence_failure(error)
            raise
        # Holding the object prevents CPython from reusing its id in a later
        # Agent.run() while this callback still owns the run boundary.
        self._saved_messages[identity] = message

    def save_run_messages(self, messages: list[dict[str, Any]], *,
                          incomplete: bool = False) -> None:
        """Close the final exchange without rewriting already saved facts."""
        remaining = [{key: value for key, value in message.items() if key != "details"}
                     for message in messages
                     if id(message) not in self._saved_messages]
        try:
            self.store.save_run_messages(self.task_id, remaining, incomplete=incomplete)
        except BaseException as error:
            self.persistence_failure(error)
            raise
        self._saved_messages.update({id(message): message for message in messages})

    def apply_new_inputs(self, history: list[dict[str, Any]], *,
                         include_images: bool = False, snapshot: bool = False,
                         advance: bool = True, repeat_last: bool = False) -> int:
        """Merge durable user input at the model-request boundary.

        On a fresh run the already loaded snapshot is marked first. Later
        model steps consume only newer user rows; saved user rows are never
        appended to the provider transcript a second time.
        """
        status = self.store.input_status(self.task_id)
        applied = int(status["applied_through"])
        rows = self.store.messages(self.task_id)
        user_rows = [row for row in rows if row.get("kind") == "user"]
        through = max((int(row["seq"]) for row in user_rows), default=applied)
        if snapshot:
            if advance:
                self.store.mark_inputs_applied(self.task_id, through)
            return through
        new_rows = [row for row in user_rows if int(row["seq"]) > applied]
        if (not new_rows and repeat_last and user_rows
                and history and history[-1].get("role") == "assistant"):
            # A model response always needs a following user turn before the
            # next request. Do not resend assistant text or mutate old calls.
            prior = message_content(
                str(user_rows[-1].get("text") or ""), user_rows[-1].get("attachments") or [],
                include_images=include_images,
            )
            history.append({"role": "user", "content": prior})
        history.extend({
            "role": "user",
            "content": message_content(
                str(row.get("text") or ""), row.get("attachments") or [],
                include_images=include_images,
            ),
        } for row in new_rows)
        if advance:
            self.store.mark_inputs_applied(self.task_id, through)
        return through

    def snapshot_history(self, *, include_images: bool = False,
                         repeat_last: bool = False) -> list[dict[str, Any]]:
        """Build provider history and mark its user snapshot atomically.

        New input arriving during this operation either appears in the build
        or remains beyond the cursor; it cannot be marked applied unseen.
        """
        with self.store._lock:
            history = self.store.messages_for_model(
                self.task_id, include_images=include_images
            )
            if repeat_last and history and history[-1].get("role") == "assistant":
                user_rows = [row for row in self.store.messages(self.task_id)
                             if row.get("kind") == "user"]
                if user_rows:
                    history.append({
                        "role": "user",
                        "content": message_content(
                            str(user_rows[-1].get("text") or ""),
                            user_rows[-1].get("attachments") or [],
                            include_images=include_images,
                        ),
                    })
            self.apply_new_inputs(history, include_images=include_images, snapshot=True)
            return history

    def capture_progress(self) -> tuple[int, int, int]:
        task = self.store.get(self.task_id)
        return (task.progress_version, len(task.evidence), len(task.decisions))

    def continuation_progress(self) -> bool:
        """Whether saved work facts changed since this checkpoint began."""
        return self._progress_marker is None or self.capture_progress() != self._progress_marker

    def begin_continuation(self) -> None:
        self._progress_marker = self.capture_progress()

    def before_model(self, history: list[dict[str, Any]], *, agent: Any,
                     include_images: bool = False,
                     attachment_contexts: list[Any] | None = None) -> None:
        """Refresh the rendered task state and accept ordered user input."""
        agent.task_context = self.context_text()
        through = self.apply_new_inputs(
            history, include_images=include_images,
            advance=False, repeat_last=True,
        )
        # The caller has completed its preparation and this hook is invoked
        # immediately before the request. Persist the consumed boundary now.
        self.store.mark_inputs_applied(self.task_id, through)
        if attachment_contexts:
            allowed = {
                a["id"] for item in self.store.messages(self.task_id)
                for a in (item.get("attachments") or [])
            }
            for context in attachment_contexts:
                context.attachment_ids = allowed


    def prepare_dispatch(self, tool_call_id: str, prepared: Any, context: Any) -> None:
        """Persist intent immediately before the side effect enters a worker."""
        context._task_tool_call_id = self._scoped_call_id(tool_call_id)
        self.before_dispatch(prepared, context)

    def child_scope(self) -> TaskCallbacks:
        """One isolated operation namespace on the same task authority."""
        child = TaskCallbacks(
            self.store, self.task_id, self.run_id, cancel=self.cancel,
        )
        child._operation_scope = "child-" + uuid.uuid4().hex[:12]
        return child

    def refresh_child_context(self, history: list[dict[str, Any]], *,
                              agent: Any, attachment_contexts: list[Any] | None = None,
                              include_images: bool = False) -> None:
        """Refresh child facts without consuming the parent's input cursor."""
        agent.task_context = self.context_text()
        if attachment_contexts:
            allowed = {
                a["id"] for item in self.store.messages(self.task_id)
                for a in (item.get("attachments") or [])
            }
            for context in attachment_contexts:
                context.attachment_ids = allowed

    def add_child_evidence(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Subagent observations enrich the parent without rewriting its plan."""
        evidence = Evidence.from_dict({
            **raw,
            "metadata": {**(raw.get("metadata") or {}), "subagent": True},
        })
        updated = self.store.add_evidence(self.task_id, evidence)
        return {
            "evidence_count": len(updated.evidence),
            "progress_version": updated.progress_version,
        }

    def _scoped_call_id(self, tool_call_id: str) -> str:
        if not self._operation_scope:
            return tool_call_id
        return f"{self._operation_scope}:{tool_call_id}"

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
        lines.append(
            "Saved tool approvals permit one dispatch of the EXACT saved tool and arguments. "
            "If that action is still needed, reissue its arguments unchanged; rewriting an equivalent "
            "command requires a new approval. Approval is not proof of execution: wait for the actual "
            "tool result. Use file tools for reading/listing instead of requesting shell commands."
        )
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
