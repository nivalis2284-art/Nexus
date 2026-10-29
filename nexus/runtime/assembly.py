"""Assemble a real model Agent for one persistent task."""

from __future__ import annotations

import asyncio
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import skills
from ..attachments import AttachmentStore
from ..core.agent import Agent, AgentSuspended
from ..core.config import ConfigStore, build_profile
from ..core.permission import default_policy
from ..core.workspace import inspect
from ..model import RuntimeCredentials
from ..task import Task, TaskStore, TaskValidationError
from ..tools import build
from ..tools._jobs import JobRegistry
from ..tools.files import file_effect
from ..tools.registry import TaskSuspension
from .callbacks import TaskCallbacks
from .connections import start_mcp
from .controller import RunFnResult, TaskConflict, needs_project_write


@dataclass
class TaskAssembler:
    """The single adapter from task facts to the existing agent runtime."""

    store: TaskStore
    config: ConfigStore
    # A service-owned registry is borrowed for the task's lifetime. Without
    # one, the task starts and closes its own connections.
    mcp_registry: Any = None
    plugins: Any = None
    request_history: Any = None
    controller: Any = None
    updates: Any = None

    def _feed(self):
        return self.updates

    def _saved(self, task_id: str, *, clear: bool = False) -> None:
        feed = self._feed()
        if feed is not None:
            feed.refresh("task", task_id, clear=clear)

    def _strict_settings(self, task: Task):
        raw = self.config.read_config()
        providers = self.config.providers()
        provider = next((item for item in providers if item.id == task.provider_id), None)
        if provider is None:
            raise TaskValidationError("任务的模型提供商已不存在；请修改任务模型绑定")
        model = next((item for item in provider.models if item.get("id") == task.model_id), None)
        if not isinstance(model, dict):
            raise TaskValidationError("任务的模型引用已不存在；请修改任务模型绑定")
        key = self.config.read_credentials().get(provider.id, "")
        if not provider.base_url or not key:
            raise TaskValidationError(f"任务提供商 {provider.name} 缺少 Base URL 或密钥")
        try:
            profile = build_profile(provider, model)
        except ValueError as error:
            raise TaskValidationError(f"任务模型配置无效: {error}") from error
        raw_settings = raw.get("permission")
        permission = raw_settings if raw_settings in ("ask", "yolo") else "ask"
        rules = raw.get("permissions")
        if not isinstance(rules, list):
            rules = []
        return profile, key, permission, rules, model

    def record_job_tails(self, task_id: str, snapshots: list[dict[str, Any]]) -> None:
        self.store.record_job_snapshots(task_id, snapshots)

    def active_jobs(self, task_id: str) -> Any:
        return getattr(self, "_jobs", {}).get(task_id)

    def __call__(self, task: Task, cancel: threading.Event) -> RunFnResult:
        stored_messages = self.store.messages(task.id)
        profile, key, permission, rules, model = self._strict_settings(task)
        workspace_path = Path(task.workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise TaskValidationError(f"任务工作目录不存在: {workspace_path}")
        workspace = inspect(workspace_path)
        skill_set = skills.discover(skills.default_roots(workspace_path, self.config.home))
        context_skill_bodies = skill_set.bodies()
        callbacks = TaskCallbacks(
            self.store, task.id, self.store.current_run(task.id), cancel=cancel,
            on_saved=self._saved,
        )
        task_jobs = self.active_jobs(task.id)
        if task_jobs is None or getattr(task_jobs, "closed", True):
            task_jobs = JobRegistry()
        # The controller can close this registry immediately on a human pause;
        # its output snapshot and terminal state are persisted separately.
        self._jobs = getattr(self, "_jobs", {})
        self._jobs[task.id] = task_jobs

        raw_settings = self.config.read_config()
        admitted_servers = self.config.load().mcp_servers
        shared_mcp = self.mcp_registry
        mcp_registry = shared_mcp if shared_mcp is not None else start_mcp(
            admitted_servers, workspace_path
        )
        try:
            registry, context = build(
                context_skill_bodies, root=workspace_path,
                config={"webfetch_hosts": list(raw_settings.get("webfetch_hosts") or []),
                        "search_endpoint": str(raw_settings.get("search_endpoint") or "")},
                mcp=mcp_registry, jobs=task_jobs, cancel_event=cancel,
                task_callbacks=callbacks, owner_home=self.config.home,
                attachment_source=(AttachmentStore(self.config.home), "tasks", task.id),
                attachment_ids={a["id"] for item in stored_messages for a in item.get("attachments", [])},
            )
            outcome = self._run(
                task, cancel, callbacks, profile, key, permission, rules, model,
                workspace, workspace_path, skill_set, task_jobs, registry, context,
                stored_messages,
            )
            if self.plugins:
                self.plugins.publish('work.finished', {"task_id": task.id, "state": self.store.get(task.id).state})
            self._saved(task.id)
            return outcome
        finally:
            if shared_mcp is None and mcp_registry is not None:
                mcp_registry.close()

    def _run(
        self, task: Task, cancel: threading.Event, callbacks: TaskCallbacks,
        profile: Any, key: str, permission: str, rules: list[dict[str, Any]],
        model: Any, workspace: Any, workspace_path: Path, skill_set: Any,
        task_jobs: JobRegistry, registry: Any, context: Any,
        stored_messages: list[dict[str, Any]],
    ) -> RunFnResult:
        incomplete = False
        context.ask = callbacks.ask
        context.task_todos = callbacks.write_todos
        context.file_effect = file_effect
        context.tool_effect = None
        raw_settings = self.config.read_config()
        context.web_allowlist = list(raw_settings.get("webfetch_hosts") or [])
        context.search_endpoint = str(raw_settings.get("search_endpoint") or "")
        from ..instructions import load_instructions
        from ..ling import available_work_models, select_work_model
        from ..role_instructions import load_role
        from ..tools.work import WORK_MODEL_TOOL, WORK_MODELS_TOOL
        context.work_models = lambda: available_work_models({"store": self.config})
        context.work_model = lambda **args: select_work_model({"store": self.config, "tasks": self.store}, task.id, **args).to_dict()
        registry.add(WORK_MODEL_TOOL)
        registry.add(WORK_MODELS_TOOL)
        if self.store.child_relation(task.id):
            registry._tools = {name: spec for name, spec in registry._tools.items()
                               if not name.startswith("subagent")}
        policy = default_policy(permission, rules)
        system_task_state = callbacks.context_text()

        messages = callbacks.snapshot_history(include_images=profile.capabilities.images)
        if not messages:
            self.store.add_message(task.id, kind="user", text=(
                "Work on this persistent task until it is finished or there is a reason to pause. "
                "Finish with task_finish(summary=the actual result and limitations), or use "
                "task_pause(reason=why you cannot usefully continue). Progress and evidence are optional "
                "records; use them when helpful. Investigate failures and change approach when needed.\n\n"
                f"Goal: {task.effective_goal or task.goal}\nConstraints: {task.constraints or '(none)'}"
            ))
            messages = callbacks.snapshot_history(include_images=profile.capabilities.images)

        async def approve(request):
            require_running()
            callbacks.approve(request)
            return True

        agent: Agent
        role_id = "subagent" if self.store.child_relation(task.id) else "work"

        def require_running() -> None:
            current = self.store.get(task.id)
            if current.state != "running":
                raise TaskSuspension(current.wait_reason or "任务已结束或挂起", kind=current.state)

        def before_model(history: list[dict[str, Any]]) -> None:
            require_running()
            current = self.store.get(task.id)
            updated_profile, updated_key, _, _, _ = self._strict_settings(current)
            agent.profile = updated_profile
            agent.credentials = RuntimeCredentials(updated_key)
            agent.effort = current.effort
            agent.owner_context = load_instructions(self.config.home)
            agent.role = load_role(self.config.home, role_id)
            from .subagents import collect_children
            collect_children(self.store, self.controller, task.id) if self.controller else None
            callbacks.before_model(
                history, agent=agent, include_images=agent.profile.capabilities.images,
                attachment_contexts=[context],
            )

        def execute_task_tool(prepared):
            require_running()
            return callbacks.wrap_execute(prepared, context, registry.execute_prepared)

        def prepare_task_tool(call_id, prepared, tool_context):
            require_running()
            if self.controller and needs_project_write(prepared.spec):
                self.controller.acquire_write(task.id, task.workspace, cancel)
            callbacks.prepare_dispatch(call_id, prepared, tool_context)

        agent = Agent(
            profile=profile, credentials=RuntimeCredentials(key), registry=registry,
            context=context, workspace=workspace, policy=policy,
            max_steps=None,
            context_limit=self.config.load().context_limit,
            approve=approve, effort=task.effort, skills=skill_set.manifest(),
            cancel_event=cancel,
            task_context=system_task_state,
            on_message=callbacks.on_message,
            before_model=before_model,
            on_tool_intent=prepare_task_tool,
            execute_prepared=execute_task_tool,
            plugins=self.plugins,
            plugin_scope={"kind": "task", "owner_id": task.id, "task_id": task.id},
            request_history=self.request_history,
            request_scope={"kind": "task", "owner_id": task.id},
            role=load_role(self.config.home, role_id),
        )


        from .subagents import SubagentService
        context.subagents = SubagentService(self.store, self.controller, self.config, task.id, "task",
            history=lambda: messages, call_id=lambda: getattr(context, "call_id", self.store.current_run(task.id)),
            attachments=lambda: [a for item in self.store.messages(task.id) for a in item.get("attachments", [])])
        try:
            final = None
            async def drive():
                final = None
                errors: list[str] = []
                async for event in agent.run(messages):
                    feed = self._feed()
                    if feed is not None:
                        if event.type in ("delta", "thinking"):
                            feed.publish("task", task.id, {
                                "type": event.type, "text": event.text,
                                "message_id": (event.data or {}).get("message_id"),
                            })
                        elif event.type in ("tool_start", "tool_result"):
                            feed.publish("task", task.id, {"type": event.type, "tool": event.data})
                        elif event.type == "context":
                            feed.publish("task", task.id, {"type": "context", "data": event.data})
                        elif event.type == "error":
                            feed.publish("task", task.id, {"type": "error", "message": event.text})
                    if event.type == "done":
                        final = event.data
                        if feed is not None:
                            feed.publish("task", task.id, {"type": "done", "data": event.data})
                    elif event.type == "error":
                        errors.append(event.text or "model error")
                if final is None and errors:
                    self.store.add_message(
                        task.id, kind="notice",
                        text="模型请求失败，本轮结果未确认：" + "; ".join(errors[-3:]),
                    )
                    return {"stopped": "model_error",
                            "finish_reason": None}
                return final
            final = asyncio.run(drive())
            while self._can_continue(task.id, final, cancel):
                messages = callbacks.snapshot_history(
                    include_images=profile.capabilities.images, repeat_last=True,
                )
                final = asyncio.run(drive())
        except AgentSuspended as suspension:
            current = self.store.get(task.id)
            if current.state == "completed":
                return RunFnResult(status="complete", text=current.work_summary)
            incomplete = True
            if current.state in ("paused", "pausing", "cancelled") or suspension.control == "paused":
                return RunFnResult(status="paused", wait_reason=suspension.reason)
            if current.state == "running":
                current = self.store.command(
                    task.id, "waiting_user", request_id=f"wait-{task.id}-{uuid.uuid4().hex}"
                )
            try:
                self.store.end_run(self.store.current_run(task.id), status="waiting_user")
            except Exception:
                pass
            snapshots = []
            for value in task_jobs.list():
                job = task_jobs.get(str(value["id"]))
                tail = ""
                if job is not None:
                    tail, _ = job.read(0, limit=8_000)
                snapshots.append({**value, "tail": tail})
            self.store.record_job_snapshots(task.id, snapshots)
            return RunFnResult(status="waiting_user", wait_reason="等待用户处理")
        except TaskSuspension as suspension:
            # A declined approval or other tool-side stop is durable. A
            # subagent suspension is translated to AgentSuspended before it
            # reaches here; this branch covers the parent and compact requests.
            current = self.store.get(task.id)
            if current.state == "completed":
                return RunFnResult(status="complete", text=current.work_summary)
            incomplete = True
            if current.state == "running":
                self.store.command(
                    task.id, "paused", request_id=f"suspended-{task.id}",
                    reason=suspension.reason, source="agent",
                )
            self.store.add_message(
                task.id, kind="notice", text=f"执行已停止：{suspension.reason}",
            )
            return RunFnResult(status="paused", wait_reason=suspension.reason)
        except TaskConflict as conflict:
            # A user pause/cancel can race a pending input or approval. The
            # controller's persisted state remains authoritative; report that
            # stop rather than turning it into a task failure.
            current = self.store.get(task.id)
            if current.state in ("paused", "cancelled"):
                return RunFnResult(status="paused", wait_reason=str(conflict))
            raise
        except TaskValidationError as error:
            incomplete = True
            # A failed task-state update is visible and resumable.
            self.store.add_message(task.id, kind="notice", text=f"任务状态更新失败: {error}")
            current = self.store.get(task.id)
            if current.state == "running":
                self.store.command(
                    task.id, "paused", request_id=f"stalled-{task.id}",
                    reason=str(error), source="error",
                )
            return RunFnResult(status="stalled", wait_reason=str(error))
        except Exception:
            incomplete = True
            try:
                self.store.mark_dispatching_unknown(self.store.current_run(task.id))
            except Exception:
                pass
            raise
        finally:
            # Persist only messages produced by this run. Re-saving the full
            # provider transcript duplicated all prior history after recovery.
            callbacks.save_run_messages(
                getattr(agent, "new_messages", []),
                incomplete=incomplete,
            )
            from .subagents import WAITING
            keep_jobs = (self.store.get(task.id).state == "waiting_user"
                         or self.store.get(task.id).wait_reason == WAITING)
            if not keep_jobs:
                snapshots = []
                for value in task_jobs.list():
                    job = task_jobs.get(str(value["id"]))
                    tail = ""
                    if job is not None:
                        tail, _ = job.read(0, limit=8_000)
                    snapshots.append({**value, "tail": tail})
                self.store.record_job_snapshots(task.id, snapshots)
            # While waiting for its owner, retain the registry so the UI can
            # inspect and stop a live command. A controller suspension closes
            # the registry and removes it before this cleanup runs.
            if not keep_jobs:
                task_jobs.close_all()
                if isinstance(self._jobs, dict) and self._jobs.get(task.id) is task_jobs:
                    self._jobs.pop(task.id, None)
            self._saved(task.id)
        stopped = str((final or {}).get("stopped") or "")
        if stopped not in ("complete", "cancelled"):
            reason = stopped or "unknown"
            self.store.add_message(
                task.id, kind="notice",
                text=f"本轮未正常结束（{reason}）；任务保留为挂起，需检查后继续。",
            )
            return RunFnResult(status="paused", wait_reason=reason)
        return RunFnResult(status="complete", text="本轮结束")


    def _can_continue(self, task_id: str, final: dict[str, Any] | None,
                      cancel: threading.Event) -> bool:
        if cancel.is_set():
            return False
        if str((final or {}).get("stopped") or "") != "complete":
            return False
        task = self.store.get(task_id)
        return task.state == "running" and not self.store.pending_interactions(task_id)
