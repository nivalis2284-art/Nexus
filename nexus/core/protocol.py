"""Translate one internal transcript into each protocol's native request shape.

The internal transcript is a flat, provider-neutral list::

    {"role": "user",      "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [{"id","name","arguments"}]}
    {"role": "tool",      "tool_call_id": "...", "name": "...", "content": "..."}

Each builder returns a request body. The gateway then applies model/stream and
validates family-specific fields, so nothing here may set credentials.
"""

from __future__ import annotations

import json
from typing import Any

from ..model import APIFamily, ModelProfile


def _chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.get("content") or None}
            calls = message.get("tool_calls") or []
            if calls:
                entry["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call.get("arguments") or "{}",
                        },
                    }
                    for call in calls
                ]
            out.append(entry)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": message["tool_call_id"],
                    "content": message.get("content") or "",
                }
            )
        else:
            out.append({"role": role, "content": message.get("content") or ""})
    return out


def _anthropic_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            text = message.get("content") or ""
            if text:
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                try:
                    parsed = json.loads(call.get("arguments") or "{}")
                except ValueError:
                    parsed = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["name"],
                        "input": parsed if isinstance(parsed, dict) else {},
                    }
                )
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": message.get("content") or "",
            }
            # Consecutive tool results belong to one user turn.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            out.append({"role": role, "content": message.get("content") or ""})
    return out


def _responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            text = message.get("content") or ""
            if text:
                out.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            for call in message.get("tool_calls") or []:
                out.append(
                    {
                        "type": "function_call",
                        "call_id": call["id"],
                        "name": call["name"],
                        "arguments": call.get("arguments") or "{}",
                    }
                )
        elif role == "tool":
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message.get("content") or "",
                }
            )
        else:
            out.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": message.get("content") or ""}],
                }
            )
    return out


def _tools(payload: list[dict[str, Any]], family: APIFamily) -> list[dict[str, Any]]:
    if family == APIFamily.ANTHROPIC_MESSAGES:
        return [
            {
                "name": item["name"],
                "description": item["description"],
                "input_schema": item["parameters"],
            }
            for item in payload
        ]
    if family == APIFamily.OPENAI_RESPONSES:
        return [
            {
                "type": "function",
                "name": item["name"],
                "description": item["description"],
                "parameters": item["parameters"],
                "strict": False,
            }
            for item in payload
        ]
    return [
        {
            "type": "function",
            "function": {
                "name": item["name"],
                "description": item["description"],
                "parameters": item["parameters"],
            },
        }
        for item in payload
    ]


def request_body(
    profile: ModelProfile,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
) -> dict[str, Any]:
    family = profile.api_family
    max_tokens = profile.max_output_tokens or 8192
    tool_list = _tools(tools, family)
    if family == APIFamily.ANTHROPIC_MESSAGES:
        body: dict[str, Any] = {
            "model": profile.model,
            "system": system,
            "messages": _anthropic_messages(messages),
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tool_list:
            body["tools"] = tool_list
        return body
    if family == APIFamily.OPENAI_RESPONSES:
        body = {
            "model": profile.model,
            "instructions": system,
            "input": _responses_input(messages),
            "max_output_tokens": max_tokens,
            "stream": True,
        }
        if tool_list:
            body["tools"] = tool_list
        return body
    body = {
        "model": profile.model,
        "messages": [{"role": "system", "content": system}, *_chat_messages(messages)],
        "stream": True,
    }
    if profile.max_output_tokens:
        body["max_tokens"] = max_tokens
    if tool_list:
        body["tools"] = tool_list
    return body
