"""Ask the owner question(s) mid-turn.

This is not a second approval mechanism; it reuses the same channel. The agent
loop already knows how to pause and wait for a human answer (that is how write
approval works), so asking a question is the same primitive with a different
payload: prompts and optional choices.

Multiple questions are asked **together** and answered in one submit, which is
what opencode's ``question`` tool does. Asking one at a time turns a decision
into a queue of dialogs and loses the sense that these choices belong together.

When no human channel is attached the tool fails loudly rather than guessing,
because a fabricated answer is worse than a visible stop.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolSpec

MAX_QUESTIONS = 8


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    question: str = Field(min_length=1, description="What you need to know.")
    options: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Optional short choices. Leave empty for a free-form answer.",
    )


class AskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    questions: list[Question] = Field(
        min_length=1,
        max_length=MAX_QUESTIONS,
        description="One or more questions, all shown and answered together.",
    )


def ask(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    asker = getattr(context, "ask", None)
    if asker is None:
        raise PermissionError(
            "Nobody is available to answer questions in this context."
        )

    questions = [
        {"question": item["question"], "options": list(item.get("options") or [])}
        for item in args["questions"]
    ]
    answers = asker(questions)
    if answers is None:
        raise PermissionError("The questions were dismissed without an answer.")

    # Backwards compatibility: an older channel returns a single string.
    if isinstance(answers, str):
        answers = [answers]
    if len(answers) != len(questions):
        raise PermissionError(
            f"Expected {len(questions)} answer(s), received {len(answers)}."
        )

    lines = []
    for question, answer in zip(questions, answers):
        lines.append(f"Q: {question['question']}\nA: {answer}")
    return {
        "content": "\n\n".join(lines),
        "answers": answers,
        "count": len(answers),
    }


TOOLS = [
    ToolSpec(
        "ask_user",
        "Ask the owner one or more questions when a decision is genuinely theirs "
        "to make (an ambiguous requirement, a destructive choice, a missing detail "
        "you cannot discover yourself). All questions are shown at once and "
        "answered in one go, so group related questions into a single call. Do not "
        "use it to confirm something you can verify with a tool.",
        AskArgs,
        ask,
        read_only=True,
    ),
]
