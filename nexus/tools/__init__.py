"""The toolset: declarations in one place, bound to one workspace context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import files, shell
from .registry import Registry, ToolResult, ToolSpec


class SkillArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=64)


class ToolContext:
    """Everything a tool may touch. Constructed per run, never per tool."""

    def __init__(self, root: Path, skills: dict[str, str] | None = None) -> None:
        self.root = Path(root).resolve()
        self.skills = skills or {}


def _skill(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    name = args["name"]
    body = context.skills.get(name)
    if body is None:
        available = ", ".join(sorted(context.skills)) or "(none installed)"
        raise FileNotFoundError(f"Unknown skill '{name}'. Available: {available}")
    return {"content": body, "skill": name}


def build(skills: dict[str, str] | None = None) -> tuple[Registry, ToolContext]:
    registry = Registry()
    registry.add(
        ToolSpec(
            "read_file",
            "Read a UTF-8 file with line numbers. Use offset/limit for large files.",
            files.ReadArgs,
            files.read,
            read_only=True,
        )
    )
    registry.add(
        ToolSpec(
            "write_file",
            "Create or overwrite a file. Prefer edit_file for existing files.",
            files.WriteArgs,
            files.write,
        )
    )
    registry.add(
        ToolSpec(
            "edit_file",
            "Replace exact text in a file. old_text must occur exactly once unless "
            "replace_all is true.",
            files.EditArgs,
            files.edit,
        )
    )
    registry.add(
        ToolSpec(
            "glob",
            "Find files by glob pattern (for example '**/*.py'). Returns workspace-relative paths.",
            files.GlobArgs,
            files.glob_paths,
            read_only=True,
        )
    )
    registry.add(
        ToolSpec(
            "grep",
            "Search file contents with a regular expression. Returns file:line: text.",
            files.GrepArgs,
            files.grep,
            read_only=True,
        )
    )
    registry.add(
        ToolSpec(
            "bash",
            "Run one command (no shell) in the workspace. Returns exit code, stdout and stderr.",
            shell.BashArgs,
            shell.bash,
        )
    )
    registry.add(
        ToolSpec(
            "read_skill",
            "Read an installed skill's instructions by name.",
            SkillArgs,
            _skill,
            read_only=True,
        )
    )
    return registry, ToolContext(Path.cwd(), skills)


__all__ = ["Registry", "ToolContext", "ToolResult", "ToolSpec", "build"]
