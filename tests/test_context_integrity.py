"""Behavioral tests for context estimates and tool-interaction boundaries."""

from __future__ import annotations

import pytest

from nexus.core import compaction, prompt

LONG_ARGUMENTS = '{"text": "' + "x" * 30_000 + '"}'


def test_flat_tool_arguments_are_counted_in_both_estimators():
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "name": "write_file", "arguments": LONG_ARGUMENTS}],
    }
    text_cost = prompt.estimate(prompt._text_of(message))
    assert text_cost > 9_000
    assert compaction.message_tokens(message) > 9_000
    assert prompt.estimate(compaction._text_of(message)) == text_cost


def test_legacy_nested_tool_shape_is_still_counted():
    legacy = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call_1", "function": {"name": "write_file", "arguments": LONG_ARGUMENTS}}
        ],
    }
    current = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "name": "write_file", "arguments": LONG_ARGUMENTS}],
    }
    assert prompt._text_of(legacy) == prompt._text_of(current)
    assert compaction.message_tokens(legacy) == compaction.message_tokens(current)
    assert compaction.message_tokens(legacy) > 9_000


def test_select_keeps_a_tool_interaction_together():
    call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "a", "name": "read_file", "arguments": "{}"}],
    }
    result = {"role": "tool", "tool_call_id": "a", "name": "read_file", "content": "ok"}
    older = {"role": "user", "content": "read notes"}
    newest = {"role": "user", "content": "continue"}
    budget = prompt.Budget(
        context_limit=10_000,
        output_reserve=0,
        tool_reserve=0,
    )
    # Leave only the newest interaction inside the messages allowance.
    chosen = prompt.select(
        [older, call, result, newest],
        budget,
        fixed=budget.input_limit - compaction.approx_tokens([call, result, newest]),
    )
    assert chosen.messages == [call, result, newest]
    assert chosen.manifest["dropped"] >= 1


def test_select_falls_back_to_the_whole_oversized_latest_interaction():
    call = {
        "role": "assistant",
        "tool_calls": [{"id": "a", "name": "grep", "arguments": "{}"}],
    }
    result = {"role": "tool", "tool_call_id": "a", "content": "x" * 5_000}
    budget = prompt.Budget(context_limit=2_000, output_reserve=0, tool_reserve=0)
    chosen = prompt.select([call, result], budget)
    assert chosen.messages == [call, result]
    assert [item["role"] for item in chosen.messages] == ["assistant", "tool"]


def test_select_omits_orphan_and_unfinished_tool_messages():
    orphan = {"role": "tool", "tool_call_id": "missing", "content": "stale"}
    unfinished = {
        "role": "assistant",
        "tool_calls": [{"id": "open", "name": "bash", "arguments": "{}"}],
    }
    question = {"role": "user", "content": "next"}
    chosen = prompt.select([orphan, unfinished, question], prompt.Budget())
    assert chosen.messages == [question]
    assert chosen.manifest["dropped"] == 2


def test_select_preserves_multiple_results_for_one_call():
    call = {
        "role": "assistant",
        "tool_calls": [
            {"id": "a", "name": "bash", "arguments": "{}"},
            {"id": "b", "name": "bash", "arguments": "{}"},
        ],
    }
    first = {"role": "tool", "tool_call_id": "a", "content": "one"}
    second = {"role": "tool", "tool_call_id": "b", "content": "two"}
    question = {"role": "user", "content": "next"}
    chosen = prompt.select([call, first, second, question], prompt.Budget())
    assert chosen.messages == [call, first, second, question]


def test_a_result_for_another_call_never_gets_attached_to_this_one():
    call = {
        "role": "assistant",
        "tool_calls": [{"id": "a", "name": "bash", "arguments": "{}"}],
    }
    wrong = {"role": "tool", "tool_call_id": "b", "content": "wrong"}
    question = {"role": "user", "content": "next"}
    chosen = prompt.select([call, wrong, question], prompt.Budget())
    assert chosen.messages == [question]


def test_an_extra_result_is_dropped_without_losing_the_matching_result():
    call = {
        "role": "assistant",
        "tool_calls": [
            {"id": "a", "name": "bash", "arguments": "{}"},
            {"id": "b", "name": "bash", "arguments": "{}"},
        ],
    }
    first = {"role": "tool", "tool_call_id": "a", "content": "first"}
    second = {"role": "tool", "tool_call_id": "b", "content": "second"}
    extra = {"role": "tool", "tool_call_id": "c", "content": "extra"}
    question = {"role": "user", "content": "next"}
    chosen = prompt.select([call, first, extra, second, question], prompt.Budget())
    assert chosen.messages == [call, first, second, question]


def test_split_keeps_a_multi_result_exchange_together():
    call = {
        "role": "assistant",
        "tool_calls": [{"id": "a", "name": "bash", "arguments": "{}"}],
    }
    first = {"role": "tool", "tool_call_id": "a", "content": "one"}
    second = {"role": "tool", "tool_call_id": "a", "content": "two"}
    newest = {"role": "user", "content": "new question"}
    head, tail = compaction.split_for_summary([call, first, second, newest], 1)
    assert head == []
    assert tail == [call, first, second, newest]


def test_oversized_multi_message_latest_group_is_not_cut():
    old = {"role": "user", "content": "old " * 10_000}
    call = {
        "role": "assistant",
        "tool_calls": [{"id": "a", "name": "read_file", "arguments": "{}"}],
    }
    result = {"role": "tool", "tool_call_id": "a", "content": "x" * 50_000}
    head, tail = compaction.split_for_summary([old, call, result], retain_tokens=1)
    assert head == [old]
    assert tail == [call, result]


def test_oversized_latest_single_result_keeps_its_small_call():
    old = {"role": "user", "content": "old " * 10_000}
    call = {
        "role": "assistant",
        "tool_calls": [{"id": "a", "name": "read_file", "arguments": "{}"}],
    }
    result = {"role": "tool", "tool_call_id": "a", "content": "x" * 50_000}
    head, tail = compaction.split_for_summary([old, call, result], retain_tokens=5_000)
    assert head == [old]
    assert tail == [call, result]


def test_single_oversized_message_still_moves_into_the_head():
    old = {"role": "user", "content": "old " * 10_000}
    giant = {"role": "user", "content": "x" * 50_000}
    head, tail = compaction.split_for_summary([old, giant], retain_tokens=1)
    assert head == [old]
    assert tail == [giant]


def test_split_reports_nothing_safe_for_an_unfinished_call():
    messages = [
        {"role": "user", "content": "old " * 100},
        {"role": "assistant", "tool_calls": [{"id": "a", "name": "x", "arguments": "{}"}]},
    ]
    head, tail = compaction.split_for_summary(messages, retain_tokens=1)
    assert head == []
    assert tail == messages
    with pytest.raises(compaction.CompactionBlocked):
        compaction.assert_paired(messages)
