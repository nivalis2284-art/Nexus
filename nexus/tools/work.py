"""Ling's work supervision tools and paginated task evidence reads."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolSpec


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ListArgs(StrictArgs):
    pass


class ReadArgs(StrictArgs):
    task_id: str = Field(min_length=1)
    section: Literal["summary", "messages", "evidence", "decisions", "operations"] = "summary"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)
    text_offset: int = Field(default=0, ge=0, description="Character offset within a long record; use its text_offsets.next_offset with limit=1.")


class StartArgs(StrictArgs):
    goal: str = Field(min_length=1, max_length=20000)
    title: str = Field(default="", max_length=160)
    constraints: str = Field(default="", max_length=20000)
    attachments: list[str] | None = Field(default=None, max_length=8)


class MessageArgs(StrictArgs):
    task_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=20000)
    attachments: list[str] | None = Field(default=None, max_length=8)


class ControlArgs(StrictArgs):
    task_id: str = Field(min_length=1)
    operation: Literal["pause", "resume", "cancel"]


def _call(operation):
    def handler(context, args):
        value = getattr(context.work_service, operation)(**args)
        return {"content": json.dumps(value, ensure_ascii=False), **value}
    return handler


WORK_TOOLS = [
    ToolSpec("work_list", "List actual independent work states; never starts or resumes work.", ListArgs, _call("list"), read_only=True),
    ToolSpec("work_read", "Read a work summary or page of messages, decisions, evidence or unknown operations. Use task ids from work_list.", ReadArgs, _call("read"), read_only=True),
    ToolSpec("work_start", "Start user-authorized complex work independently and return immediately with its task id. Continue conversation; task tools retain normal permissions. Latest sent attachments are forwarded unless specified.", StartArgs, _call("start")),
    ToolSpec("work_message", "Save the user's amendments for an existing task, applied at its next model boundary. Does not resume paused work or authorize pending tools. Use work_control separately when asked to resume.", MessageArgs, _call("message")),
    ToolSpec("work_control", "Request pause, resume or cancellation of one explicitly identified task. A pending decision must be answered by the user before resuming.", ControlArgs, _call("control")),
]


class TaskReadArgs(StrictArgs):
    section: Literal["summary", "messages", "evidence", "decisions", "operations"] = "summary"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)
    text_offset: int = Field(default=0, ge=0)


def task_read(context, args):
    from ..ling import WorkService

    callbacks = context.task_callbacks
    reader = object.__new__(WorkService)
    reader.store = callbacks.store
    class InactiveController:
        active_task = None
        @staticmethod
        def queued_ids():
            return []
    reader.controller = InactiveController()
    value = reader.read(task_id=callbacks.task_id, **args)
    return {"content": json.dumps(value, ensure_ascii=False), **value}


TASK_READ_TOOL = ToolSpec("task_read", "Read this task's saved state, older evidence, decisions, messages or unknown operations in pages. Saved observations are not proof of unverified claims.", TaskReadArgs, task_read, read_only=True)
