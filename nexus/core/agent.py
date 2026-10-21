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
        # The durable turn output must not depend on the size of compacted history.
        self.new_messages: list[dict[str, Any]] = []

    # --- public -----------------------------------------------------------

    def stop(self) -> None:
        """Ask the loop to stop at the next safe checkpoint.

        Safe checkpoints are before a model request and before each tool run, so
        a file write is never interrupted halfway through.
        """
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

    async def _fold(
        self,
        original: list[dict[str, Any]],
        trimmed: list[dict[str, Any]],
        window: int,
    ) -> tuple[list[dict[str, Any]], compaction.CompactionReport]:
        """Ask the model for a checkpoint and return the replacement history.

        Runs as a normal one-shot request against the same profile, with no
        tools, so it cannot start new work. Any failure is reported as "no
        compaction" rather than propagated: a turn that runs long is better than
        a turn that dies because summarising failed.
        """
        # Eligibility is decided here rather than inside compaction.summarise,
        # because the summariser call is async and summarise() is pure.
        if compaction.approx_tokens(trimmed) < window * compaction.TRIGGER_RATIO:
            return trimmed, compaction.CompactionReport(reason="below threshold")
        if self.compaction_epochs >= self.max_epochs:
            return trimmed, compaction.CompactionReport(reason="epoch budget exhausted")
        try:
            compaction.assert_paired(trimmed)
        except compaction.CompactionBlocked as blocked:
            return trimmed, compaction.CompactionReport(reason=str(blocked))

        head, tail = compaction.split_for_summary(
            trimmed, int(window * compaction.RETAIN_RATIO)
        )
        if not head:
            return trimmed, compaction.CompactionReport(reason="nothing to compact")

        transcript = "\n\n".join(
            f"[{message.get('role')}] {compaction._text_of(message)[:4000]}"
            for message in head
        )
        prompt_text = transcript + "\n\n" + compaction.SUMMARY_INSTRUCTION

        async def ask() -> str | None:
            body = request_body(
                self.profile,
                "You are a compaction engine.",
                [{"role": "user", "content": prompt_text}],
                [],
                effort=None,
            )
            parts: list[str] = []
            budget_failure: Exception | None = None
            try:
                async with _client() as client:
                    gateway = ModelGateway(client, limits=StreamLimits())
                    async for event in gateway.stream(
                        self.profile, body, credentials=self.credentials,
                        request_budget=getattr(self.context, "model_budget", None),
                    ):
                        if event.type == "text_delta" and event.text:
                            parts.append(event.text)
                        elif event.type == "error":
                            return None
            except PermissionError as error:
                budget_failure = error
            except TaskSuspension as error:
                budget_failure = error
            except Exception:  # noqa: BLE001 -- summarising must never be fatal
                return None
            if budget_failure is not None:
                raise budget_failure
            return "".join(parts) or None

        try:
            checkpoint = await ask()
        except TaskSuspension as error:
            # A summariser does not get to turn revoked permission or an
            # exhausted budget into an ordinary "no compaction" fallback.
            raise AgentSuspended.from_tool(error) from error
        if not checkpoint:
            return trimmed, compaction.CompactionReport(reason="summariser returned nothing")

        summary_message = {
            "role": "user",
            "content": (
                f"{compaction.SUMMARY_TAG_OPEN}\n{checkpoint.strip()}\n"
                f"{compaction.SUMMARY_TAG_CLOSE}"
            ),
        }
        candidate = [summary_message, *tail]
        before = compaction.approx_tokens(trimmed)
        after = compaction.approx_tokens(candidate)
        if after >= before:
            return trimmed, compaction.CompactionReport(
                reason="no gain", before_tokens=before, after_tokens=after
            )
        return candidate, compaction.CompactionReport(
            summarised=True,
            epochs=self.compaction_epochs + 1,
            reason="ok",
            before_tokens=before,
            after_tokens=after,
        )

    async def run(self, messages: list[dict[str, Any]]) -> AsyncIterator[RunEvent]:
        """Yield events; mutate ``messages`` in place so callers can persist."""
        result = RunResult()
        self.new_messages = []
        budget = prompt.Budget(
            context_limit=self.context_limit or self.profile.context_window_tokens or 64_000,
            output_reserve=self.profile.max_output_tokens or 8_000,
        )
        tools = self.registry.schema()
        tool_text = self.registry.declarations()
        async with _client() as client:
            gateway = ModelGateway(client, limits=StreamLimits())
            steps = count(1) if self.max_steps is None else range(1, self.max_steps + 1)
            for step in steps:
                if self.stopped:
                    result.stopped = "cancelled"
                    break
                if self.before_model is not None:
                    self.before_model(messages)
                system = prompt.build_system_prompt(
                    self.workspace,
                    tool_text,
                    platform=_platform(),
                    date=time.strftime("%Y-%m-%d"),
                    skills=self.skills,
                    task_context=self.task_context,
                    interaction_context=self.interaction_context,
                )
                # Trim oversized tool results before budgeting. Deterministic,
                # no model call: see core.compaction for why this goes first.
                trimmed, compaction_report = compaction.prune(messages)
                self.last_compaction = compaction_report

                # If pruning was not enough, fold old history into a checkpoint.
                # Only attempted when a summariser is wired up, and a failure
                # here never stops the turn.
                window = self.context_limit or self.profile.context_window_tokens or 64_000
                if (
                    self.compaction_epochs < self.max_epochs
                    and compaction.approx_tokens(trimmed) >= window * compaction.TRIGGER_RATIO
                    and not self.stopped
                ):
                    folded, fold_report = await self._fold(messages, trimmed, window)
                    if fold_report.summarised:
                        messages[:] = folded
                        trimmed = folded
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

                context = prompt.select(
                    trimmed,
                    budget,
                    system_tokens=prompt.estimate(system),
                    tools_tokens=prompt.estimate(json.dumps(tools)),
                )
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
                text_parts: list[str] = []
                thinking_parts: list[str] = []
                calls: list[dict[str, Any]] = []
                finish_reason = ""
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
                    async for event in gateway.stream(
                        self.profile, body, credentials=self.credentials,
                        request_budget=getattr(self.context, "model_budget", None),
                    ):
                        if event.type == "text_delta" and event.text:
                            produced = True
                            text_parts.append(event.text)
                            yield RunEvent("delta", event.text)
                        elif event.type == "thinking_delta" and event.text:
                            produced = True
                            thinking_parts.append(event.text)
                            yield RunEvent("thinking", event.text)
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
                            result.usage = _usage(event.usage)
                        elif event.type == "complete":
                            finish_reason = event.finish_reason or ""
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
                                "role": "assistant", "content": "".join(text_parts),
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
                        {"role": "assistant", "content": text,
                         "thinking": thinking or None,
                         "usage": result.usage or None}
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
                    {"role": "assistant", "content": text, "tool_calls": calls,
                     "thinking": thinking or None,
                     "usage": result.usage or None}
                )
                for call_index, call in enumerate(calls):
                    result.tool_calls += 1
                    yield RunEvent(
                        "tool_start",
                        data={"id": call["id"], "name": call["name"],
                              "arguments": _safe_json(call["arguments"])},
                    )
                    if self.stopped:
                        result.stopped = "cancelled"
                        outcome = ToolResult(False, "Not executed: run cancelled.",
                                             error_code="cancelled")
                    else:
                        try:
                            outcome = await self._invoke(call, yield_event=None)
                        except TaskSuspension as tool_suspension:
                            suspended = AgentSuspended.from_tool(tool_suspension)
                            suspended.details.setdefault("current_call", call)
                            suspended.details.setdefault("remaining_calls", calls[call_index + 1:])
                            raise suspended from tool_suspension
                        except AgentSuspended as suspended:
                            # The current tool call and every call after it remain
                            # unresolved. The task runtime persists the whole
                            # pending batch so no fabricated result is replayed.
                            suspended.details.setdefault("current_call", call)
                            suspended.details.setdefault("remaining_calls", calls[call_index + 1:])
                            raise
                    tool_message = {
                            "role": "tool",
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

    async def _invoke(self, call: dict[str, Any], *, yield_event: Any) -> ToolResult:
        name = call["name"]
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


def _usage(usage: Any) -> dict[str, Any]:
    return {
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
