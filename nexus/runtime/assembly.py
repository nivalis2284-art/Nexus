"""Assemble a real model Agent for one persistent task."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .. import skills
from ..attachments import AttachmentStore
from ..core import subagent
from ..core.agent import Agent, AgentSuspended
from ..core.config import ConfigStore, Settings, build_profile
from ..core.permission import default_policy
from ..core.workspace import Workspace, inspect
from ..model import RuntimeCredentials
from ..task import Task, TaskStore, TaskValidationError
from ..tools import build
from ..tools._jobs import JobRegistry
from ..tools.files import file_effect
from ..tools.registry import Registry, TaskSuspension
from .callbacks import TaskCallbacks
from .connections import start_mcp
from .controller import RunFnResult, TaskConflict

CONTINUATION_LIMIT = 4
NO_PROGRESS_LIMIT = 3


def _is_ling_task(task: Task, rows: list[dict[str, Any]]) -> bool:
    """Only delegated Ling work continues automatically.

    User-created and pre-existing tasks keep their established manual
    end-of-turn behavior.
    """
    return any(
        row.get("kind") == "user" and row.get("source_session_id")
        or row.get("kind") == "autonomy"
        or bool(row.get("autonomy_goal_id") or row.get("autonomy_action_id"))
        for row in rows
    )


def _autonomy_scope(assembler: TaskAssembler, task: Task) -> Any:
    autonomy = getattr(assembler, "autonomy", None)
    if autonomy is None:
        return None
    lookup = getattr(autonomy, "task_scope", None)
    if lookup is None:
        return None
    return lookup(task.id)


def _action_ids(task: Task, rows: list[dict[str, Any]]) -> tuple[str, str]:
    goal = next((str(row["autonomy_goal_id"]) for row in rows
                 if row.get("autonomy_goal_id")), "")
    action = next((str(row["autonomy_action_id"]) for row in rows
                   if row.get("autonomy_action_id")), "")
    return goal, action


def _is_action_task(task: Task, rows: list[dict[str, Any]]) -> bool:
    """An autonomy row belongs to this task even if its scope was revoked."""
    return any(row.get("kind") == "autonomy"
               or row.get("autonomy_goal_id") or row.get("autonomy_action_id")
               for row in rows)


def _autonomy_prompt(task: Task, rows: list[dict[str, Any]]) -> str:
    goal_id, action_id = _action_ids(task, rows)
    return (
        "This is a bounded autonomous work task delegated by Ling, not a new user command. "
        "The autonomy goal, action ID and owner scope are authoritative; the user has not "
        "made an additional request in this turn. Work only inside the admitted read and "
        "output scope. Treat an unavailable or ambiguous action as a reason to stop and "
        "report rather than inventing owner authorization.\n\n"
        f"Autonomous goal: {task.effective_goal or task.goal}\n"
        f"Autonomy goal ID: {goal_id or '(missing)'}\n"
        f"Autonomy action ID: {action_id or '(missing)'}\n"
        f"Constraints: {task.constraints or '(none recorded)'}"
    )


def _autonomy_context(task: Task, rows: list[dict[str, Any]], state: str, scope: Any = None) -> str:
    """Persistent task facts plus the provenance of autonomous delegation."""
    text = state + "\n\n# Autonomous work provenance\n" + _autonomy_prompt(task, rows)
    if scope is not None:
        current = getattr(scope, "settings", {})
        runtime = getattr(scope, "runtime", None)
        if runtime is not None:
            current = runtime.store.settings()
            goal_id, _action_id = _action_ids(task, rows)
            concern = runtime.store.get("goal", goal_id) if goal_id else None
            if concern is not None:
                text += ("\nLatest owning concern (facts and understanding, NOT a new owner request "
                         "or a replacement of this task's accepted goal/permissions). Read referenced "
                         "sources when needed and incorporate relevant corrections; report conflicts "
                         "instead of assuming successful work.\n" + json.dumps({
                             "owning_concern": {key: concern.get(key) for key in
                                 ("id", "version", "status", "question", "understanding", "sources")}
                         }, ensure_ascii=False))
        accepted = getattr(scope, "accepted", current)
        fields = ("read_roots", "output_root", "allowed_mcp_tools")
        text += ("\nUse ABSOLUTE paths within BOTH current and accepted scopes below. "
                 "The working directory is only a common ancestor, not an authorized read root; "
                 "do not glob or grep '.' when it is outside these roots. "
                 "Read/write these explicit directories with file tools, not a shell.\n" +
                 json.dumps({"current_owner_scope": {key: current.get(key) for key in fields},
                             "accepted_action_scope": {key: accepted.get(key) for key in fields}}, ensure_ascii=False))
    return text


def _subagent_registry(registry: Registry) -> Registry:
    """Keep observation tools, but never the parent's lifecycle controls."""
    child = Registry()
    excluded = {"task_finish", "task_update", "task_attempt", "task_resolve_operation"}
    for spec in registry.specs():
        if spec.name not in excluded:
            child.add(spec)
    return child


