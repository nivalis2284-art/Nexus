"""Tool for reading and editing the owner's saved persona."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolSpec


class PersonaArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal[
        "read", "save_section", "delete_section", "move_section",
        "save_entry", "delete_entry",
    ]
    section: dict[str, Any] | None = Field(
        default=None,
        description=(
            "For save_section, the section to add {title, content}, or an update "
            "with id and only the fields to change."
        ),
    )
    offset: int | None = Field(
        default=None,
        description="For move_section, a non-zero signed position change.",
    )
    entry: dict[str, Any] | None = Field(
        default=None,
        description=(
            "For save_entry, a new entry {name, description, body, enabled}, or an "
            "update with id and only the fields to change. name is an English slug; "
            "description is required; enabled defaults to true."
        ),
    )
    id: str | None = None


def manage(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    store = getattr(context, "persona_store", None)
    if store is None or not getattr(context, "persona_writable", False):
        raise PermissionError("Persona editing is not enabled.")
    document = store.manage(args)
    return {
        "content": json.dumps(document, ensure_ascii=False),
        "document": document,
    }


PERSONA_TOOL = ToolSpec(
    "persona_manage",
    "Read or edit the owner's saved persona document. Only save when the owner "
    "explicitly asks to change the persona; never save a draft, an inferred "
    "preference, or wording the owner has not approved. Actions: read, "
    "save_section (add, or update by id), delete_section, move_section (id and "
    "offset), save_entry (full new entry or partial update by id), delete_entry.",
    PersonaArgs,
    manage,
)

__all__ = ["PERSONA_TOOL", "PersonaArgs", "manage"]
