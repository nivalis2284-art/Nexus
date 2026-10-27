"""The toolset: one declarative list, one context, one dispatcher.

Assembly is list-driven on purpose. Adding a tool means writing it in a module
that exposes ``TOOLS`` and appending that module to ``CATALOG`` below; nothing
here has to change. That is the seam a future plugin loader would take over:
replace ``CATALOG`` with "discover modules that export TOOLS".

A tool whose declared requirements are unmet (for example ``websearch`` with no
endpoint configured) is **not registered**. It never appears in the schema, so
the model cannot call a tool that could only fail.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import ask, attachments, files, jobs, patch, shell, subagent_tool, todo, web
from .registry import Registry, ToolResult, ToolSpec

# Order matters only for prompt readability: cheap, common tools first.
CATALOG = (files, attachments, shell, jobs, patch, todo, ask, subagent_tool, web, None)


class SkillArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)


class ToolContext:
    """Everything a tool may touch. Constructed per run, never per tool."""

    def __init__(self, root: Path, skills: dict[str, str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.skills = skills or {}
        # Filled by the caller when the corresponding feature is configured.
        self.todos: list[dict[str, str]] = []
        self.web_allowlist: list[str] = []
        self.search_endpoint: str = ""
        self.ask: Any = None
        # Live MCP connection registry, injected by the caller when configured.
        self.mcp: Any = None
        self.task_callbacks: Any = None


def _skill(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = args["name"]
    body = context.skills.get(name)
    if body is None:
        available = ", ".join(sorted(context.skills)) or "(none installed)"
        raise FileNotFoundError(f"Unknown skill '{name}'. Available: {available}")
    return {"content": body, "skill": name}


SKILL_TOOL = ToolSpec(
    "read_skill",
    "Read an installed skill's instructions by name.",
    SkillArgs,
    _skill,
    read_only=True,
)


def tool_specs() -> list[ToolSpec]:
    """Every declared tool, before availability filtering."""
    specs: list[ToolSpec] = []
    for module in CATALOG:
        if module is None:
            specs.append(SKILL_TOOL)
        else:
            specs.extend(module.TOOLS)
    return specs


def build(
    skills: dict[str, str] | None = None,
    *,
    root: Path | None = None,
    config: dict[str, Any] | None = None,
    mcp: Any = None,
    jobs: Any = None,
    cancel_event: Any = None,
    task_callbacks: Any = None,
    attachment_source: Any = None,
    attachment_ids: set[str] | None = None,
    work_service: Any = None,
    owner_home: Path | None = None,
    owner_writable: bool = False,
) -> tuple[Registry, ToolContext]:
    """Assemble a registry. ``config`` decides which optional tools exist."""
    context = ToolContext(root or Path.cwd(), skills)
    context.mcp = mcp
    context.jobs = jobs
    context.cancel_event = cancel_event
    context.task_callbacks = task_callbacks
    context.work_service = work_service
    context.attachments = attachment_source
    context.attachment_ids = attachment_ids or set()
    settings = config or {}
    allowlist = settings.get("webfetch_hosts") or []
    if isinstance(allowlist, str):
        allowlist = [item.strip() for item in allowlist.split(",") if item.strip()]
    context.web_allowlist = [str(item) for item in allowlist]
    context.search_endpoint = str(settings.get("search_endpoint") or "")

    available = {
        "attachments": attachment_source,
        "webfetch_hosts": context.web_allowlist,
        "search_endpoint": context.search_endpoint,
    }

    registry = Registry()
    for spec in tool_specs():
        if spec.available(available):
            registry.add(spec)
    for spec in mcp_tool_specs(context):
        registry.add(spec)
    if task_callbacks is not None:
        from .tasks import TOOLS as task_tools
        from .work import TASK_READ_TOOL

        for spec in task_tools:
            registry.add(spec)
        registry.add(TASK_READ_TOOL)
    if work_service is not None:
        from .work import WORK_TOOLS
        for spec in WORK_TOOLS:
            registry.add(spec)
    if owner_home is not None:
        from .instructions import tool_specs as instruction_tools
        for spec in instruction_tools(owner_home, writable=owner_writable):
            registry.add(spec)
    return registry, context


def mcp_tool_specs(context: "ToolContext") -> list[ToolSpec]:
    """Adapt live MCP declarations into ordinary tool specs.

    MCP tools join the same registry as builtins, so the agent loop needs no
    knowledge of MCP at all. Their schemas are whatever the server advertised,
    so the argument model is a permissive passthrough and validation is left to
    the server, which is the only party that knows the real contract.
    """
    from .mcp_tools import specs_for

    return specs_for(context)


__all__ = [
    "Registry",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "build",
    "mcp_tool_specs",
    "tool_specs",
]
