"""Context compaction: deterministic pruning first, summarisation second.

Two independent mechanisms, applied in this order:

1. :func:`prune` — trims the middle out of oversized tool results. Pure text
   surgery: no model call, no semantic change, nothing invented. This is the
   first line of defence because it cannot introduce a new source of error.
2. :func:`summarise` — replaces old history with a structured checkpoint. Only
   runs when pruning was not enough.

Parameters come from DSH's implementations
(``dsh-compaction-tool-result-pruner`` and ``dsh-compaction-basic``):

* prune: threshold 8192 chars, keep 4096 head + 1024 tail
* summarise: trigger at 80% of the window, keep the newest 16% verbatim

Three rules make compaction safe, all learned from reading those packages:

* **Never shrink without proving it shrank.** A compaction that produces a
  longer context is worse than no compaction.
* **Never cut inside a tool call/result pair.** A dangling ``tool_call`` with no
  result confuses every provider.
* **Never fail the turn because compaction failed.** Fall back to pruning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

# --- pruning constants, from dsh-compaction-tool-result-pruner --------------

PRUNE_THRESHOLD = 8_192
PRUNE_HEAD = 4_096
PRUNE_TAIL = 1_024
PRUNE_MARKER = "\n\n[... tool result middle pruned ...]\n\n"

# --- summarisation constants, from dsh-compaction-basic --------------------

TRIGGER_RATIO = 0.8
RETAIN_RATIO = 0.16
SUMMARY_TAG_OPEN = "<compacted-summary>"
SUMMARY_TAG_CLOSE = "</compacted-summary>"

# The eight sections DSH asks for. Keeping the same shape means a checkpoint
# written by either system is readable by the other.
SUMMARY_TEMPLATE = """## Primary Request and Intent
- [the user's original and evolving goals; quote verbatim where wording matters]

## Key Technical Concepts
- [technologies, frameworks, patterns and conventions in play]

## Files and Code
- [exact path: why it matters, key changes or snippets]

## Errors and Fixes
- [error: how it was resolved, plus any related user feedback]

## Pending Jobs
- [explicitly requested work not yet completed]

## Current Work
- [precisely what was in progress at this checkpoint]

## Next Step
- [the single next action, or "(none)"]

## Critical Context
- [decisions and their rationale, constraints, user preferences, open questions]"""

SUMMARY_INSTRUCTION = (
    "You are acting as a compaction engine. Condense the conversation ABOVE into a "
    "structured checkpoint that lets another model resume the work without losing "
    "essential context.\n\n"
    "Output EXACTLY the Markdown structure below: keep every section, in order. "
    "Use terse bullets, not prose. Write \"(none)\" for an empty section — never "
    "drop a section.\n\n" + SUMMARY_TEMPLATE
)


@dataclass(frozen=True)
class PrunePolicy:
    threshold: int = PRUNE_THRESHOLD
    head: int = PRUNE_HEAD
    tail: int = PRUNE_TAIL

    def __post_init__(self) -> None:
        if self.threshold <= self.head + self.tail:
            raise ValueError("threshold must exceed head + tail")


@dataclass
class CompactionReport:
    """What actually happened, for tests and for the UI."""

    pruned_results: int = 0
    pruned_chars: int = 0
    summarised: bool = False
    epochs: int = 0
    reason: str = ""
    before_tokens: int = 0
    after_tokens: int = 0


def _code_point_trim(text: str, head: int, tail: int) -> str:
    """Slice by code point, never between a surrogate pair's halves."""
    head_text = text[:head]
    # Never leave a lone high surrogate at the boundary.
    if head_text and 0xD800 <= ord(head_text[-1]) <= 0xDBFF:
        head_text = head_text[:-1]
    tail_text = text[-tail:]
    if tail_text and 0xDC00 <= ord(tail_text[0]) <= 0xDFFF:
        tail_text = tail_text[1:]
    return head_text + PRUNE_MARKER + tail_text


def prune(
    messages: list[dict[str, Any]], policy: PrunePolicy | None = None
) -> tuple[list[dict[str, Any]], CompactionReport]:
    """Shorten oversized tool results in place-free fashion.

    Returns a new list; the input is not mutated, so callers can keep the
    original transcript for the session file.
    """
    policy = policy or PrunePolicy()
    report = CompactionReport()
    out: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if (
            message.get("role") == "tool"
            and isinstance(content, str)
            and len(content) > policy.threshold
        ):
            trimmed = _code_point_trim(content, policy.head, policy.tail)
            report.pruned_results += 1
            report.pruned_chars += len(content) - len(trimmed)
            out.append({**message, "content": trimmed})
        else:
            out.append(message)
    return out, report


def _text_of(message: dict[str, Any]) -> str:
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text", "")))
                parts.append(str(block.get("content", "")))
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict):
            function = call.get("function") or {}
            parts.append(str(function.get("name", "")))
            parts.append(str(function.get("arguments", "")))
    return "".join(parts)


def approx_tokens(messages: list[dict[str, Any]]) -> int:
    """Same estimator the prompt builder uses: bytes / 3."""
    total = 0
    for message in messages:
        total += len(_text_of(message).encode("utf-8")) // 3 + 8
    return total


# ---------------------------------------------------------------- pairing


