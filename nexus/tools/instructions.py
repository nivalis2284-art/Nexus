"""Model tools for reading and explicitly updating the owner's instructions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..instructions import load_instructions, save_instructions
from .registry import ToolSpec


class ReadInstructionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SaveInstructionsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(description="The exact complete replacement text the owner asked to save.")


def tool_specs(source, *, writable: bool = False) -> list[ToolSpec]:
    """Bind instruction tools to a home directory or a store exposing ``home``.

    ``writable`` adds the replacing tool. Work that must not edit the owner's
    instructions uses the default read-only list.
    """
    home = Path(source) if isinstance(source, (str, Path)) else Path(source.home)

    def read(context: Any, args: dict[str, Any]) -> dict[str, Any]:
        text = load_instructions(home)
        return {"content": text, "text": text}

    def save(context: Any, args: dict[str, Any]) -> dict[str, Any]:
        text = save_instructions(home, args["text"])
        return {"content": text, "text": text}

    specs = [
        ToolSpec(
            "read_instructions",
            "Read the owner's persistent instructions: their basic principles and "
            "preferences. These are active in your system context; read again only "
            "when you need the exact saved text.",
            ReadInstructionsArgs,
            read,
            read_only=True,
        )
    ]
    if writable:
        specs.append(
            ToolSpec(
                "save_instructions",
                "Replace the owner's persistent instructions with the exact text the "
                "owner explicitly asks to save. Only call after an explicit owner "
                "request to edit their instructions; never infer or draft a preference. "
                "The change applies from the next model request.",
                SaveInstructionsArgs,
                save,
            )
        )
    return specs
