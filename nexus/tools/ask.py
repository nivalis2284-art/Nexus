"""Ask the owner a question mid-turn.

This is not a second approval mechanism; it reuses the same channel. The agent
loop already knows how to pause and wait for a human answer (that is how write
approval works), so asking a question is the same primitive with a different
payload: a prompt and optional choices.

When no human channel is attached the tool fails loudly rather than guessing,
because a fabricated answer is worse than a visible stop.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .registry import ToolSpec


class QuestionArgs(BaseModel):
    question: str = Field(min_length=1, description="What you need to know.")
    options: list[str] = Field(
        default_factory=list,
        description="Optional short choices. Leave empty for a free-form answer.",
    )


def ask(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    asker = getattr(context, "ask", None)
    if asker is None:
        raise PermissionError(
            "Nobody is available to answer questions in this context."
        )
    answer = asker(args["question"], list(args.get("options") or []))
    if answer is None:
        raise PermissionError("The question was dismissed without an answer.")
    return {"content": f"Answer: {answer}", "answer": answer}


TOOLS = [
    ToolSpec(
        "ask_user",
        "Ask the owner a question when a decision is genuinely theirs to make "
        "(an ambiguous requirement, a destructive choice, a missing detail you "
        "cannot discover yourself). Do not use it to confirm something you can "
        "verify with a tool.",
        QuestionArgs,
        ask,
        read_only=True,
    ),
]
