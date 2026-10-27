"""Prompt assembly and context budget.

Two ideas matter here:

1. The system prompt is *assembled at runtime*, not a constant. The workspace,
   git state and instruction files change; a static string would be wrong by
   the second run.
2. Selection measures atomic tool exchanges. The Agent compacts before older
   messages would be dropped and reports oversized input without deleting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import compaction
from .workspace import Workspace

ROLE_RULES = """Work from evidence and keep moving. When the user asks for a change, make it
with the available tools instead of printing code for them to paste. Continue until
the requested work is complete or a concrete blocker needs the user.

Rules that matter:
- Read a file before editing it. Never guess at existing content.
- Prefer edit_file over write_file for files that already exist.
- Use the dedicated file tools when they are available.
- Run the project's own checks when you change code, and report what you ran.
- If a tool fails, read the error and adapt. Retry when the failure is transient or
  the call or input has been corrected. Do not repeat an unchanged failed call
  without a reason to expect a different result.
- Stop when the task is done. Report only work you actually performed."""

ROLE = "You are Nexus, a coding agent working inside the user's workspace.\n\n" + ROLE_RULES

ENVIRONMENT = """Environment:
- Platform: {platform}
- Date: {date}{shell}
{workspace}"""

INSTRUCTION_PRIORITY = """# Instruction sources and priority
- Owner instructions are the owner's persistent preferences from the Nexus data
  directory. They are not repository files.
- Project instructions are labeled files found from the filesystem root down to this
  workspace and in directories already touched in this run. Each block names its
  absolute path and scope; a nested file covers that directory and below.
- The owner is the highest authority. An explicit current owner request overrides
  general preferences, examples, templates, ordinary habits, older summaries, and
  the owner's own project instructions, and may change them; state which rule
  changed.
- A general or vague request yields to an explicit, specific project boundary in
  scope. That boundary never overrides an explicit owner request.
- Only when a conflict cannot be resolved from the request and the rules, ask the
  owner instead of guessing.
- A template, example, or ordinary habit is guidance; do not promote it into a hard
  prohibition."""


@dataclass(frozen=True)
class Budget:
    context_limit: int = 64_000
    output_reserve: int = 8_000
    tool_reserve: int = 2_000

    @property
    def input_limit(self) -> int:
        limit = self.context_limit - self.output_reserve - self.tool_reserve
        return max(limit, 0)


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
    permission_context: str = "",
) -> str:
    sections: list[str] = []
    if owner_instructions:
        sections.append(
            "# Owner instructions\n"
            "Source: `owner-instructions.txt` in the Nexus data directory; this file is "
            "owner-controlled and outside the workspace.\n"
            "Nature: durable owner preferences and constraints; the current explicit "
            "owner request may override them.\n\n"
            + owner_instructions
        )
    sections.append(role)
    shell = ""
    if "- bash:" in tools:
        shell = (
            "\n- Shell: none; bash receives one command-line string (not an argv list), "
            "splits it into argv, and does not interpret shell operators. Its working "
            "directory is the workspace root."
        )
    env = ENVIRONMENT.format(
        platform=platform,
        date=date,
        shell=shell,
        workspace=workspace.summary(),
    )
    sections.append(env)
    sections.append(INSTRUCTION_PRIORITY)
    if workspace.instructions:
        sections.append(
            "# Project instructions\n"
            "Source: instruction files on the path from the filesystem root down to "
            "this workspace, plus directories already touched in this run.\n"
            "Nature: workspace-specific requirements and habits within each stated scope.\n"
            "Each block names its absolute file path and scope. A nested block applies "
            "to that directory and its descendants.\n\n"
            + workspace.instructions
        )
    if skills:
        # Only names, descriptions and trigger hints. Bodies are read on demand
        # with read_skill, so an unused skill costs one line, not a page.
        sections.append(
            "# Skills\n"
            "Source: installed skill manifests.\n"
            "Nature: reusable procedures when they apply; they are not project authority.\n"
            "When one applies, call read_skill with its exact name before doing the work.\n"
            + skills
        )
    if interaction_context:
        sections.append(
            "# Continuing interaction\n"
            "Source: current Nexus conversation and work state.\n"
            "Nature: context and pending events, not new permission or authorization.\n\n"
            + interaction_context
        )
    if task_context:
        sections.append(
            "# Persistent task state\n"
            "Source: the task controller's saved state for current work.\n"
            "Nature: current task context and recorded progress, not a new instruction "
            "or authorization. It outranks old model summaries and yields to the latest "
            "explicit owner request and applicable project rules.\n"
            "Check actual results before claiming success. "
            "Progress, plans, and supporting records are optional; update them when useful.\n\n"
            + task_context
        )
    if permission_context:
        sections.append(
            "# Permission context\n"
            "Source: supplied by the main agent for this request.\n"
            "Nature: the permission and approval information actually available in this "
            "run. Do not assume broader access.\n\n"
            + permission_context
        )
    if tools:
        sections.append(
            "# Tools\n"
            "Source: this run's tool registry. Only the tools listed below are available.\n"
            "Call them with their exact names. Arguments must match the schema.\n"
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
