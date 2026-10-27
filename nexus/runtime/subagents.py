"""Independent child work on the existing durable task queue."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from ..core.compaction import paired, project
from ..task import TaskConflict, TaskValidationError

WAITING = "Waiting for subagents"
TERMINAL = {"completed", "cancelled"}


class SubagentService:
    def __init__(self, store, controller, config, parent_id, parent_kind, *,
                 settings=None, history=None, call_id=None, attachments=None):
        self.store, self.controller, self.config = store, controller, config
        self.parent_id, self.parent_kind = parent_id, parent_kind
        self.settings = settings
        self.history = history or (lambda: [])
        self.call_id = call_id or (lambda: str(time.time_ns()))
        self.attachments = attachments

    def _own(self, child_id):
        relation = self.store.child_relation(child_id)
        if not relation or relation["parent_id"] != self.parent_id or relation["parent_kind"] != self.parent_kind:
            raise TaskConflict("subagent belongs to another parent")
        return self.store.get(child_id)

    def start(self, task, *, context_mode="fresh", provider_id="", model_id="", effort=None,
              read_only=True, **_):
        if self.controller is None:
            raise TaskValidationError("Subagents require the running Nexus task service.")
        if self.parent_kind == "task":
            ancestor = self.store.child_relation(self.parent_id)
            if ancestor:
                raise TaskValidationError("A subagent cannot delegate again; report back to its parent.")
            parent = self.store.get(self.parent_id)
            workspace, constraints = parent.workspace, parent.constraints
            provider_id = provider_id or parent.provider_id
            model_id = model_id or parent.model_id
            effort = parent.effort if effort is None else effort
        else:
            parent = self.config.load()
            workspace, constraints = parent.workspace, ""
            provider_id = provider_id or parent.provider.id
            model_id = model_id or parent.model["id"]
            effort = parent.effort if effort is None else effort
        provider = next((p for p in self.config.providers() if p.id == provider_id), None)
        model = next((m for m in provider.models if m.get("id") == model_id), None) if provider else None
        if not model or not provider.base_url or not self.config.has_key(provider_id):
            raise TaskValidationError("Select an available configured subagent model.")
        from ..core.effort import normalize
        effort = normalize(effort)
        identity = hashlib.sha256(json.dumps([self.parent_kind, self.parent_id, self.call_id(), task,
            context_mode, provider_id, model_id, effort], ensure_ascii=False).encode()).hexdigest()
        child = self.store.create(title=task[:80], goal=task, constraints=constraints,
            workspace=workspace, provider_id=provider_id, model_id=model_id, effort=effort,
            read_only=read_only, request_id="child-" + identity)
        self.store.link_child(child.id, parent_id=self.parent_id, parent_kind=self.parent_kind, context_mode=context_mode)
        if not self.store.messages(child.id):
            if context_mode == "fork":
                snapshot = paired(project(deepcopy(self.history())))
                self.store.save_run_messages(child.id, snapshot)
            from ..attachments import AttachmentStore
            source = AttachmentStore(self.config.home)
            copied = []
            for item in self.attachments() if self.attachments else []:
                with Path(item["original_path"]).open("rb") as stream:
                    copied.append(source.save("tasks", child.id, item["name"], item["mime"], stream, item["size"]))
            self.store.add_message(child.id, kind="user", text=(
                "You are an independently managed subagent. Complete only this delegated scope, "
                "using task_finish with your actual result. You cannot approve tools or finish the parent.\n\n" + task
            ), data={"attachments": copied})
        if child.state == "pending":
            self.controller.submit(child.id, request_id="child-start-" + identity)
        return self.read(child.id)

    def list(self):
        return {"children": self.store.children(self.parent_id, parent_kind=self.parent_kind)}

    def read(self, child_id, *, section="summary", offset=0, limit=20, text_offset=0):
        self._own(child_id)
        from ..ling import WorkReader
        return {**WorkReader(self.store, self.controller).read(child_id, section, offset, limit, text_offset),
                **self.store.child_relation(child_id)}

    def message(self, child_id, text):
        child = self._own(child_id)
        if child.state == "cancelled":
            raise TaskConflict("Cancelled subagent needs explicit resume before receiving more work.")
        if child.state == "completed":
            self.controller.reopen(child_id, request_id=f"child-reopen-{self.call_id()}")
        key = f"child-message-{self.parent_id}-{self.call_id()}"
        self.store.accept_user_message(child_id, text=text, request_id=key)
        if child.state == "completed":
            self.controller.submit(child_id, request_id=key + "-run")
        return self.read(child_id)

    def control(self, child_id, operation):
        child = self._own(child_id)
        key = f"child-{operation}-{self.parent_id}-{self.call_id()}"
        if operation == "resume":
            if child.state in TERMINAL:
                self.controller.reopen(child_id, request_id=key + "-reopen")
            self.controller.resume(child_id, request_id=key)
        elif operation == "pause":
            self.controller.pause(child_id, request_id=key)
        elif operation == "cancel":
            self.controller.cancel(child_id, request_id=key)
        else:
            raise TaskValidationError("Unknown subagent control.")
        return self.read(child_id)

    def model(self, child_id, provider_id, model_id, effort=None):
        self._own(child_id)
        from ..ling import validate_work_model
        values = validate_work_model({"store": self.config}, child_id, provider_id, model_id, effort)
        self.store.set_model(child_id, **values)
        return self.read(child_id)

    def wait(self, child_id=""):
        if child_id:
            self._own(child_id)
        children = self.store.children(self.parent_id, parent_kind=self.parent_kind)
        selected = [c for c in children if not child_id or c["task_id"] == child_id]
        if all(c["state"] in TERMINAL for c in selected):
            return {"children": [self.read(c["task_id"]) for c in selected]}
        if self.parent_kind == "task":
            jobs = getattr(self.controller.run, "_jobs", {}).get(self.parent_id)
            if jobs and any(job["running"] for job in jobs.list()):
                raise TaskValidationError("Finish or stop your live background commands before yielding to child work that may need this project's writer.")
            self.store.command(self.parent_id, "paused", request_id=f"child-wait-{self.call_id()}",
                reason=WAITING, source="agent")
        from ..core.agent import AgentSuspended
        raise AgentSuspended(WAITING, kind="children")


def collect_children(store, controller, parent_id):
    """Persist each result once, and continue only an automatically waiting parent."""
    children = store.children(parent_id, parent_kind="task")
    for child in children:
        if child["state"] not in TERMINAL | {"paused", "waiting_user"}:
            continue
        task = store.get(child["task_id"])
        if task.wait_reason == WAITING:
            continue
        store.accept_user_message(parent_id,
            text=f"[Subagent observation; not a new owner instruction] {task.id} {task.state}\n{task.work_summary or task.wait_reason}",
            request_id=f"child-result-{task.id}-{task.control_version}")
    parent = store.get(parent_id)
    if children and all(c["state"] in TERMINAL | {"paused", "waiting_user"} for c in children) and parent.state == "paused" and parent.wait_reason == WAITING:
        controller.resume(parent_id, request_id=f"child-ready-{parent_id}-{parent.control_version}")
