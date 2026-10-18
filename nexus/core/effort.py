"""Effort levels: five fixed tiers, mapped per protocol.

The vocabulary is fixed by the product (low / medium / high / xhigh / max) but
the wire representation is per protocol, because the three families do not share
one:

* anthropic_messages      -> ``output_config.effort`` plus an enabled thinking block
* openai_chat_completions -> ``reasoning_effort``
* openai_responses        -> ``reasoning.effort``

A profile may override the wire value for a level (some gateways spell tiers
differently), which is why the mapping lives here and is applied at request
build time rather than hard-coded into each builder.
"""

from __future__ import annotations

from typing import Any

from ..model import APIFamily

LEVELS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

LABELS = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "xhigh": "很高",
    "max": "最高",
}

# Tokens budgeted for a thinking block on Anthropic. Only used when thinking is
# switched on; the three OpenAI families take a level name, not a budget.
THINKING_BUDGET = {
    "low": 2_000,
    "medium": 6_000,
    "high": 12_000,
    "xhigh": 24_000,
    "max": 32_000,
}


class EffortError(ValueError):
    pass


def normalize(level: str | None) -> str | None:
    if level in (None, "", "off", "none", "default"):
        return None
    value = str(level).strip().lower()
    if value not in LEVELS:
        raise EffortError(
            f"Unknown reasoning level '{level}'. Choose one of: {', '.join(LEVELS)}."
        )
    return value


def wire_value(profile: Any, level: str) -> str:
    """The value this profile wants for a level (per-level override wins)."""
    overrides = getattr(profile, "effort_values", None) or {}
    if isinstance(overrides, dict):
        value = overrides.get(level)
        if isinstance(value, str) and value:
            return value
    return level


def apply(body: dict[str, Any], family: APIFamily, profile: Any, level: str | None) -> None:
    """Mutate a request body to carry the reasoning level.

    Absent level means "provider default": nothing is added, so a model that
    rejects the field is never sent one by mistake. The level is normalized
    here as well, because a stored ``"off"`` must never reach the wire.
    """
    normalized = normalize(level)
    if not normalized:
        return
    level = normalized
    value = wire_value(profile, level)
    if family == APIFamily.OPENAI_CHAT_COMPLETIONS:
        body["reasoning_effort"] = value
        return
    if family == APIFamily.OPENAI_RESPONSES:
        body["reasoning"] = {**(body.get("reasoning") or {}), "effort": value}
        return
    # Anthropic: thinking must be enabled for the effort to mean anything.
    budget = THINKING_BUDGET.get(level, 6_000)
    body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    body["output_config"] = {**(body.get("output_config") or {}), "effort": value}
    # A thinking block counts against max_tokens, so the window must fit both.
    current = body.get("max_tokens")
    if type(current) is int and current <= budget:
        body["max_tokens"] = budget + current


def describe(family: APIFamily, level: str, profile: Any = None) -> dict[str, Any]:
    """Human-readable summary for the UI, without sending anything."""
    if not level:
        return {"level": None, "field": "(provider default)", "value": None}
    value = wire_value(profile, level) if profile is not None else level
    field = {
        APIFamily.OPENAI_CHAT_COMPLETIONS: "reasoning_effort",
        APIFamily.OPENAI_RESPONSES: "reasoning.effort",
        APIFamily.ANTHROPIC_MESSAGES: "output_config.effort",
    }[family]
    return {
        "level": level,
        "field": field,
        "value": value,
        "budget_tokens": THINKING_BUDGET.get(level)
        if family == APIFamily.ANTHROPIC_MESSAGES
        else None,
    }
