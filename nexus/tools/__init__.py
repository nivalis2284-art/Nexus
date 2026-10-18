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

from . import ask, files, shell, todo, web
from .registry import Registry, ToolResult, ToolSpec

# Order matters only for prompt readability: cheap, common tools first.
CATALOG = (files, shell, todo, ask, web, None)  # None is replaced by _SKILL below


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
) -> tuple[Registry, ToolContext]:
    """Assemble a registry. ``config`` decides which optional tools exist."""
    context = ToolContext(root or Path.cwd(), skills)
    settings = config or {}
    allowlist = settings.get("webfetch_hosts") or []
    if isinstance(allowlist, str):
        allowlist = [item.strip() for item in allowlist.split(",") if item.strip()]
    context.web_allowlist = [str(item) for item in allowlist]
    context.search_endpoint = str(settings.get("search_endpoint") or "")

    available = {
        "webfetch_hosts": context.web_allowlist,
        "search_endpoint": context.search_endpoint,
    }

    registry = Registry()
    for spec in tool_specs():
        if spec.available(available):
            registry.add(spec)
    return registry, context


__all__ = [
    "Registry",
    "ToolContext",
    "ToolResult",
    "ToolSpec",
    "build",
    "tool_specs",
]
