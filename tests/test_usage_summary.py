from __future__ import annotations

from typing import ClassVar

from nexus.model import APIFamily
from nexus.model._parsers import usage_from
from nexus.runtime.usage import summarize_usage


def test_anthropic_cache_counts_are_added_to_input():
    summary = summarize_usage([{
        "role": "assistant",
        "usage": {"input_tokens": 20, "cached_input_tokens": 80,
                  "cache_creation_input_tokens": 5, "output_tokens": 7,
                  "api_family": "anthropic_messages"},
    }])
    assert summary == {
        "input": 20, "cached": 80, "created": 5, "output": 7,
        "total_input": 105, "total": 112, "has_usage": True, "complete": True,
        "estimated": False,
    }


def test_openai_cache_hits_are_already_in_prompt_tokens():
    summary = summarize_usage([{
        "role": "assistant",
        "usage": {"input_tokens": 100, "cached_input_tokens": 80,
                  "output_tokens": 5, "api_family": "openai_chat_completions"},
    }])
    assert summary["input"] == 20
    assert summary["cached"] == 80
    assert summary["total_input"] == 100
    assert summary["total"] == 105


def test_deepseek_cache_hits_are_parsed_and_not_double_counted():
    usage = usage_from({
        "prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105,
        "prompt_cache_hit_tokens": 70, "prompt_cache_miss_tokens": 30,
    }, APIFamily.OPENAI_CHAT_COMPLETIONS)
    assert usage.cached_input_tokens == 70
    summary = summarize_usage([{"role": "assistant", "usage": {
        "input_tokens": 100, "cached_input_tokens": 70, "output_tokens": 5,
        "api_family": "openai_chat_completions"},
    }])
    assert (summary["input"], summary["total_input"], summary["total"]) == (30, 100, 105)


def test_tool_only_attempt_without_usage_is_reported_incomplete():
    messages = [
        {"role": "assistant", "usage": None, "tool_calls": [{"id": "c"}]},
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "usage": {"input_tokens": 4, "output_tokens": 2,
                                         "api_family": "openai_chat_completions"}},
    ]
    summary = summarize_usage(messages)
    assert summary["has_usage"] is True
    assert summary["complete"] is False
    assert summary["total"] == 6


def test_message_data_usage_and_empty_history():
    class Message:
        kind = "assistant"
        data: ClassVar = {"usage": {"input_tokens": 9, "output_tokens": 1,
                                    "api_family": "openai_responses"}}

    assert summarize_usage([Message()])["total"] == 10
    assert summarize_usage([]) == {
        "input": 0, "cached": 0, "created": 0, "output": 0,
        "total_input": 0, "total": 0, "has_usage": False, "complete": False,
        "estimated": False,
    }


def test_legacy_cached_subset_is_marked_estimated():
    summary = summarize_usage([{
        "role": "assistant",
        "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 5},
    }])
    assert summary["estimated"] is True
    assert summary["complete"] is True


def test_session_family_resolves_legacy_rows():
    summary = summarize_usage(
        [{"role": "assistant", "usage": {"input_tokens": 100,
                                          "cached_input_tokens": 80, "output_tokens": 5}}],
        family="anthropic_messages",
    )
    assert summary["estimated"] is False
    assert summary["total_input"] == 180
