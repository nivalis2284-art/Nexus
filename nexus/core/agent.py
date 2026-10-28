"""Agent loop: model, tools, repeat until the model stops asking for tools.

This is the whole agent. Everything else in the package exists to serve this
function: assemble a request, stream a reply, run any tool calls, feed results
back, and stop when a reply contains no tool call.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from itertools import count
from typing import Any

from ..core import compaction, prompt
from ..core.permission import ASK, DENY, Policy, default_policy, target
from ..core.protocol import request_body
from ..core.retry import RetryPolicy, should_retry
from ..core.workspace import Workspace
from ..model import (
    ModelError,
    ModelGateway,
    ModelProfile,
    RuntimeCredentials,
    StreamLimits,
)
from ..model.request_history import RequestHistory
from ..tools import Registry, ToolContext
from ..tools.registry import TaskSuspension, ToolResult

# Provider-specific values that mean the model finished the requested turn.
# The call variants are only normal when the parser actually produced calls.
_CALL_FINISH_REASONS = frozenset({"tool_calls", "tool_use", "function_call"})
_NORMAL_FINISH_REASONS = frozenset({"stop", "end_turn", "stop_sequence", "completed"})


@dataclass
class ApprovalRequest:
    tool: str
    arguments: dict[str, Any]
    value: str


Approval = Callable[[ApprovalRequest], Awaitable[bool]]


@dataclass
class RunEvent:
    """What the caller sees. Persistence and transport stay outside."""

    type: str
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    text: str = ""
    thinking: str = ""
    steps: int = 0
    tool_calls: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    stopped: str = ""
    finish_reason: str = ""


class AgentSuspended(Exception):
    """The task controller wants this run to yield to the user.

    This is deliberately separate from a tool failure: an approval or question
    that has not received an answer must leave the operation pending rather than
    turning it into a failed operation the model is encouraged to retry.
    """

    def __init__(self, reason: str = "等待用户", **details: Any) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details

    @property
    def control(self) -> str:
        """A controller-facing reason for a durable, resumable pause."""
        return str(self.details.get("kind") or "question")

    @classmethod
    def from_tool(cls, error: TaskSuspension) -> AgentSuspended:
        return cls(error.reason, **error.details)


class ApprovalRequired(AgentSuspended):
    """A task needs a persisted decision before dispatching a tool."""

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__("等待工具审批", kind="approval", request=request)


class QuestionRequired(AgentSuspended):
    """A task is waiting for an answer that must survive a restart."""

    def __init__(self, questions: list[dict[str, Any]]) -> None:
        super().__init__("等待用户回答", kind="question", questions=questions)


class Agent:
    def __init__(
        self,
        *,
        profile: ModelProfile,
        credentials: RuntimeCredentials,
        registry: Registry,
        context: ToolContext,
        workspace: Workspace,
        policy: Policy | None = None,
        max_steps: int | None = 40,
        context_limit: int | None = None,
        approve: Approval | None = None,
        effort: str | None = None,
        retry: RetryPolicy | None = None,
        skills: str = "",
        cancel_event: threading.Event | None = None,
        task_context: str = "",
        on_message: Callable[[dict[str, Any]], None] | None = None,
        execute_prepared: Callable[[Any], ToolResult] | None = None,
        on_tool_intent: Callable[[str, Any, Any], None] | None = None,
        on_tool_result: Callable[[dict[str, Any], dict[str, Any], ToolResult], None] | None = None,
        before_model: Callable[[list[dict[str, Any]]], None] | None = None,
        interaction_context: str = "",
        role: str = prompt.ROLE,
        owner_context: str = "",
        request_history: RequestHistory | None = None,
        request_scope: dict[str, Any] | None = None,
        plugins: Any = None,
        plugin_scope: dict[str, Any] | None = None,
    ) -> None:
        self.profile = profile
        self.credentials = credentials
        self.registry = registry
        self.context = context
        self.workspace = workspace
        self.policy = policy or default_policy("ask")
        self.max_steps = max_steps
        self.context_limit = context_limit
        self.approve = approve
        self.effort = effort
        self.retry = retry or RetryPolicy()
        # Rendered skill manifest (names + when-to-use, never bodies).
        self.skills = skills
        self.last_compaction: compaction.CompactionReport | None = None
        self.compaction_epochs = 0
        self.max_epochs = 16
        self.cancel_event = cancel_event or threading.Event()
        self.context.cancel_event = self.cancel_event
        self.task_context = task_context
        self.on_message = on_message
        self.execute_prepared = execute_prepared
        self.on_tool_intent = on_tool_intent
        self.on_tool_result = on_tool_result
        self.before_model = before_model
        self.interaction_context = interaction_context
        self.role = role
        self.owner_context = owner_context
        self.request_history = request_history
        self.request_scope = dict(request_scope or {})
        self.plugins = plugins
        self.plugin_scope = dict(plugin_scope or {})
        self.plugin_context = ""
        self.request_materials: list[dict[str, Any]] = []
        self.request_plugins: list[dict[str, Any]] = []
        # The durable turn output must not depend on the size of compacted history.
        self.new_messages: list[dict[str, Any]] = []
        self.context.agent = self
        self._usage_anchor = None

    def _request_scope(self, kind: str | None = None) -> dict[str, Any]:
        scope = dict(self.request_scope)
        if kind is not None:
            scope["kind"] = kind
        scope["request_materials"] = self.request_materials
        scope["request_plugins"] = self.request_plugins
        return scope

    # --- public -----------------------------------------------------------

    def stop(self) -> None:
        """Cancel model reads and subsequent dispatch; atomic file writes settle."""
        self.cancel_event.set()

    @property
    def stopped(self) -> bool:
        return self.cancel_event.is_set()

    def _record(self, messages: list[dict[str, Any]], message: dict[str, Any]) -> None:
        messages.append(message)
        self.new_messages.append(message)
        if self.on_message is not None:
            self.on_message(message)

    def close_pending_calls(self, messages: list[dict[str, Any]]) -> None:
        """Keep replay valid if transport fails while a tool exchange is open."""
        pending: dict[str, dict[str, Any]] = {}
        for message in self.new_messages:
            for call in message.get("tool_calls") or []:
                pending[call["id"]] = call
            if message.get("role") == "tool":
                pending.pop(message.get("tool_call_id"), None)
        for call in pending.values():
            self._record(messages, {
                "role": "tool", "tool_call_id": call["id"], "name": call["name"],
                "arguments": _safe_json(call["arguments"]),
                "content": "No result was recorded: run interrupted.",
                "ok": False, "error": "interrupted",
            })

    def seal_pending_calls(self, messages: list[dict[str, Any]]) -> None:
        """Mark unfinished task calls as interrupted without claiming they ran.

        Task execution persists its own operation facts. This helper only keeps
        the provider-facing transcript legal when a run is being closed for
        recovery. ``unknown`` is intentionally different from a tool failure.
        """
        pending: dict[str, dict[str, Any]] = {}
        for message in self.new_messages:
            for call in message.get("tool_calls") or []:
                pending[call["id"]] = call
            if message.get("role") == "tool":
                pending.pop(message.get("tool_call_id"), None)
        for call in pending.values():
            self._record(messages, {
                "role": "tool", "tool_call_id": call["id"], "name": call["name"],
                "arguments": _safe_json(call["arguments"]),
                "content": "No result was recorded: operation outcome is unknown.",
                "ok": False, "error": "unknown",
            })

    async def _stream(self, gateway, body, **kwargs):
        """Close a live HTTP iterator when the owner stops, even during silence."""
        from contextlib import suppress

        stream = gateway.stream(self.profile, body, credentials=self.credentials, **kwargs)
        pending = None
        try:
            while not self.stopped:
                pending = asyncio.create_task(anext(stream))
                while not pending.done() and not self.stopped:
                    await asyncio.wait({pending}, timeout=0.05)
                if self.stopped:
                    break
                try:
                    event = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                yield event
        finally:
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError, StopAsyncIteration):
                    await pending
            await stream.aclose()

    async def _fold(self, original, trimmed, window):
        """Summarize a complete prefix; never cut a requirement before summarizing."""
        limit = getattr(self, "_history_limit", int(window * compaction.TRIGGER_RATIO))
        try:
            compaction.assert_paired(original)
        except compaction.CompactionBlocked as error:
            return trimmed, compaction.CompactionReport(reason=str(error))
        head, tail = compaction.split_for_summary(original, max(1, int(limit * compaction.RETAIN_RATIO)))
        if not head:
            return trimmed, compaction.CompactionReport(reason="A single message exceeds the available context; split the input into smaller parts.")
        transcript = "\n\n".join(
            f"[{message.get('role')}] {compaction._text_of(message)}" for message in head
        )
        from .effort import output_reserve
        room = window - output_reserve(self.profile) - compaction.estimate(compaction.SUMMARY_INSTRUCTION) - 128
        if room <= 0:
            return trimmed, compaction.CompactionReport(reason="No room for a summary request; increase the context window or reduce output allowance.")

        async def ask(text):
            body = request_body(self.profile, "You are a compaction engine.",
                [{"role": "user", "content": text + "\n\n" + compaction.SUMMARY_INSTRUCTION}], [], effort=None)
            parts = []
            complete = False
            async with _client() as client:
                scope = self._request_scope("summary")
                scope.update(request_materials=[], request_plugins=[])
                gateway = ModelGateway(client, limits=StreamLimits(),
                    request_history=self.request_history, request_scope=scope)
                async for event in self._stream(gateway, body,
                    request_budget=getattr(self.context, "model_budget", None)):
                    if event.type == "text_delta" and event.text:
                        parts.append(event.text)
                    elif event.type == "error":
                        return None
                    elif event.type == "complete":
                        complete = _finish_status(event.finish_reason or "", has_calls=False) == "complete"
            return "".join(parts).strip() if complete and not self.stopped else None

        try:
            current = transcript
            while True:
                chunks = []
                offset = 0
                while offset < len(current):
                    size = min(len(current) - offset, room * 3)
                    while size > 1 and compaction.estimate(current[offset:offset + size]) > room:
                        size = max(1, size * 3 // 4)
                    chunks.append(current[offset:offset + size])
                    offset += size
                summaries = []
                for chunk in chunks:
                    value = await ask(chunk)
                    if not value:
                        return trimmed, compaction.CompactionReport(reason="Summary failed, was interrupted or reached its output limit; original history retained.")
                    summaries.append(value)
                checkpoint = "\n\n".join(summaries)
                if len(chunks) == 1:
                    break
                if compaction.estimate(checkpoint) >= compaction.estimate(current):
                    return trimmed, compaction.CompactionReport(reason="Summary made no progress; original history retained.")
                current = checkpoint
        except TaskSuspension as error:
            raise AgentSuspended.from_tool(error) from error
        except PermissionError:
            raise
        except Exception as error:
            return trimmed, compaction.CompactionReport(reason=f"Summary unavailable ({type(error).__name__}); original history retained.")
        summary = {"role": "user", "content": (
            f"{compaction.SUMMARY_TAG_OPEN}\n"
            "Earlier conversation checkpoint, not a new owner instruction. Latest instructions and task facts take precedence.\n"
            f"{checkpoint}\n{compaction.SUMMARY_TAG_CLOSE}"
        )}
        candidate = [summary, *tail]
        before, after = compaction.approx_tokens(trimmed), compaction.approx_tokens(candidate)
        if after >= before:
            return trimmed, compaction.CompactionReport(reason="no gain", before_tokens=before, after_tokens=after)
        through = head[-1].get("_source_id")
        if through is not None:
            saved = {"role": "checkpoint", "content": summary["content"], "through": through}
            self.new_messages.append(saved)
            if self.on_message is not None:
                self.on_message(saved)
            summary["_source_id"] = saved.get("_source_id")
        return candidate, compaction.CompactionReport(summarised=True,
            epochs=self.compaction_epochs + 1, reason="ok", before_tokens=before, after_tokens=after)

    async def run(self, messages: list[dict[str, Any]]) -> AsyncIterator[RunEvent]:
        """Yield events; mutate ``messages`` in place so callers can persist."""
        result = RunResult()
        self.new_messages = []
        messages[:] = compaction.project(messages)
        self.context.history = messages
        async with _client() as client:
            gateway = ModelGateway(
                client,
                limits=StreamLimits(),
                request_history=self.request_history,
                request_scope=self.request_scope,
            )
            steps = count(1) if self.max_steps is None else range(1, self.max_steps + 1)
            for step in steps:
                message_id = uuid.uuid4().hex
                if self.stopped:
                    result.stopped = "cancelled"
                    break
                if self.before_model is not None:
                    self.before_model(messages)
                from .workspace import refresh
                self.workspace = refresh(self.workspace, getattr(self.context, "instruction_paths", ()))
                from .effort import output_reserve
                budget = prompt.Budget(
                    context_limit=self.context_limit or self.profile.context_window_tokens or 64_000,
                    output_reserve=output_reserve(self.profile, self.effort),
                )
                prepared = messages
                if self.plugins is not None:
                    try:
                        prepared = self.plugins.prepare_agent(self, messages)
                    except ValueError as error:
                        # A required plugin context can stop request preparation
                        # before the gateway is reached. Keep that attempt
                        # separate from sent requests and from the model's view.
                        scope = self._request_scope()
                        scope["request_materials"] = []
                        scope["request_plugins"] = [{
                            "status": "failed",
                            "phase": "prepare",
                            "error": str(error),
                        }]
                        if self.request_history is not None:
                            record_id = self.request_history.record(
                                None,
                                scope,
                                self.profile.model,
                                self.profile.api_family.value,
                                status="prepared_failed",
                            )
                            self.request_history.update(
                                record_id, "prepared_failed", "plugin_context: " + str(error)
                            )
                        raise
                # Trim oversized tool results before budgeting. Deterministic,
                # no model call: see core.compaction for why this goes first.
                stable, compaction_report = compaction.prune(prepared)
                self.last_compaction = compaction_report

                self.request_materials = list(getattr(self, "request_materials", []) or [])
                self.request_plugins = list(getattr(self, "request_plugins", []) or [])
                tools = self.registry.schema()
                tool_text = self.registry.declarations()
                interaction_context = self.interaction_context
                plugin_context = getattr(self, "plugin_context", "")
                if plugin_context:
                    interaction_context = (
                        f"{interaction_context}\n{plugin_context}"
                        if interaction_context else plugin_context
                    )
                system = prompt.build_system_prompt(
                    self.workspace,
                    tool_text,
                    platform=_platform(),
                    date=time.strftime("%Y-%m-%d"),
                    role=self.role,
                    owner_instructions=self.owner_context,
                    skills=self.skills,
                    task_context=self.task_context,
                    interaction_context=interaction_context,
                    permission_context=prompt.permission_text(self.policy),
                )
                overhead = prompt.estimate(system) + prompt.estimate(json.dumps(tools))
                self._history_limit = budget.input_limit - overhead
                route = (self.profile.api_family, self.profile.model, self.profile.base_url)
                estimated = overhead + compaction.approx_tokens(stable)
                pressure = estimated
                if self._usage_anchor is not None:
                    old_route, old_overhead, old_messages, old_estimate, actual = self._usage_anchor
                    if route == old_route and overhead == old_overhead and stable[:len(old_messages)] == old_messages:
                        pressure = max(estimated, actual + estimated - old_estimate)
                self._history_limit -= max(0, pressure - estimated)
                # Preserve old goals before the final input selection would drop them.
                window = self.context_limit or self.profile.context_window_tokens or 64_000
                if (
                    self.compaction_epochs < self.max_epochs
                    and compaction.approx_tokens(stable) >= min(window * compaction.TRIGGER_RATIO, self._history_limit)
                    and not self.stopped
                ):
                    # Only permanent history can become a durable checkpoint.
                    folded, fold_report = await self._fold(messages, stable, window)
                    if fold_report.summarised:
                        messages[:] = folded
                        prepared = folded
                        self.compaction_epochs = fold_report.epochs
                        self.last_compaction = fold_report
                        yield RunEvent(
                            "compacted",
                            data={
                                "epochs": fold_report.epochs,
                                "before": fold_report.before_tokens,
                                "after": fold_report.after_tokens,
                            },
                        )
                    elif fold_report.reason:
                        self.last_compaction = fold_report

                if self.stopped:
                    result.stopped = "cancelled"
                    break
                trimmed, _ = compaction.prune(prepared)
                context = prompt.select(
                    trimmed,
                    budget,
                    system_tokens=prompt.estimate(system),
                    tools_tokens=prompt.estimate(json.dumps(tools)),
                )
                calibrated_input = context.manifest["estimated_input_tokens"] + max(0, pressure - estimated)
                if context.manifest["dropped"] > len(trimmed) - len(compaction.paired(trimmed)) or calibrated_input > budget.input_limit:
                    reason = (self.last_compaction.reason if self.last_compaction else "")
                    yield RunEvent("error", "Context capacity exceeded; history retained. " + reason + " Reduce the input/tools or increase the model context window.")
                    return
                yield RunEvent(
                    "context",
                    data={
                        "system": context.manifest["breakdown"]["system"],
                        "tools": context.manifest["breakdown"]["tools"],
                        "messages": context.manifest["breakdown"]["messages"],
                        "window": budget.context_limit,
                        "limit": context.manifest["limit"],
                        "dropped": context.manifest["dropped"],
                        "source": "estimate",
                    },
                )
                body = request_body(
                    self.profile, system, context.messages, tools, effort=self.effort
                )
                request_estimate = overhead + compaction.approx_tokens(context.messages)
                text_parts: list[str] = []
                thinking_parts: list[str] = []
                calls: list[dict[str, Any]] = []
                finish_reason = ""
                native = None
                # Usage belongs to one response. A tool step followed by a
                # provider that reports no usage must not inherit the previous
                # step's counts.
                result.usage = {}
                produced = False
                attempt = 0
                while True:
                    attempt += 1
                    retry_after: float | None = None
                    failure: ModelError | None = None
                    async for event in self._stream(gateway, body,
                        request_budget=getattr(self.context, "model_budget", None),
                        scope=self._request_scope(),
                    ):
                        if event.type == "text_delta" and event.text:
                            produced = True
                            text_parts.append(event.text)
                            yield RunEvent("delta", event.text, data={"message_id": message_id})
                        elif event.type == "thinking_delta" and event.text:
                            produced = True
                            thinking_parts.append(event.text)
                            yield RunEvent("thinking", event.text, data={"message_id": message_id})
                        elif event.type == "tool_call" and event.tool_call:
                            produced = True
                            call = event.tool_call
                            calls.append(
                                {
                                    "id": call.id,
                                    "name": call.name,
                                    "arguments": call.arguments_json,
                                }
                            )
                        elif event.type == "usage" and event.usage:
                            result.usage = _usage(event.usage, self.profile.api_family)
                        elif event.type == "complete":
                            finish_reason = event.finish_reason or ""
                            if event.usage is not None:
                                final_usage = _usage(event.usage)
                                if final_usage:
                                    result.usage = {**result.usage, **final_usage,
                                                    "api_family": self.profile.api_family.value}
                            from .replay import capture
                            native = capture(self.profile, event.continuation)
                            actual = result.usage.get("input_tokens")
                            if actual is not None:
                                if self.profile.api_family.value == "anthropic_messages":
                                    actual += (result.usage.get("cached_input_tokens") or 0) + (result.usage.get("cache_creation_input_tokens") or 0)
                                from copy import deepcopy
                                self._usage_anchor = (route, overhead, deepcopy(context.messages), request_estimate, actual)
                        elif event.type == "error":
                            failure = event.error or ModelError("provider_error", "")
                            if event.error:
                                retry_after = getattr(event.error, "retry_after", None)
                    if failure is None:
                        break
                    # Only a failure before any visible output can be retried: a
                    # replay after partial text would duplicate what is on screen.
                    if not should_retry(
                        code=failure.code,
                        status=failure.status_code,
                        reason=failure.reason,
                        produced_output=produced,
                    ) or attempt > self.retry.max_retries:
                        result.stopped = failure.message or "model error"
                        if text_parts or thinking_parts or calls:
                            self._record(messages, {
                                "role": "assistant", "message_id": message_id, "content": "".join(text_parts),
                                "thinking": "".join(thinking_parts) or None,
                                "tool_calls": calls,
                            })
                            for call in calls:
                                self._record(messages, {
                                    "role": "tool", "tool_call_id": call["id"],
                                    "name": call["name"], "arguments": _safe_json(call["arguments"]),
                                    "content": "Not executed: model stream failed.",
                                    "ok": False, "error": "model_error",
                                })
                        yield RunEvent("error", result.stopped)
                        return
                    delay = self.retry.delay_for(attempt, retry_after)
                    yield RunEvent(
                        "retrying",
                        data={
                            "attempt": attempt,
                            "max": self.retry.max_retries,
                            "delay": round(delay, 2),
                            "code": failure.code,
                            "status": failure.status_code,
                        },
                    )
                    # Reset the partial capture: this attempt is being discarded.
                    text_parts.clear()
                    thinking_parts.clear()
                    calls.clear()
                    # A failed attempt's usage is not the successful response's
                    # usage. Keep the retried answer internally consistent.
                    result.usage = {}
                    deadline = time.monotonic() + delay
                    while not self.stopped and time.monotonic() < deadline:
                        await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))
                    if self.stopped:
                        break
                text = "".join(text_parts)
                thinking = "".join(thinking_parts)
                result.text = text
                result.thinking = thinking
                result.finish_reason = finish_reason
                result.steps = step
                if not calls:
                    self._record(messages,
                        {"role": "assistant", "message_id": message_id, "content": text,
                         "thinking": thinking or None,
                         "usage": result.usage or None, **({"native": native} if native else {})}
                    )
                    result.stopped = (
                        "cancelled"
                        if self.stopped
                        else _finish_status(finish_reason, has_calls=False)
                    )
                    yield RunEvent("done", data={"usage": result.usage, "steps": step,
                                                 "stopped": result.stopped,
                                                 "finish_reason": finish_reason or None})
                    return
                self._record(messages,
                    {"role": "assistant", "message_id": message_id, "content": text, "tool_calls": calls,
                     "thinking": thinking or None,
                     "usage": result.usage or None, **({"native": native} if native else {})}
                )
                for call_index, call in enumerate(calls):
                    result.tool_calls += 1
                    yield RunEvent(
                        "tool_start",
                        data={"id": call["id"], "parent_message_id": message_id, "name": call["name"],
                              "arguments": _safe_json(call["arguments"])},
                    )
                    if self.stopped:
                        result.stopped = "cancelled"
                        outcome = ToolResult(False, "Not executed: run cancelled.",
                                             error_code="cancelled")
                    else:
                        try:
                            outcome = await self._invoke(call)
                        except TaskSuspension as tool_suspension:
                            suspended = AgentSuspended.from_tool(tool_suspension)
                            suspended.details.setdefault("current_call", call)
                            suspended.details.setdefault("remaining_calls", calls[call_index + 1:])
                            raise suspended from tool_suspension
                        except AgentSuspended as suspended:
                            if suspended.control == "children":
                                self._record(messages, {
                                    "role": "tool", "tool_call_id": call["id"], "name": call["name"],
                                    "parent_message_id": message_id,
                                    "arguments": _safe_json(call["arguments"]),
                                    "content": "Parent yielded while awaiting subagent results. Results return automatically.",
                                    "ok": True, "error": "",
                                })
                            # Unexecuted calls remain pending across suspension.
                            suspended.details.setdefault("current_call", call)
                            suspended.details.setdefault("remaining_calls", calls[call_index + 1:])
                            raise
                    tool_message = {
                            "role": "tool",
                            "parent_message_id": message_id,
                            "tool_call_id": call["id"],
                            "name": call["name"],
                            "content": outcome.content,
                            "arguments": _safe_json(call["arguments"]),
                            "ok": outcome.ok,
                            "error": outcome.error_code,
                        }
                    if outcome.data.get("subagent"):
                        tool_message["details"] = outcome.data
                    self._record(messages, tool_message)
                    if self.on_tool_result is not None:
                        self.on_tool_result(call, _safe_json(call["arguments"]), outcome)
                    yield RunEvent(
                        "tool_result",
                        data={
                            "id": call["id"],
                            "parent_message_id": message_id,
                            "name": call["name"],
                            "arguments": _safe_json(call["arguments"]),
                            "ok": outcome.ok,
                            "content": outcome.content,
                            "error": outcome.error_code,
                            "details": tool_message.get("details"),
                        },
                    )
            result.stopped = result.stopped or "max_steps"
            yield RunEvent("done", data={"usage": result.usage, "steps": result.steps,
                                         "stopped": result.stopped,
                                         "finish_reason": result.finish_reason or None})

    async def _invoke(self, call: dict[str, Any]) -> ToolResult:
        name = call["name"]
        self.context.call_id = str(call.get("id") or "")
        try:
            arguments = json.loads(call["arguments"] or "{}")
        except ValueError:
            return ToolResult(False, "Arguments were not valid JSON.", error_code="bad_json")
        if not isinstance(arguments, dict):
            return ToolResult(False, "Arguments must be a JSON object.",
                              error_code="bad_arguments")
        prepared = self.registry.prepare(name, arguments, self.context)
        if isinstance(prepared, ToolResult):
            return prepared
        if name in {"read_file", "write_file", "edit_file", "apply_patch", "glob", "grep"}:
            from .workspace import refresh
            paths = set(getattr(self.context, "instruction_paths", ()))
            paths.add(str(arguments.get("path") or "."))
            self.context.instruction_paths = paths
            updated = refresh(self.workspace, paths)
            if updated.instructions != self.workspace.instructions:
                self.workspace = updated
                if not prepared.spec.read_only:
                    return ToolResult(False,
                        "Project instructions changed or a new directory scope applies. Read these before retrying this edit:\n"
                        + updated.instructions, error_code="instructions_updated")
        decision = self.policy.decide(name, target(name, arguments))
        if decision == DENY:
            return ToolResult(False, f"Blocked by policy: {name}", error_code="denied")
        if decision == ASK:
            if self.approve is None:
                return ToolResult(
                    False,
                    f"'{name}' needs approval, but no approval channel is connected.",
                    error_code="needs_approval",
                )
            allowed = await self.approve(
                ApprovalRequest(name, arguments, target(name, arguments))
            )
            if not allowed:
                return ToolResult(False, "The user declined this call.", error_code="declined")
        if self.stopped:
            return ToolResult(False, "Not executed: run cancelled.", error_code="cancelled")
        executor = self.execute_prepared or (
            lambda ready: self.registry.execute_prepared(ready, self.context)
        )
        if self.on_tool_intent is not None:
            await asyncio.to_thread(self.on_tool_intent, str(call.get("id") or ""), prepared, self.context)
        return await asyncio.to_thread(executor, prepared)


def _client():
    import httpx

    return httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=15.0),
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
        follow_redirects=False,
    )


def _platform() -> str:
    import platform as _p

    return f"{_p.system()} {_p.release()} ({_p.machine()})"


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _usage(usage: Any, family: Any = None) -> dict[str, Any]:
    result = {
        key: getattr(usage, key)
        for key in (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "reasoning_tokens",
            # Cache accounting matters for an honest reading of a long session:
            # most of a repeated prompt is served from cache on every turn.
            "cached_input_tokens",
            "cache_creation_input_tokens",
        )
        if getattr(usage, key, None) is not None
    }
    if family is not None:
        result["api_family"] = str(getattr(family, "value", family))
    return result


def _finish_status(finish_reason: str, *, has_calls: bool) -> str:
    """Translate a provider terminal reason into the run's stop status.

    A normal terminal reason is still ``complete``. Output limits, filtering,
    and a call reason with no parsed calls are real reasons the turn did not
    complete normally, and callers must be able to distinguish them.
    """
    if not finish_reason:
        return "complete"
    if finish_reason in _CALL_FINISH_REASONS:
        return "complete" if has_calls else finish_reason
    if finish_reason in _NORMAL_FINISH_REASONS:
        return "complete"
    return finish_reason
