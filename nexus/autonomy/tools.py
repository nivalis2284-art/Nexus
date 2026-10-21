"""Owner-directed controls for the normal Ling conversation, not deliberation."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ..tools.registry import ToolSpec


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Control(Empty):
    operation: Literal["pause", "resume", "terminate"]


class Withdraw(Empty):
    goal_id: str


def controls(runtime):
    def state(context, args):
        return {"content": json.dumps(runtime.snapshot(), ensure_ascii=False)}

    def command(context, args):
        value = runtime.command(args["operation"], runtime.store.control()["version"])
        return {"content": json.dumps(value, ensure_ascii=False)}

    def withdraw(context, args):
        goal = runtime.store.get("goal", args["goal_id"])
        if not goal:
            raise ValueError("关注事项不存在")
        return {"content": json.dumps(runtime.withdraw(goal["id"], goal["version"]), ensure_ascii=False)}

    return [ToolSpec("autonomy_state", "Read Ling's actual autonomous concerns, decisions, watches, plans and owner controls.", Empty, state, read_only=True),
            ToolSpec("autonomy_control", "Only when the owner asks: pause, resume a paused autonomous part, or terminate it. Does not change unrelated user work or authorize new scope.", Control, command),
            ToolSpec("autonomy_withdraw", "Only when the owner withdraws a specific concern: persist its withdrawal and stop associated autonomous work/watches/plans.", Withdraw, withdraw)]
