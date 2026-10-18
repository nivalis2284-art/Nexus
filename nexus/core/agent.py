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
        self.stopped = False

    # --- public -----------------------------------------------------------

    def stop(self) -> None:
        """Ask the loop to stop at the next safe checkpoint.

        Safe checkpoints are before a model request and before each tool run, so
        a file write is never interrupted halfway through.
        """
        self.stopped = True

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
                )
                context = prompt.select(
                    messages, budget, fixed=prompt.estimate(system) + prompt.estimate(
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
