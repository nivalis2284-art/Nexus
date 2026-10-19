"""The ``subagent`` tool: hand a scoped task to a nested agent run.

The handler talks to the parent through ``context.spawn_subagent``, a callable the
assembly code injects. That keeps this module free of any import of the agent
loop, which is the same pattern ``ask_user`` already uses for its human channel.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolSpec

DESCRIPTION = (
    "Delegate one scoped task to a fresh subagent and get back its final report. "
    "Use it for work that is self-contained and would otherwise fill your context "
    "with detail you do not need afterwards (a broad search, an isolated "
    "investigation, a long mechanical change). Do not use it for a single tool "
    "call you could make yourself, and do not delegate the task you are in the "
    "middle of reasoning about."
)


class SubagentArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    task: str = Field(
        min_length=1,
        max_length=8000,
        description="A complete, self-contained instruction. The child cannot see this conversation.",
    )
    max_steps: int = Field(default=12, ge=1, le=40)


def spawn(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    factory = getattr(context, "spawn_subagent", None)
    if factory is None:
        raise PermissionError("Subagents are not available in this context.")
    result = factory(args["task"], args["max_steps"])
    if isinstance(result, str):
        return {"content": result, "subagent": True}
    text = getattr(result, "text", "") or "(subagent returned nothing)"
    return {
        "content": text,
        "ok": getattr(result, "stopped", "complete") not in ("error", "cancelled"),
        "subagent": True,
        "stopped": getattr(result, "stopped", "complete"),
        "steps": getattr(result, "steps", 0),
        "depth": getattr(result, "depth", 1),
    }


TOOLS = [
    ToolSpec(
        "subagent",
        DESCRIPTION,
        SubagentArgs,
        spawn,
    ),
]
