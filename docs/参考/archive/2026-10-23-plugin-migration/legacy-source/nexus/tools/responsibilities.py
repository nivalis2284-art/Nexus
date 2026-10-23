"""Ling tools for saving and controlling durable responsibilities."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .registry import ToolSpec


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListArgs(StrictArgs):
    pass


class SaveArgs(StrictArgs):
    id: str | None = None
    title: str | None = None
    instruction: str | None = None
    summary: str | None = None


class ControlArgs(StrictArgs):
    id: str
    operation: Literal["pause", "resume", "end"]


def _service(context: Any):
    service = getattr(getattr(context, "work_service", None), "responsibilities", None)
    if service is None:
        raise PermissionError("Responsibility tools are available only in Ling.")
    return service


def _result(value: dict[str, Any]) -> dict[str, Any]:
    return {"content": json.dumps(value, ensure_ascii=False), **value}


def list_responsibilities(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    return _result(_service(context).list())


def save_responsibility(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    value = _service(context).save(
        id=args.get("id"), title=args.get("title"),
        instruction=args.get("instruction"), summary=args.get("summary"),
        origin="ling",
    )
    return _result(value)


def control_responsibility(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    value = _service(context).control(args["id"], args["operation"])
    return _result(value)


TOOLS = [
    ToolSpec(
        "responsibility_list",
        "List responsibilities and linked work. Paused and ended responsibilities are not running.",
        ListArgs, list_responsibilities, read_only=True,
    ),
    ToolSpec(
        "responsibility_save",
        "Create or update a responsibility Ling carries, including responsibilities Ling chooses for herself. New responsibilities are saved as active with title and instruction required; origin is recorded as Ling.",
        SaveArgs, save_responsibility,
    ),
    ToolSpec(
        "responsibility_control",
        "Pause, resume, or end a responsibility. Pause suspends its current running or queued work; resume does not start work; end leaves existing work unchanged.",
        ControlArgs, control_responsibility,
    ),
]
