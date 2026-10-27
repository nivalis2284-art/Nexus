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

from . import compaction
from .workspace import Workspace

ROLE_RULES = """Operate by acting, not by describing. When the user asks for a change, make it
with tools instead of printing code for them to paste.

Rules that matter:
- Read a file before editing it. Never guess at existing content.
- Prefer edit_file over write_file for files that already exist.
- Use the dedicated tools (read_file, glob, grep) instead of shell equivalents.
- Run the project's own checks when you change code, and report what you ran.
- If a tool fails, read the error and adapt. Do not repeat an identical call.
- Stop when the task is done. Do not summarise work you did not perform."""

ROLE = "You are Nexus, a coding agent working inside the user's workspace.\n\n" + ROLE_RULES

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
    return compaction.estimate(text)


def build_system_prompt(
    workspace: Workspace,
    tools: str,
    *,
    platform: str,
    date: str,
    role: str = ROLE,
    owner_instructions: str = "",
    skills: str = "",
    task_context: str = "",
    interaction_context: str = "",
) -> str:
    sections = [owner_instructions, role] if owner_instructions else [role]
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
    if skills:
        # Only names, descriptions and trigger hints. Bodies are read on demand
        # with read_skill, so an unused skill costs one line, not a page.
        sections.append(
            "# Skills\n"
            "Reusable procedures installed in this environment. When one applies, "
            "call read_skill with its exact name before doing the work.\n"
            + skills
        )
    if interaction_context:
        sections.append("# Continuing interaction\n" + interaction_context)
    if task_context:
        sections.append(
            "# Persistent task state\n"
            "This state is maintained by the task controller. It outranks old model summaries. "
            "Follow the latest user goal and check actual results before claiming success. "
            "Progress, plans, and supporting records are optional; update them when useful.\n\n"
            + task_context
        )
    if tools:
        sections.append(
            "# Tools\n"
            "Call tools with their exact names. Arguments must match the schema.\n"
            + tools
        )
    return "\n\n".join(sections)


def select(
    messages: list[dict[str, Any]],
    budget: Budget,
    fixed: int = 0,
    *,
    system_tokens: int | None = None,
    tools_tokens: int | None = None,
) -> Context:
    """Keep the newest contiguous tail that fits.

    ``messages`` are already provider-shaped. The returned list is a slice, so
    callers must not assume it starts at the original first message.
    Tool calls and their results are selected as one atomic interaction. Stray
    or unfinished tool messages are omitted from model input.

    ``fixed`` is the old combined budget for the prompt and tool schemas. New
    callers pass ``system_tokens`` and ``tools_tokens`` separately so the UI can
    show where the context is actually going; ``fixed`` stays supported so an
    older call site keeps working.
    """
    if system_tokens is None and tools_tokens is None:
        system_tokens, tools_tokens = fixed, 0
    system_tokens = system_tokens or 0
    tools_tokens = tools_tokens or 0
    overhead = system_tokens + tools_tokens

    limit = budget.input_limit - overhead
    safe = compaction.paired(messages)
    used = 0
    selected_groups: list[list[dict[str, Any]]] = []
    units = compaction.message_units(safe)
    for start, end, _ in reversed(units):
        group = safe[start:end]
        cost = compaction.approx_tokens(group)
        if selected_groups and used + cost > limit:
            break
        selected_groups.append(group)
        used += cost
    selected = [message for group in reversed(selected_groups) for message in group]
    if not selected and safe:
        last_start, last_end, _ = units[-1]
        selected = safe[last_start:last_end]
    dropped = len(messages) - len(selected)
    manifest = {
        "sent": len(selected),
        "dropped": dropped,
        "estimated_input_tokens": used + overhead,
        "limit": budget.input_limit,
        "breakdown": {
            "system": system_tokens,
            "tools": tools_tokens,
            "messages": used,
        },
    }
    return Context(instructions="", messages=selected, manifest=manifest)


# An image costs a fixed conservative allowance. Exact counts depend on the
# provider's tiling of the image, which nobody can compute before sending, so
# this is deliberately a rough upper bound and is presented as an estimate.
IMAGE_TOKEN_ALLOWANCE = compaction.IMAGE_TOKEN_ALLOWANCE


def image_tokens(message: dict[str, Any]) -> int:
    return compaction.image_tokens(message)


def _text_of(message: dict[str, Any]) -> str:
    return compaction._text_of(message)
