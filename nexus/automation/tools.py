"""Only execution results belong to Nexus; rule tools are provided over MCP."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from ..tools.registry import ToolSpec


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class FinishArgs(Empty):
    reply: str = ""
    summary: str = ""

def tools(runtime):
    def state(context, args):
        value = runtime.snapshot()
        return {"content": json.dumps(value, ensure_ascii=False), **value}

    def finish(context, args):
        return runtime.finish(context, args)

    return [
        ToolSpec("automation_list", "Read wake activity history and the scheduling service state. "
                 "Manage rules with the scheduling MCP tools.", Empty, state, read_only=True),
        ToolSpec("automation_finish", "End this wake: reply for a useful owner message; "
                 "otherwise summarize the experience in one sentence. Explicit reminders require reply.",
                 FinishArgs, finish),
    ]
