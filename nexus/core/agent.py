"""Agent loop: model, tools, repeat until the model stops asking for tools.

This is the whole agent. Everything else in the package exists to serve this
function: assemble a request, stream a reply, run any tool calls, feed results
back, and stop when a reply contains no tool call.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..core import prompt
from ..core import compaction
from ..core.permission import ALLOW, ASK, DENY, Policy, default_policy, target
from ..core.protocol import request_body
from ..core.retry import RetryPolicy, should_retry
from ..core.workspace import Workspace
from ..model import (
    APIFamily,
    ModelError,
    ModelEvent,
    ModelGateway,
    ModelProfile,
    RuntimeCredentials,
    StreamLimits,
)
from ..tools import Registry, ToolContext
from ..tools.registry import ToolResult


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
        max_steps: int = 40,
        context_limit: int | None = None,
        approve: Approval | None = None,
        effort: str | None = None,
        retry: RetryPolicy | None = None,
        skills: str = "",
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
        self.stopped = False

    # --- public -----------------------------------------------------------

    def stop(self) -> None:
        """Ask the loop to stop at the next safe checkpoint.

        Safe checkpoints are before a model request and before each tool run, so
        a file write is never interrupted halfway through.
        """
        self.stopped = True

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
            try:
                async with _client() as client:
                    gateway = ModelGateway(client, limits=StreamLimits())
                    async for event in gateway.stream(
                        self.profile, body, credentials=self.credentials
                    ):
                        if event.type == "text_delta" and event.text:
                            parts.append(event.text)
                        elif event.type == "error":
                            return None
            except Exception:  # noqa: BLE001 -- summarising must never be fatal
                return None
            return "".join(parts) or None

        checkpoint = await ask()
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
        budget = prompt.Budget(
            context_limit=self.context_limit or self.profile.context_window_tokens or 64_000,
            output_reserve=self.profile.max_output_tokens or 8_000,
        )
        tools = self.registry.schema()
        tool_text = self.registry.declarations()
        async with _client() as client:
            gateway = ModelGateway(client, limits=StreamLimits())
            for step in range(1, self.max_steps + 1):
                if self.stopped:
                    result.stopped = "cancelled"
                    break
                system = prompt.build_system_prompt(
                    self.workspace,
                    tool_text,
                    platform=_platform(),
                    date=time.strftime("%Y-%m-%d"),
                    skills=self.skills,
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
                    trimmed, budget, fixed=prompt.estimate(system) + prompt.estimate(
                        json.dumps(tools)
                    )
                )
                body = request_body(
                    self.profile, system, context.messages, tools, effort=self.effort
                )
                turns: list[ModelEvent] = []
                text_parts: list[str] = []
                thinking_parts: list[str] = []
                calls: list[dict[str, Any]] = []
                produced = False
                attempt = 0
                while True:
                    attempt += 1
                    retry_after: float | None = None
                    failure: ModelError | None = None
                    async for event in gateway.stream(
                        self.profile, body, credentials=self.credentials
                    ):
                        turns.append(event)
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
                    await asyncio.sleep(delay)
                text = "".join(text_parts)
                thinking = "".join(thinking_parts)
                result.text = text
                result.thinking = thinking
                result.steps = step
                if not calls:
                    messages.append(
                        {"role": "assistant", "content": text,
                         "thinking": thinking or None,
                         "usage": result.usage or None}
                    )
                    result.stopped = "complete"
                    yield RunEvent("done", data={"usage": result.usage, "steps": step})
                    return
                messages.append(
                    {"role": "assistant", "content": text, "tool_calls": calls,
                     "thinking": thinking or None,
                     "usage": result.usage or None}
                )
                for call in calls:
                    if self.stopped:
                        result.stopped = "cancelled"
                        break
                    result.tool_calls += 1
                    yield RunEvent(
                        "tool_start",
                        data={"id": call["id"], "name": call["name"],
                              "arguments": _safe_json(call["arguments"])},
                    )
                    outcome = await self._invoke(call, yield_event=None)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "name": call["name"],
                            "content": outcome.content,
                        }
                    )
                    yield RunEvent(
                        "tool_result",
                        data={
                            "id": call["id"],
                            "name": call["name"],
                            "ok": outcome.ok,
                            "content": outcome.content,
                            "error": outcome.error_code,
                        },
                    )
            result.stopped = result.stopped or "max_steps"
            yield RunEvent("done", data={"usage": result.usage, "steps": result.steps,
                                         "stopped": result.stopped})

    async def _invoke(self, call: dict[str, Any], *, yield_event: Any) -> ToolResult:
        name = call["name"]
        try:
            arguments = json.loads(call["arguments"] or "{}")
        except ValueError:
            return ToolResult(False, "Arguments were not valid JSON.", error_code="bad_json")
        if not isinstance(arguments, dict):
            return ToolResult(False, "Arguments must be a JSON object.",
                              error_code="bad_arguments")
        spec = self.registry.get(name)
        if spec is None:
            return ToolResult(False, f"Unknown tool: {name}", error_code="unknown_tool")
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
        return await asyncio.to_thread(self.registry.execute, name, arguments, self.context)


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
        for key in ("input_tokens", "output_tokens", "total_tokens", "reasoning_tokens")
        if getattr(usage, key, None) is not None
    }
