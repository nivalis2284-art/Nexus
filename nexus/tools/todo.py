"""The task list tool.

Shape copied deliberately from DSH's ``dsh-tool-todo``: an item is exactly
``{content, status}`` with status in ``pending | in_progress | completed``, and
each call replaces the whole list (last write wins). No ids, no priorities, no
timestamps — those were considered and rejected because the model has no use for
them and they only create ways to get out of sync.

State lives on the tool context for the current session, so a later turn sees the
list it wrote earlier without a new storage layer.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .registry import ToolSpec

STATUSES = ("pending", "in_progress", "completed")

DESCRIPTION = (
    "Record and update a task list to plan multi-step work and show progress; "
    "skip it for trivial single-step tasks. Add one todo per concrete step before "
    "you start. While work remains keep the step you are on `in_progress`; mark "
    "each todo `completed` as soon as it is done. Each call replaces the whole "
    "list, so always send every item."
)


class TodoItem(BaseModel):
    content: str = Field(min_length=1, description="One concrete step.")
    status: Literal["pending", "in_progress", "completed"] = "pending"


class TodoArgs(BaseModel):
    todos: list[TodoItem] = Field(
        min_length=1, description="The complete list, not a delta."
    )


def write(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    items = args["todos"]
    seen: set[str] = set()
    cleaned: list[dict[str, str]] = []
    for item in items:
        content = item["content"].strip()
        if not content:
            raise ValueError("A todo cannot be empty.")
        key = content.lower()
        if key in seen:
            raise ValueError(f"Duplicate todo: {content}")
        seen.add(key)
        cleaned.append({"content": content, "status": item["status"]})

    context.todos = cleaned

    done = sum(1 for item in cleaned if item["status"] == "completed")
    active = [item["content"] for item in cleaned if item["status"] == "in_progress"]
    lines = [f"[{_box(item['status'])}] {item['content']}" for item in cleaned]
    summary = f"{done}/{len(cleaned)} done"
    if active:
        summary += f" · in progress: {active[0]}"
    return {"content": "\n".join(lines) + f"\n\n({summary})", "todos": cleaned}


def _box(status: str) -> str:
    return {"pending": " ", "in_progress": "~", "completed": "x"}.get(status, " ")


TOOLS = [
    ToolSpec(
        "todo_write",
        DESCRIPTION,
        TodoArgs,
        write,
        read_only=True,
    ),
]
