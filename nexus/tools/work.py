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
    context: str = Field(default="", max_length=40000, description="Relevant conversation facts the work needs; its history is independent.")
    attachments: list[str] | None = Field(default=None, max_length=8)
    read_only: bool = Field(default=False, description="True for independent observation/research; side effects acquire project write ownership before executing.")
    provider_id: str = ""
    model_id: str = ""
    effort: Literal["low", "medium", "high", "xhigh", "max", "default"] | None = None


class MessageArgs(StrictArgs):
    task_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=20000)
    attachments: list[str] | None = Field(default=None, max_length=8)


class ControlArgs(StrictArgs):
    task_id: str = Field(min_length=1)
    operation: Literal["pause", "resume", "cancel"]


class ModelArgs(StrictArgs):
    task_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


def _call(operation):
    def handler(context, args):
        value = getattr(context.work_service, operation)(**args)
        return {"content": json.dumps(value, ensure_ascii=False), **value}
    return handler


WORK_TOOLS = [
    ToolSpec("work_models", "List all configured available work models without credentials.", ListArgs, _call("models"), read_only=True),
    ToolSpec("work_model", "Change a work model and reasoning effort at its next model request; preserve history. Honor the owner's explicit choice.", ModelArgs, _call("model")),
    ToolSpec("work_pause_all", "Pause all work ONLY when the owner explicitly requests it. A bare stop affects the main conversation only.", ListArgs, _call("pause_all")),
    ToolSpec("work_list", "List actual independent work states; never starts or resumes work.", ListArgs, _call("list"), read_only=True),
    ToolSpec("work_read", "Read a work summary or page of messages, decisions, evidence or unknown operations. Use task ids from work_list.", ReadArgs, _call("read"), read_only=True),
    ToolSpec("work_start", "Start complex work independently and return immediately; normal tool permissions apply. Latest sent attachments are forwarded unless specified.", StartArgs, _call("start")),
    ToolSpec("work_message", "Save the user's amendments for an existing task, applied at its next model boundary. Does not resume paused work or authorize pending tools. Use work_control separately when asked to resume.", MessageArgs, _call("message")),
    ToolSpec("work_control", "Request pause, resume or cancellation of one explicitly identified task. A pending decision must be answered by the user before resuming.", ControlArgs, _call("control")),
]


class TaskReadArgs(StrictArgs):
    section: Literal["summary", "messages", "evidence", "decisions", "operations"] = "summary"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)
    text_offset: int = Field(default=0, ge=0)


def task_read(context, args):
    from ..ling import WorkReader

    callbacks = context.task_callbacks
    reader = WorkReader(callbacks.store)
    value = reader.read(task_id=callbacks.task_id, **args)
    return {"content": json.dumps(value, ensure_ascii=False), **value}


TASK_READ_TOOL = ToolSpec("task_read", "Read this task's saved state, older evidence, decisions, messages or unknown operations in pages. Saved observations are not proof of unverified claims.", TaskReadArgs, task_read, read_only=True)


def own_model(context, args):
    value = context.work_model(**args)
    value["task_id"] = value["id"]
    return {"content": json.dumps(value, ensure_ascii=False), **value}


class OwnModelArgs(StrictArgs):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


WORK_MODEL_TOOL = ToolSpec("work_model", "Change only this work's model and reasoning effort for its next request; honor owner choices.", OwnModelArgs, own_model)
WORK_MODELS_TOOL = ToolSpec("work_models", "List configured models available for this work.", ListArgs,
    lambda context, args: {"content": json.dumps(context.work_models(), ensure_ascii=False)}, read_only=True)