def _mcp_names(scope: Any) -> set[str] | None:
    """MCP exposure is the intersection of current and accepted owner lists.

    A missing scope has no MCP filter. A real scope with no admitted names
    exposes none; it does not fall back to every configured server.
    """
    if scope is None:
        return None
    current = getattr(scope, "settings", {}) or {}
    accepted = getattr(scope, "accepted", {}) or {}
    current_names = current.get("allowed_mcp_tools") or []
    accepted_names = accepted.get("allowed_mcp_tools") or []
    return set(current_names).intersection(accepted_names)


def _filter_mcp(registry: Registry, allowed: set[str] | None) -> Registry:
    if allowed is None:
        return registry
    filtered = Registry()
    for spec in registry.specs():
        if not spec.name.startswith("mcp__") or spec.name in allowed:
            filtered.add(spec)
    return filtered


def _mcp_servers(settings: Settings, allowed: set[str] | None) -> list[dict[str, Any]]:
    """Select only configured servers named by admitted MCP tools.

    Launching a server is already an external capability, so a registry-level
    filter alone is insufficient. The returned dictionaries are copies used in
    a transient settings view; the owner's configuration is never modified.
    """
    if not allowed:
        return []
    names = set()
    for qualified in allowed:
        parts = qualified.split("__", 2)
        if len(parts) == 3 and parts[0] == "mcp" and parts[1]:
            names.add(parts[1])
    return [dict(entry) for entry in settings.mcp_servers
            if str(entry.get("name") or "") in names]


def _skill_roots(scope: Any) -> list[Path]:
    """Workspace skills only from the current/accepted admitted roots.

    ``workspace_root`` may be a common ancestor such as a drive or user home;
    searching it would expose skills outside the owner's selection. Each
    admitted directory is considered independently instead. Root order is
    deterministic and the output root participates only through this exact
    path, not through an ancestor scan.
    """
    current = getattr(scope, "settings", {}) or {}
    accepted = getattr(scope, "accepted", {}) or {}
    current_read = current.get("read_roots") or []
    accepted_read = accepted.get("read_roots") or []
    current_output = current.get("output_root") or ""
    accepted_output = accepted.get("output_root") or ""
    if (not current_read and not current_output
            and not accepted_read and not accepted_output):
        # Duck-typed scopes used by integrations may expose only the bounded
        # workspace. Treat that exact path as the root; never scan its parent.
        workspace = getattr(scope, "workspace_root", None)
        return [Path(workspace) / ".nexus" / "skills"] if workspace else []
    admitted: list[Path] = []
    for key in ("read_roots", "output_root"):
        now = current.get(key)
        then = accepted.get(key)
        now_values = now if isinstance(now, list) else [now] if now else []
        then_values = then if isinstance(then, list) else [then] if then else []
        for raw_now in now_values:
            try:
                path_now = Path(raw_now).expanduser().resolve()
            except (OSError, TypeError, ValueError):
                continue
            for raw_then in then_values:
                try:
                    path_then = Path(raw_then).expanduser().resolve()
                except (OSError, TypeError, ValueError):
                    continue
                if path_now == path_then or path_now.is_relative_to(path_then):
                    admitted.append(path_now)
                    break
    roots: list[Path] = []
    for root in admitted:
        candidate = root / ".nexus" / "skills"
        if candidate not in roots:
            roots.append(candidate)
    return roots


class _ScopedConfig:
    """A read-only ConfigStore view for one already authorized MCP start."""

    def __init__(self, store: ConfigStore, mcp_servers: list[dict[str, Any]]) -> None:
        self._store = store
        self._mcp_servers = mcp_servers

    def load(self) -> Settings:
        return replace(self._store.load(), mcp_servers=self._mcp_servers)


