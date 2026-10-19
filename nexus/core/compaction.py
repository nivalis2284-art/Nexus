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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# The prompt builder and compaction share these estimates so that budgeting,
# split points and reports cannot disagree about the same message.
IMAGE_TOKEN_ALLOWANCE = 1_600
MESSAGE_OVERHEAD = 8

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


def estimate(text: str) -> int:
    """Conservative token estimate: UTF-8 bytes / 3 (CJK is about 3 bytes/token)."""
    return len(text.encode("utf-8")) // 3 + 1


def _value(value: Any) -> str:
    return "" if value is None else str(value)


def _call_field(call: dict[str, Any], name: str) -> Any:
    """Read a field from the internal flat shape or the legacy nested shape."""
    value = call.get(name)
    if value is not None:
        return value
    function = call.get("function")
    if isinstance(function, dict):
        return function.get(name)
    return None


def _text_of(message: dict[str, Any]) -> str:
    parts: list[str] = []
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                # Image payloads are base64, not text. Their fixed allowance is
                # applied by image_tokens instead.
                if block.get("type") == "image":
                    continue
                parts.append(str(block.get("text", "")))
                parts.append(str(block.get("content", "")))
            elif isinstance(block, str):
                parts.append(block)
    for key in ("arguments", "tool_call_id", "name"):
        value = message.get(key)
        if value is not None:
            parts.append(_value(value))
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        # Current sessions store tool calls flat ({id, name, arguments}).
        # Older sessions use OpenAI's nested function object. Count both.
        parts.append(_value(call.get("id")))
        parts.append(_value(_call_field(call, "name")))
        parts.append(_value(_call_field(call, "arguments")))
    return "".join(parts)


def image_tokens(message: dict[str, Any]) -> int:
    content = message.get("content")
    if not isinstance(content, list):
        return 0
    count = sum(
        1
        for block in content
        if isinstance(block, dict) and block.get("type") == "image"
    )
    return count * IMAGE_TOKEN_ALLOWANCE


def message_tokens(message: dict[str, Any]) -> int:
    """Estimated cost of one message, including its fixed framing overhead."""
    return estimate(_text_of(message)) + MESSAGE_OVERHEAD + image_tokens(message)


def approx_tokens(messages: list[dict[str, Any]]) -> int:
    """Same estimator the prompt builder uses: bytes / 3."""
    return sum(message_tokens(message) for message in messages)


def paired(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop only tool messages that cannot be paired from model input.

    Complete exchanges are preserved, including several results for one
    assistant call. A complete call keeps its matching results and drops any
    extra result in the same group. An unfinished call is dropped as a whole;
    a standalone result is dropped by itself. Session history is untouched.
    """
    out: list[dict[str, Any]] = []
    for start, end, _ in message_units(messages):
        ids = _call_ids(messages[start])
        if not ids:
            if messages[start].get("role") != "tool":
                out.extend(messages[start:end])
            continue
        results: dict[str, dict[str, Any]] = {}
        for index in range(start + 1, end):
            call_id = messages[index].get("tool_call_id")
            if call_id is not None:
                results.setdefault(str(call_id), messages[index])
        if not all(call_id in results for call_id in ids):
            continue
        out.append(messages[start])
        out.extend(results[call_id] for call_id in ids)
    return out


def _call_ids(message: dict[str, Any]) -> list[str]:
    if message.get("role") != "assistant":
        return []
    ids: list[str] = []
    for call in message.get("tool_calls") or []:
        if isinstance(call, dict) and call.get("id") is not None:
            ids.append(str(call["id"]))
    return ids


def message_units(
    messages: list[dict[str, Any]],
) -> list[tuple[int, int, bool]]:
    """Return atomic ranges as (start, end, valid).

    An assistant message with tool calls and all of its consecutive results is
    one unit. Splitting a request or a compacted tail at any other boundary can
    leave a tool result without its call. ``valid`` is false for unfinished tool
    calls and orphan tool results; callers that build model input omit them.
    """
    units: list[tuple[int, int, bool]] = []
    index = 0
    while index < len(messages):
        ids = _call_ids(messages[index])
        if ids:
            pending = set(ids)
            matched = True
            end = index + 1
            while end < len(messages) and messages[end].get("role") == "tool":
                call_id = messages[end].get("tool_call_id")
                normalized = None if call_id is None else str(call_id)
                if normalized in pending:
                    pending.remove(normalized)
                else:
                    # A result that does not belong to this call makes the
                    # boundary unsafe to treat as a complete interaction.
                    matched = False
                end += 1
            units.append((index, end, matched and not pending))
            index = end
        elif messages[index].get("role") == "tool":
            # Consecutive orphan results are one unit. Keeping the newest one
            # alone as a fallback could still send a result without its call.
            end = index + 1
            while end < len(messages) and messages[end].get("role") == "tool":
                end += 1
            units.append((index, end, False))
            index = end
        else:
            units.append((index, index + 1, True))
            index += 1
    return units


# ---------------------------------------------------------------- pairing


class CompactionBlocked(RuntimeError):
    """Compaction refused: cutting here would corrupt the transcript."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def assert_paired(messages: list[dict[str, Any]]) -> None:
    """Refuse to compact a transcript containing an invalid tool exchange."""
    if any(not valid for _, _, valid in message_units(messages)):
        raise CompactionBlocked("unpaired tool interaction(s)")


def split_for_summary(
    messages: list[dict[str, Any]], retain_tokens: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (to summarise, verbatim tail), never cutting a pair.

    Walks backwards by complete message units. A unit is an ordinary message or
    one assistant tool-call message together with every consecutive result.
    """
    if not messages:
        return [], []
    units = message_units(messages)
    if any(not valid for _, _, valid in units):
        # There is no safe cut in a transcript with a half-finished exchange.
        return [], list(messages)
    tail_start = len(messages)
    used = 0
    for start, end, _ in reversed(units):
        cost = approx_tokens(messages[start:end])
        if tail_start < len(messages) and used + cost > retain_tokens:
            break
        tail_start = start
        used += cost

    head = messages[:tail_start]
    tail = messages[tail_start:]
    # A single enormous message (a pasted log, one giant tool result) can fill
    # the whole retained tail, leaving nothing to summarise. That is exactly the
    # case where compaction is needed, so keep only the newest message verbatim
    # and summarise the rest.
    if not head and len(messages) > 1:
        last_start, _, valid = units[-1]
        # A giant last interaction can only be split after a complete assistant
        # message. A pure tool tail cannot be cut away from its call.
        if valid and messages[last_start].get("role") != "tool":
            head = messages[:last_start]
            tail = messages[last_start:]
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
