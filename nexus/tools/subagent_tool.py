"""Asynchronous subagent operations bound to the calling session or work."""
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .registry import ToolSpec

class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class SubagentArgs(Args):
    task: str = Field(min_length=1, max_length=20000, description="Complete scope, relevant facts, constraints and expected result.")
    context_mode: Literal["fresh", "fork"] = "fresh"
    provider_id: str = ""
    model_id: str = ""
    effort: Literal["low", "medium", "high", "xhigh", "max", "default"] | None = None
    read_only: bool = True

class ReadArgs(Args):
    child_id: str = Field(min_length=1)
    section: Literal["summary", "messages", "evidence", "decisions", "operations"] = "summary"
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)
    text_offset: int = Field(default=0, ge=0)

class MessageArgs(Args):
    child_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=20000)

class ControlArgs(Args):
    child_id: str = Field(min_length=1)
    operation: Literal["pause", "resume", "cancel"]

class WaitArgs(Args):
    child_id: str = ""

class ModelArgs(Args):
    child_id: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None

class EmptyArgs(Args):
    pass


def call(operation):
    def run(context, arguments):
        service = getattr(context, "subagents", None)
        if service is None:
            raise PermissionError("Subagents are not available in this execution context.")
        value = getattr(service, operation)(**arguments)
        return {"content": json.dumps(value, ensure_ascii=False), **value}
    return run

spawn = call("start")
TOOLS = [
    ToolSpec("subagent", "Delegate an independent scope and immediately receive its task_id. Continue useful parent work while it runs. Fresh starts with the supplied scope; fork snapshots current context. Owner model choices win. No tool approval is inherited. Do not delegate the parent decision you are currently reasoning about.", SubagentArgs, spawn),
    ToolSpec("subagent_list", "List your own children's actual states.", EmptyArgs, call("list"), read_only=True),
    ToolSpec("subagent_read", "Read a child's result or paginated records using child_id=its task_id.", ReadArgs, call("read"), read_only=True),
    ToolSpec("subagent_message", "Send ordered amendments to your child at its next model boundary; completed children start another iteration, paused children stay paused.", MessageArgs, call("message")),
    ToolSpec("subagent_control", "Pause, resume or cancel only your own child. Never answer its owner approval yourself.", ControlArgs, call("control")),
    ToolSpec("subagent_model", "Change only your child's model and reasoning effort at its next request. Honor explicit owner choices and use work_models to inspect available models.", ModelArgs, call("model")),
    ToolSpec("subagent_wait", "Yield only when no independent parent work remains. Releases the parent's execution slot until all children finish; results return automatically. This does not approve pending actions.", WaitArgs, call("wait")),
]