@dataclass
class TaskAssembler:
    """The single adapter from task facts to the existing agent runtime."""

    store: TaskStore
    config: ConfigStore
    autonomy: Any = None

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
        autonomy_task = _is_action_task(task, stored_messages)
        scope = _autonomy_scope(self, task)
        if autonomy_task and scope is None:
            raise TaskValidationError("自主任务没有有效的当前授权；请由灵重新审议")
        profile, key, permission, rules, model = self._strict_settings(task)
        if scope is not None:
            profile = scope.constrain_profile(profile)
        workspace_path = Path(task.workspace).expanduser().resolve()
        if not workspace_path.is_dir():
            raise TaskValidationError(f"任务工作目录不存在: {workspace_path}")
        if scope is not None:
            # The common ancestor exists only to make file tools able to resolve
            # multiple admitted roots. It must not import ancestor instructions
            # or inspect a repository outside the owner's selected directories.
            workspace = Workspace(
                root=workspace_path,
                instructions=(
                    "Bounded autonomous work. Read only the owner-approved roots "
                    "and write only to the approved output root."
                ),
            )
            skill_set = skills.discover(_skill_roots(scope))
            # An autonomous task may only use skills already installed under
            # an admitted read/output root. Do not inherit the common ancestor
            # or the user's global skills.
            context_skill_bodies = skill_set.bodies()
        else:
            workspace = inspect(workspace_path)
            skill_set = skills.discover(skills.default_roots(workspace_path, self.config.home))
            context_skill_bodies = skill_set.bodies()
        callbacks = TaskCallbacks(self.store, task.id, self.store.current_run(task.id), cancel=cancel)
        callbacks.autonomy_scope = scope
        task_jobs = self.active_jobs(task.id)
        if task_jobs is None or getattr(task_jobs, "closed", True):
            task_jobs = JobRegistry()
        # The controller can close this registry immediately on a human pause;
        # its output snapshot and terminal state are persisted separately.
        self._jobs = getattr(self, "_jobs", {})
        self._jobs[task.id] = task_jobs

        raw_settings = self.config.read_config()
        allowed_mcp = _mcp_names(scope)
        mcp_config = self.config
        if scope is not None:
            # Recompute the intersection at each run. Pausing or narrowing an
            # accepted action must stop a server from being launched, even if
            # it was previously exposed to this task.
            admitted_servers = _mcp_servers(self.config.load(), allowed_mcp)
            mcp_config = _ScopedConfig(self.config, admitted_servers)
        else:
            admitted_servers = None
        mcp_registry = (start_mcp(mcp_config, workspace_path)
                        if admitted_servers is None or admitted_servers else None)
        try:
            registry, context = build(
                context_skill_bodies, root=workspace_path,
                config={"webfetch_hosts": list(raw_settings.get("webfetch_hosts") or []),
                        "search_endpoint": str(raw_settings.get("search_endpoint") or "")},
                mcp=mcp_registry, jobs=task_jobs, cancel_event=cancel,
                task_callbacks=callbacks,
                attachment_source=(AttachmentStore(self.config.home), "tasks", task.id),
                attachment_ids={a["id"] for item in stored_messages for a in item.get("attachments", [])},
            )
            registry = _filter_mcp(registry, allowed_mcp)
            return self._run(
                task, cancel, callbacks, profile, key, permission, rules, model,
                workspace, workspace_path, skill_set, task_jobs, registry, context,
                stored_messages, scope,
            )
        finally:
            if mcp_registry is not None:
                mcp_registry.close()

    def _run(
        self, task: Task, cancel: threading.Event, callbacks: TaskCallbacks,
        profile: Any, key: str, permission: str, rules: list[dict[str, Any]],
        model: Any, workspace: Any, workspace_path: Path, skill_set: Any,
        task_jobs: JobRegistry, registry: Any, context: Any,
        stored_messages: list[dict[str, Any]],
        scope: Any = None,
    ) -> RunFnResult:
        incomplete = False
        context.ask = callbacks.ask
        context.task_todos = callbacks.write_todos
        context.file_effect = file_effect
        context.tool_effect = None
        raw_settings = self.config.read_config()
        context.web_allowlist = list(raw_settings.get("webfetch_hosts") or [])
        context.search_endpoint = str(raw_settings.get("search_endpoint") or "")
        context.autonomy_scope = scope
        context.model_budget = getattr(scope, "model_budget", None) if scope is not None else None

        policy = default_policy(permission, rules)
        if scope is not None:
            policy = scope.policy(policy)
        system_task_state = callbacks.context_text()

        # A subagent has a separate transcript and tools, but shares the parent's
        # workspace, policy, approval channel and cancellation event. Completion
        # of the parent task is reserved to the task agent.
        child_registry, child_context = build(
            skill_set.bodies(), root=workspace_path,
            config={"webfetch_hosts": list(raw_settings.get("webfetch_hosts") or []),
                    "search_endpoint": str(raw_settings.get("search_endpoint") or "")},
            mcp=context.mcp, jobs=task_jobs, cancel_event=cancel,
            attachment_source=(AttachmentStore(self.config.home), "tasks", task.id),
            attachment_ids={a["id"] for item in stored_messages
                            for a in item.get("attachments", [])},
        )
        child_registry = _subagent_registry(child_registry)
        child_registry = _filter_mcp(child_registry, _mcp_names(scope))
        child_context.ask = callbacks.ask
        child_callbacks = callbacks.child_scope()
        child_callbacks.add_evidence = child_callbacks.add_child_evidence
        child_context.task_callbacks = child_callbacks
        child_context.file_effect = file_effect
        child_context.web_allowlist = list(raw_settings.get("webfetch_hosts") or [])
        child_context.search_endpoint = str(raw_settings.get("search_endpoint") or "")
        child_context.autonomy_scope = scope
        child_context.model_budget = context.model_budget
        if scope is not None:
            # The child runs inside the parent's existing call, which already
            # passed guard_tool before its executor was entered. Running guard
            # again could consume a durable MCP approval twice; the child's
            # actual tools still pass guard at their own dispatch boundary.
            child_context.autonomy_scope = None
        messages = callbacks.snapshot_history(include_images=profile.capabilities.images)
        if not messages:
            if scope is None:
                self.store.add_message(task.id, kind="user", text=(
                    "Work on this persistent task. Start by understanding the goal, then use the task tools "
                    "to record material decisions and verified outcomes. Do not claim completion without evidence.\n\n"
                    f"Goal: {task.effective_goal or task.goal}\nConstraints: {task.constraints or '(none)'}"
                ))
            else:
                # Older autonomy rows may predate the initial autonomy message.
                # Supply the model a provider transcript without claiming that
                # the owner issued a new command.
                messages = [{"role": "user", "content": _autonomy_prompt(
                    task, self.store.messages(task.id),
                )}]
            if scope is None:
                messages = callbacks.snapshot_history(include_images=profile.capabilities.images)

        ling_task = _is_ling_task(task, self.store.messages(task.id))

        async def approve(request):
            callbacks.approve(request)
            return True

        def make_child_runner(prompt: str, budget: int):
            scoped_callbacks = callbacks.child_scope()
            scoped_callbacks.add_evidence = scoped_callbacks.add_child_evidence
            child_context.task_callbacks = scoped_callbacks
            child = Agent(
                profile=profile, credentials=RuntimeCredentials(key),
                registry=child_registry, context=child_context,
                workspace=workspace, policy=policy, max_steps=budget,
                context_limit=self.config.load().context_limit,
                approve=approve, effort=task.effort,
                skills=skill_set.manifest(), cancel_event=cancel,
                task_context="You are a subagent. You cannot finish the parent task.",
                on_tool_intent=scoped_callbacks.prepare_dispatch,
                execute_prepared=lambda prepared: scoped_callbacks.wrap_execute(
                    prepared, child_context, child_registry.execute_prepared
                ),
            )
            def child_before_model(history: list[dict[str, Any]]) -> None:
                if scope is not None:
                    try:
                        scope.check()
                    except TaskSuspension as error:
                        raise AgentSuspended.from_tool(error) from error
                child_callbacks.refresh_child_context(
                    history, agent=child, include_images=profile.capabilities.images,
                    attachment_contexts=[child_context],
                )
                if scope is not None:
                    child.task_context = _autonomy_context(task, self.store.messages(task.id), child.task_context, scope)
            child.before_model = child_before_model
            return child.run([{"role": "user", "content": prompt}])

        async def child_runner(prompt: str, budget: int):
            async for event in make_child_runner(prompt, budget):
                yield event

        agent: Agent

        def before_model(history: list[dict[str, Any]]) -> None:
            if scope is not None:
                try:
                    scope.check()
                except TaskSuspension as error:
                    raise AgentSuspended.from_tool(error) from error
            callbacks.before_model(
                history, agent=agent, include_images=profile.capabilities.images,
                attachment_contexts=[context, child_context],
            )
            if scope is not None:
                # TaskCallbacks.before_model refreshes task context on every
                # step. Reassert autonomy provenance so later continuation
                # requests cannot present this work as a new owner command,
                # while retaining the freshly persisted task facts.
                agent.task_context = _autonomy_context(
                    task, self.store.messages(task.id), agent.task_context, scope,
                )

        agent = Agent(
            profile=profile, credentials=RuntimeCredentials(key), registry=registry,
            context=context, workspace=workspace, policy=policy,
            max_steps=self.config.load().max_steps,
            context_limit=self.config.load().context_limit,
            approve=approve, effort=task.effort, skills=skill_set.manifest(),
            cancel_event=cancel,
            task_context=(_autonomy_context(
                              task, self.store.messages(task.id), system_task_state, scope)
                          if scope is not None else system_task_state),
            on_message=callbacks.on_message,
            before_model=before_model,
            on_tool_intent=callbacks.prepare_dispatch,
            execute_prepared=lambda prepared: callbacks.wrap_execute(
                prepared, context, registry.execute_prepared
            ),
        )


        def spawn_subagent(goal: str, steps: int):
            request = subagent.SubagentRequest(task=goal, max_steps=steps, depth=1)
            try:
                result = asyncio.run(subagent.run_subagent(
                    request, make_runner=child_runner,
                ))
            except subagent.SubagentSuspended as suspended:
                saved = self.store.add_message(
                    task.id, kind="notice",
                    text="Subagent suspended while waiting for user; partial trace only.",
                    data={"subagent": True, "status": "waiting_user",
                          "stopped": "waiting_user", "trace_complete": False,
                          "trace": suspended.trace},
                )
                suspended.message_seq = saved["seq"]
                raise suspended.original from suspended
            except Exception as error:
                result = subagent.SubagentResult(
                    text=f"Subagent failed: {type(error).__name__}: {error}",
                    stopped="error", depth=1,
                )
            saved = self.store.add_message(
                task.id, kind="notice",
                text=f"Subagent result ({result.stopped})\n{result.text}",
                data={"subagent": True, "stopped": result.stopped,
                      "steps": result.steps, "trace": result.trace},
            )
            result.message_seq = saved["seq"]
            return result

        context.spawn_subagent = spawn_subagent
        try:
            final = None
            async def drive():
                final = None
                errors: list[str] = []
                async for event in agent.run(messages):
                    if event.type == "done":
                        final = event.data
                    elif event.type == "error":
                        errors.append(event.text or "model error")
                if final is None and errors:
                    self.store.add_message(
                        task.id, kind="notice",
                        text="模型请求失败，本轮结果未确认：" + "; ".join(errors[-3:]),
                    )
                    return {"stopped": "model_error", "steps": agent.max_steps,
                            "finish_reason": None}
                return final
            final = asyncio.run(drive())
            continuation = 0
            no_progress = 0
            while self._can_continue(task.id, final, cancel, ling_task):
                if continuation >= CONTINUATION_LIMIT:
                    return self._block(task.id, "任务正常回复后仍未完成；可解释的续跑次数已用尽。")
                continuation += 1
                callbacks.begin_continuation()
                messages = callbacks.snapshot_history(
                    include_images=profile.capabilities.images, repeat_last=True,
                )
                final = asyncio.run(drive())
                if callbacks.continuation_progress() or self.store.input_status(task.id)["pending_count"]:
                    no_progress = 0
                else:
                    no_progress += 1
                    if no_progress >= NO_PROGRESS_LIMIT:
                        return self._block(task.id, "续跑未产生新的保存进度、证据或决定。")
        except AgentSuspended:
            incomplete = True
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
        except TaskSuspension as suspension:
            # Scope revocation and budget exhaustion are durable control stops.
            # A subagent suspension is translated to AgentSuspended before it
            # reaches here; this branch covers the parent and compact requests.
            incomplete = True
            current = self.store.get(task.id)
            if current.state == "running":
                self.store.command(
                    task.id, "paused", request_id=f"autonomy-stop-{task.id}",
                )
            self.store.add_message(
                task.id, kind="notice", text=f"自主执行已停止：{suspension.reason}",
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
                      cancel: threading.Event, ling_task: bool) -> bool:
        if cancel.is_set() or not ling_task:
            return False
        if str((final or {}).get("stopped") or "") != "complete":
            return False
        task = self.store.get(task_id)
        return (task.state == "running" and not task.blocked
                and not self.store.pending_interactions(task_id)
                and not self.store.unfinished_operations(task_id))

    def _block(self, task_id: str, reason: str) -> RunFnResult:
        task = self.store.get(task_id)
        updated = self.store.update_progress(
            task_id, expected_version=task.progress_version,
            changes={"blocked": reason},
        )
        self.store.add_message(task_id, kind="notice", text=f"任务无法继续：{reason}")
        if self.store.get(task_id).state == "running":
            self.store.command(
                task_id, "paused", request_id=f"blocked-{task_id}-{time.time_ns()}",
                expected_version=updated.control_version,
            )
        return RunFnResult(status="stalled", wait_reason=reason)