class CompactionBlocked(RuntimeError):
    """Compaction refused: cutting here would corrupt the transcript."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _open_calls(messages: list[dict[str, Any]]) -> set[str]:
    """Ids of tool calls that have no matching result yet."""
    pending: set[str] = set()
    for message in messages:
        for call in message.get("tool_calls") or []:
            if isinstance(call, dict) and call.get("id"):
                pending.add(str(call["id"]))
        if message.get("role") == "tool":
            call_id = message.get("tool_call_id")
            if call_id is not None:
                pending.discard(str(call_id))
    return pending


def assert_paired(messages: list[dict[str, Any]]) -> None:
    """Refuse to compact a transcript that contains an unfinished tool call."""
    pending = _open_calls(messages)
    if pending:
        raise CompactionBlocked(
            f"unfinished tool call(s): {', '.join(sorted(pending))}"
        )


def split_for_summary(
    messages: list[dict[str, Any]], retain_tokens: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (to summarise, verbatim tail), never cutting a pair.

    Walks backwards accumulating the tail until it reaches ``retain_tokens``,
    then extends the tail backwards until it does not start in the middle of a
    tool exchange.
    """
    if not messages:
        return [], []
    tail: list[dict[str, Any]] = []
    used = 0
    for message in reversed(messages):
        cost = len(_text_of(message).encode("utf-8")) // 3 + 8
        if tail and used + cost > retain_tokens:
            break
        tail.append(message)
        used += cost
    tail.reverse()

    # If the tail begins with a tool result, its matching call lives just
    # before it and must be pulled in too.
    while tail and tail[0].get("role") == "tool":
        index = len(messages) - len(tail)
        if index == 0:
            break
        tail.insert(0, messages[index - 1])
    head = messages[: len(messages) - len(tail)]
    # A single enormous message (a pasted log, one giant tool result) can fill
    # the whole retained tail, leaving nothing to summarise. That is exactly the
    # case where compaction is needed, so keep only the newest message verbatim
    # and summarise the rest.
    if not head and len(messages) > 1:
        head = messages[:-1]
        tail = messages[-1:]
    return head, tail


# ---------------------------------------------------------------- summarise


Summarizer = Callable[[str], "str | None"]


def summarise(
    messages: list[dict[str, Any]],
    *,
    window_tokens: int,
    call_model: Summarizer,
    max_epochs: int = 16,
    epochs: int = 0,
) -> tuple[list[dict[str, Any]], CompactionReport]:
    """Replace old history with a checkpoint when pressure is high.

    ``call_model`` takes a prompt and returns the checkpoint text, or ``None``
    on failure. Any failure means "keep the original transcript": a turn that
    runs with a too-long context is better than a turn that cannot run at all.
    """
    report = CompactionReport(epochs=epochs)
    report.before_tokens = approx_tokens(messages)

    if window_tokens <= 0:
        report.reason = "no window"
        return messages, report
    if report.before_tokens < window_tokens * TRIGGER_RATIO:
        report.reason = "below threshold"
        return messages, report
    if epochs >= max_epochs:
        report.reason = "epoch budget exhausted"
        return messages, report
    try:
        assert_paired(messages)
    except CompactionBlocked as blocked:
        report.reason = str(blocked)
        return messages, report

    head, tail = split_for_summary(messages, int(window_tokens * RETAIN_RATIO))
    if not head:
        report.reason = "nothing to compact"
        return messages, report

    transcript = "\n\n".join(
        f"[{message.get('role')}] {_text_of(message)[:4000]}" for message in head
    )
    checkpoint = call_model(transcript + "\n\n" + SUMMARY_INSTRUCTION)
    if not checkpoint or not checkpoint.strip():
        report.reason = "summariser returned nothing"
        return messages, report

    summary_message = {
        "role": "user",
        "content": f"{SUMMARY_TAG_OPEN}\n{checkpoint.strip()}\n{SUMMARY_TAG_CLOSE}",
    }
    candidate = [summary_message, *tail]

    report.after_tokens = approx_tokens(candidate)
    # Never accept a "compaction" that did not actually shrink the context.
    if report.after_tokens >= report.before_tokens:
        report.reason = "no gain"
        return messages, report

    report.summarised = True
    report.epochs = epochs + 1
    report.reason = "ok"
    return candidate, report


def compact(
    messages: list[dict[str, Any]],
    *,
    window_tokens: int,
    call_model: Summarizer | None = None,
    prune_policy: PrunePolicy | None = None,
    epochs: int = 0,
    max_epochs: int = 16,
) -> tuple[list[dict[str, Any]], CompactionReport]:
    """The single entry point: prune, then summarise if still over budget.

    Callers pass the full history and get back what to send. Neither step ever
    raises for expected conditions; ``call_model`` being absent simply means
    "summarisation is disabled".
    """
    pruned, report = prune(messages, prune_policy)
    if call_model is None:
        return pruned, report

    result, summary_report = summarise(
        pruned,
        window_tokens=window_tokens,
        call_model=call_model,
        epochs=epochs,
        max_epochs=max_epochs,
    )
    report.summarised = summary_report.summarised
    report.epochs = summary_report.epochs
    report.reason = summary_report.reason
    report.before_tokens = summary_report.before_tokens
    report.after_tokens = summary_report.after_tokens
    return result, report
