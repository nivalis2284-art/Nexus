"""Prompt assembly and context budget.

Two ideas matter here:

1. The system prompt is *assembled at runtime*, not a constant. The workspace,
   git state and instruction files change; a static string would be wrong by
   the second run.
2. The transcript is trimmed from the newest message backwards. The newest
   message is always kept, even when it alone exceeds the budget, because
   failing a turn is worse than sending an oversized one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .workspace import Workspace

ROLE = """You are Nexus, a coding agent working inside the user's workspace.

Operate by acting, not by describing. When the user asks for a change, make it
with tools instead of printing code for them to paste.

Rules that matter:
- Read a file before editing it. Never guess at existing content.
- Prefer edit_file over write_file for files that already exist.
- Use the dedicated tools (read_file, glob, grep) instead of shell equivalents.
- Run the project's own checks when you change code, and report what you ran.
- If a tool fails, read the error and adapt. Do not repeat an identical call.
- Stop when the task is done. Do not summarise work you did not perform."""

ENVIRONMENT = """Environment:
- Platform: {platform}
- Date: {date}
- Shell: none (the bash tool takes an argument list, not a shell string)
{workspace}"""


@dataclass(frozen=True)
class Budget:
    context_limit: int = 64_000
    output_reserve: int = 8_000
    tool_reserve: int = 2_000

    @property
    def input_limit(self) -> int:
        limit = self.context_limit - self.output_reserve - self.tool_reserve
        return max(limit, 2_000)


@dataclass
class Context:
    instructions: str
    messages: list[dict[str, Any]]
    manifest: dict[str, Any] = field(default_factory=dict)


def estimate(text: str) -> int:
    """Conservative token estimate: bytes / 3 (CJK is ~3 bytes per token).

    Deliberately not presented as the provider's own count. A real usage number
    arrives after the call; this only decides how much to send.
    """
    return len(text.encode("utf-8")) // 3 + 1


def build_system_prompt(
    workspace: Workspace,
    tools: str,
    *,
    platform: str,
    date: str,
) -> str:
    sections = [ROLE]
    env = ENVIRONMENT.format(
        platform=platform,
        date=date,
        workspace=workspace.summary(),
    )
    sections.append(env)
    if workspace.instructions:
        sections.append(
            "# Project instructions\n"
            "The following comes from instruction files in the workspace. "
            "Follow them unless they conflict with your core rules.\n\n"
            + workspace.instructions
        )
    if tools:
        sections.append(
            "# Tools\n"
            "Call tools with their exact names. Arguments must match the schema.\n"
            + tools
        )
    return "\n\n".join(sections)


def select(messages: list[dict[str, Any]], budget: Budget, fixed: int) -> Context:
    """Keep the newest contiguous tail that fits.

    ``messages`` are already provider-shaped. The returned list is a slice, so
    callers must not assume it starts at the original first message.
    """
    limit = budget.input_limit - fixed
    used = 0
    selected: list[dict[str, Any]] = []
    dropped = 0
    for message in reversed(messages):
        cost = estimate(_text_of(message)) + 8
        if selected and used + cost > limit:
            dropped = len(messages) - len(selected)
            break
        selected.append(message)
        used += cost
    selected.reverse()
    if not selected and messages:
        selected = [messages[-1]]
        dropped = len(messages) - 1
    manifest = {
        "sent": len(selected),
        "dropped": dropped,
        "estimated_input_tokens": used + fixed,
        "limit": budget.input_limit,
    }
    return Context(instructions="", messages=selected, manifest=manifest)


def _text_of(message: dict[str, Any]) -> str:
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
                parts.append(str(block.get("content", "")))
    for key in ("arguments", "tool_call_id", "name"):
        value = message.get(key)
        if value:
            parts.append(str(value))
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict):
            parts.append(str(function.get("name", "")))
            parts.append(str(function.get("arguments", "")))
    return "".join(parts)
