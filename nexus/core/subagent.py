"""Subagents: a nested agent run with its own transcript.

Copied from ``dsh-subagent`` where it matters:

* **Depth is bounded.** A subagent may delegate, but the chain has a hard limit,
  recorded on the run so a nested call cannot reset it.
* **Only the final text returns.** The child's transcript stays with the child;
  the parent gets a summary, not a second conversation to carry around.

One deliberate difference: DSH ships both fork (inherit the parent's context) and
spawn (start fresh) as separate packages. Only spawn is implemented here, because
forking is really a memory-semantics question — what a child should remember is a
decision for the long-term direction of the project, not something to lock in now.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable

DEFAULT_MAX_DEPTH = 2


class SubagentError(RuntimeError):
    pass


class DepthExceeded(SubagentError):
    def __init__(self, depth: int, limit: int) -> None:
        self.depth = depth
        self.limit = limit
        super().__init__(
            f"Subagent depth {depth} exceeds the limit of {limit}. "
            "Do the remaining work directly instead of delegating again."
        )


@dataclass
class SubagentRequest:
    task: str
    max_steps: int = 12
    depth: int = 1


@dataclass
class SubagentResult:
    text: str
    steps: int = 0
    stopped: str = ""
    depth: int = 1


def assert_depth(depth: int, limit: int = DEFAULT_MAX_DEPTH) -> None:
    if depth < 1:
        raise SubagentError("depth must be at least 1")
    if depth > limit:
        raise DepthExceeded(depth, limit)


def _subagent_prompt(task: str) -> str:
    return (
        "You are a subagent working on one scoped task for a parent agent.\n\n"
        "Rules:\n"
        "- Do the work with tools rather than describing what you would do.\n"
        "- Stay inside the task. Do not start unrelated work.\n"
        "- Finish with a short report: what you found or changed, and anything the "
        "parent needs to know. The parent only sees this final message, not your "
        "steps.\n\n"
        f"Task:\n{task}"
    )


Runner = Callable[[str, int], AsyncIterator[Any]]


async def run_subagent(
    request: SubagentRequest,
    *,
    make_runner: Runner,
    limit: int = DEFAULT_MAX_DEPTH,
) -> SubagentResult:
    """Drive one nested run and reduce it to a result.

    ``make_runner`` returns an async iterator of agent events for the given
    prompt and step budget. Passing a factory rather than an agent keeps this
    module free of construction details it does not need to know.
    """
    assert_depth(request.depth, limit)
    text_parts: list[str] = []
    steps = 0
    stopped = "complete"
    async for event in make_runner(_subagent_prompt(request.task), request.max_steps):
        kind = getattr(event, "type", None)
        if kind == "delta" and getattr(event, "text", ""):
            text_parts.append(event.text)
        elif kind == "done":
            data = getattr(event, "data", None) or {}
            steps = int(data.get("steps") or 0)
            stopped = str(data.get("stopped") or "complete")
        elif kind == "error":
            stopped = "error"
    return SubagentResult(
        text="".join(text_parts).strip() or "(subagent returned nothing)",
        steps=steps,
        stopped=stopped,
        depth=request.depth,
    )
