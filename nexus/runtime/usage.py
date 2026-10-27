"""Normalize provider usage records for the web summary.

The stored counters stay native: Anthropic reports cache reads and writes
outside ``input_tokens``, while OpenAI and DeepSeek include cache hits inside
their prompt count. The summary exposes one comparable breakdown.
"""

from __future__ import annotations

from typing import Any

ANTHROPIC = "anthropic_messages"


def _value(usage: Any, key: str) -> Any:
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage_of(message: Any) -> Any:
    if isinstance(message, dict):
        usage = message.get("usage")
        if usage is None and isinstance(message.get("data"), dict):
            usage = message["data"].get("usage")
        return usage
    data = getattr(message, "data", None)
    return data.get("usage") if isinstance(data, dict) else None


def _role_of(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or message.get("kind") or "")
    return str(getattr(message, "kind", "") or "")


def _family(usage: Any) -> str | None:
    family = _value(usage, "api_family") or _value(usage, "family")
    family = str(getattr(family, "value", family) or "")
    if family:
        return family
    # Old rows predate the family field. Cache creation is Anthropic-only, and
    # reads larger than input mean input excluded those reads. A plain cached
    # subset is ambiguous, so leave it unknown rather than guessing.
    cached = _count(_value(usage, "cached_input_tokens"))
    native = _count(_value(usage, "input_tokens"))
    if _value(usage, "cache_creation_input_tokens") is not None or (
        cached is not None and native is not None and cached > native
    ):
        return ANTHROPIC
    return None


def summarize_usage(messages: list[Any], *, family: str | None = None) -> dict[str, Any]:
    """Sum every message's usage, including assistant tool-call-only steps.

    ``family`` supplies the session's API family for rows saved before usage
    began carrying its own family.
    """
    input_tokens = cached = created = output = 0
    has_usage = False
    estimated = False
    assistants = assistants_with_usage = 0
    for message in messages or []:
        usage = _usage_of(message)
        if _role_of(message) == "assistant":
            assistants += 1
        if not usage:
            continue
        native = _count(_value(usage, "input_tokens"))
        if native is None:
            native = _count(_value(usage, "prompt_tokens"))
        cache_read = _count(_value(usage, "cached_input_tokens"))
        if cache_read is None:
            cache_read = _count(_value(usage, "prompt_cache_hit_tokens")) or 0
        cache_write = _count(_value(usage, "cache_creation_input_tokens")) or 0
        out = _count(_value(usage, "output_tokens"))
        if out is None:
            out = _count(_value(usage, "completion_tokens"))
        if native is None and out is None and not cache_read and not cache_write:
            continue
        has_usage = True
        if _role_of(message) == "assistant":
            assistants_with_usage += 1
        if native is not None:
            row_family = _family(usage) or family
            if row_family is None:
                estimated = estimated or bool(cache_read or cache_write)
                input_tokens += native
                cached += cache_read
                created += cache_write
            elif row_family == ANTHROPIC:
                input_tokens += native
                cached += cache_read
                created += cache_write
            else:
                input_tokens += max(0, native - cache_read)
                cached += cache_read
                # OpenAI-family prompt counts already include cache reads. A
                # cache-write counter, when present, is still separate input.
                created += cache_write
        if out is not None:
            output += out
    total_input = input_tokens + cached + created
    return {
        "input": input_tokens,
        "cached": cached,
        "created": created,
        "output": output,
        "total_input": total_input,
        "total": total_input + output,
        "has_usage": has_usage,
        "complete": has_usage and (assistants == 0 or assistants == assistants_with_usage),
        "estimated": estimated,
    }
