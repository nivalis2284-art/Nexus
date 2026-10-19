"""Assemble a real model Agent for one persistent task."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import mcp, skills
from ..core.agent import Agent, AgentSuspended
from ..core.config import ConfigStore, build_profile
from ..core.permission import default_policy
from ..core.workspace import inspect
from ..model import RuntimeCredentials
from ..task import Task, TaskStore, TaskValidationError
from ..tools import build
from .callbacks import TaskCallbacks
from .controller import RunFnResult
from ..tools.files import file_effect
from ..tools._jobs import JobRegistry


@dataclass
class TaskAssembler:
    """The single adapter from task facts to the existing agent runtime."""

    store: TaskStore
    config: ConfigStore

    def _strict_settings(self, task: Task):
        raw = self.config.read_config()
        providers = self.config.providers()
        provider = next((item for item in providers if item.id == task.provider_id), None)
        if provider is None:
            raise TaskValidationError("任务的模型提供商已不存在；请修改任务模型绑定")
        if provider.family != "anthropic_messages":
            pass
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
        profile, key, permission, rules, model = self._strict_settings(task)
        workspace_path = Path(task.workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise TaskValidationError(f"任务工作目录不存在: {workspace_path}")
        workspace = inspect(workspace_path)
        skill_set = skills.discover(skills.default_roots(workspace_path, self.config.home))
        callbacks = TaskCallbacks(self.store, task.id, self.store.current_run(task.id), cancel=cancel)
        task_jobs = self.active_jobs(task.id)
        if task_jobs is None or getattr(task_jobs, "closed", True):
            task_jobs = JobRegistry()
        # The controller can close this registry immediately on a human pause;
        # its output snapshot and terminal state are persisted separately.
        self._jobs = getattr(self, "_jobs", {})
        self._jobs[task.id] = task_jobs

        registry, context = build(
            skill_set.bodies(), root=workspace_path,
            config={"webfetch_hosts": list(self.config.read_config().get("webfetch_hosts") or []),
                    "search_endpoint": str(self.config.read_config().get("search_endpoint") or "")},
            mcp=None, jobs=task_jobs, cancel_event=cancel,
            task_callbacks=callbacks,
        )
        context.ask = callbacks.ask
        context.task_todos = callbacks.write_todos
        context.file_effect = file_effect
        context.tool_effect = None
        raw_settings = self.config.read_config()
        context.web_allowlist = list(raw_settings.get("webfetch_hosts") or [])
        context.search_endpoint = str(raw_settings.get("search_endpoint") or "")

        policy = default_policy(permission, rules)
        system_task_state = callbacks.context_text()
        messages = self.store.messages_for_model(task.id)
        answered = self.store.latest_answered_interaction(task.id)
        if answered:
            messages.append({"role": "user", "content": (
                "The user has answered the saved task interaction: "
                + str(answered.get("payload")) + " -> " + str(answered.get("answer"))
            )})
        if not messages:
            messages.append({"role": "user", "content": (
                "Work on this persistent task. Start by understanding the goal, then use the task tools "
                "to record material decisions and verified outcomes. Do not claim completion without evidence.\n\n"
                f"Goal: {task.effective_goal or task.goal}\nConstraints: {task.constraints or '(none)'}"
            )})

        def approve(request):
            callbacks.approve(request)

        agent = Agent(
            profile=profile, credentials=RuntimeCredentials(key), registry=registry,
            context=context, workspace=workspace, policy=policy,
            max_steps=self.config.load().max_steps,
            context_limit=self.config.load().context_limit,
            approve=approve, effort=task.effort, skills=skill_set.manifest(),
            cancel_event=cancel, task_context=system_task_state,
            on_message=callbacks.save_message,
            on_tool_intent=callbacks.prepare_dispatch,
            on_tool_result=lambda call, args, result: callbacks.tool_message(
                str(call.get("id") or ""), str(call.get("name") or ""),
                args, result.content, result.ok, result.error_code, result.data,
            ),
            execute_prepared=lambda prepared: callbacks.wrap_execute(
                prepared, context, registry.execute_prepared
            ),
        )
        try:
            async def drive():
                final = None
                async for event in agent.run(messages):
                    if event.type == "done":
                        final = event.data
                return final
            import asyncio
            asyncio.run(drive())
        except AgentSuspended:
            current = self.store.get(task.id)
            if current.state == "running":
                current = self.store.command(
                    task.id, "waiting_user", request_id=f"wait-{task.id}"
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
        except TaskValidationError as error:
            # A rejected task tool call (e.g. claiming completion without
            # evidence) is feedback for the model. The agent loop catches only
            # tool failures inside its worker, so a deliberate tool-side gate
            # is surfaced as a visible task pause instead of a silent success.
            self.store.add_message(task.id, kind="notice", text=f"任务规则阻止继续: {error}")
            current = self.store.get(task.id)
            if current.state == "running":
                self.store.command(
                    task.id, "paused", request_id=f"stalled-{task.id}"
                )
            return RunFnResult(status="stalled", wait_reason=str(error))
        except BaseException as error:
            try:
                self.store.mark_dispatching_unknown(self.store.current_run(task.id))
            except Exception:
                pass
            raise
        finally:
            callbacks.save_run_messages(messages)
            if self.store.get(task.id).state != "waiting_user":
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
            if self.store.get(task.id).state != "waiting_user":
                task_jobs.close_all()
                if isinstance(self._jobs, dict) and self._jobs.get(task.id) is task_jobs:
                    self._jobs.pop(task.id, None)
        return RunFnResult(status="complete", text="本轮结束")
