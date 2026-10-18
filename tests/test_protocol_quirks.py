"""Regression tests for the two protocol quirks found against a real endpoint.

Both are compatibility issues, not correctness issues: a stricter parser
rejected streams that the provider considers valid.
"""

from __future__ import annotations

import json

import pytest

from nexus.model._parsers import ChatParser, ResponsesParser
from nexus.model._sse import ProtocolError


def test_chat_tool_delta_without_type_is_accepted():
    """Streaming tool_calls may omit `type`; only the final message adds it."""
    parser = ChatParser()
    frames = [
        {
            "id": "c1",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": "read_file", "arguments": '{"path":'},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "c1",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {"index": 0, "function": {"arguments": ' "a.txt"}'}}
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "c1",
            "choices": [
                {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
            ],
        },
    ]
    events = []
    for frame in frames:
        events.extend(parser.feed(frame))
    calls = [e for e in events if e.type == "tool_call"]
    assert len(calls) == 1
    assert calls[0].tool_call.name == "read_file"
    assert calls[0].tool_call.arguments == {"path": "a.txt"}


def test_chat_rejects_a_declared_non_function_type():
    parser = ChatParser()
    frames = [
        {
            "id": "c1",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "web_search",
                                "function": {"name": "x", "arguments": "{}"},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "c1",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        },
    ]
    with pytest.raises(ProtocolError):
        for frame in frames:
            parser.feed(frame)


def test_responses_accepts_a_reissued_item_id():
    """The final assembled item may carry a fresh id; call_id is the identity."""
    parser = ResponsesParser()
    collected = []
    collected += parser.feed(
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "item_streamed",
                "type": "function_call",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": "",
            },
        }
    )
    collected += parser.feed(
        {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "item_id": "item_streamed",
            "delta": '{"path": "a.txt"}',
        }
    )
    collected += parser.feed(
        {
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "id": "item_streamed",
                "type": "function_call",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": '{"path": "a.txt"}',
                "status": "completed",
            },
        }
    )
    collected += parser.feed(
        {
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "id": "item_final",
                        "type": "function_call",
                        "call_id": "call_1",
                        "name": "read_file",
                        "arguments": '{"path": "a.txt"}',
                        "status": "completed",
                    }
                ],
            },
        }
    )
    calls = [e for e in collected if e.type == "tool_call"]
    assert len(calls) == 1
    assert calls[0].tool_call.id == "call_1"
    assert calls[0].tool_call.arguments == {"path": "a.txt"}


def test_responses_rejects_changed_arguments():
    parser = ResponsesParser()
    parser.feed(
        {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "id": "i1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": "",
            },
        }
    )
    parser.feed(
        {
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "item_id": "i1",
            "arguments": '{"path": "a.txt"}',
        }
    )
    with pytest.raises(ProtocolError):
        parser.feed(
            {
                "type": "response.completed",
                "response": {
                    "id": "resp_1",
                    "status": "completed",
                    "output": [
                        {
                            "id": "i1",
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "read_file",
                            "arguments": '{"path": "b.txt"}',
                            "status": "completed",
                        }
                    ],
                },
            }
        )


def test_tool_result_roundtrip_shape():
    """The chat transcript we rebuild must match what the protocol expects."""
    from nexus.core.protocol import request_body
    from nexus.model import APIFamily, ModelProfile

    profile = ModelProfile(
        api_family=APIFamily.OPENAI_CHAT_COMPLETIONS,
        model="m",
        base_url="https://x.test/v1",
    )
    body = request_body(
        profile,
        "sys",
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "name": "read_file", "arguments": "{}"}],
            },
            {"role": "tool", "tool_call_id": "call_1", "name": "read_file", "content": "ok"},
        ],
        [],
    )
    messages = body["messages"]
    # messages[0] is the system prompt; the assistant turn follows the user turn.
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[2]["tool_calls"][0]["id"] == "call_1"
    assert messages[3]["role"] == "tool"
    assert messages[3]["tool_call_id"] == "call_1"
    json.dumps(body)  # must be serialisable
