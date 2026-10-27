"""Translate one internal transcript into each protocol's native request shape.

The internal transcript is a flat, provider-neutral list::

    {"role": "user",      "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [{"id","name","arguments"}]}
    {"role": "tool",      "tool_call_id": "...", "name": "...", "content": "..."}

``content`` may also be a list of blocks when a message carries images::

    [{"type": "text", "text": "..."},
     {"type": "image", "mime": "image/png", "data": "<base64>"}]

Each builder returns a request body. The gateway then applies model/stream and
validates family-specific fields, so nothing here may set credentials.
"""

from __future__ import annotations

import json
from typing import Any

from ..model import APIFamily, ModelProfile
from . import effort as reasoning
from .replay import replayable


def is_blocks(content: Any) -> bool:
    return isinstance(content, list)


def blocks_to_text(content: Any) -> str:
    """Flatten block content to plain text, for places that need a string."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _image_data_url(block: dict[str, Any]) -> str:
    mime = block.get("mime") or "image/png"
    data = block.get("data") or ""
    return f"data:{mime};base64,{data}"


def _chat_content(content: Any) -> Any:
    """OpenAI Chat: images are ``image_url`` parts inside a content array."""
    if not is_blocks(content):
        return content or ""
    parts: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "text", "text": str(block.get("text") or "")})
        elif kind == "image":
            parts.append({"type": "image_url", "image_url": {"url": _image_data_url(block)}})
    return parts or ""


def _anthropic_image_block(block: dict[str, Any]) -> dict[str, Any]:
    """Anthropic: source is a typed object, not a data URL."""
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": block.get("mime") or "image/png",
            "data": block.get("data") or "",
        },
    }


def _anthropic_user_content(content: Any) -> Any:
    if not is_blocks(content):
        return content or ""
    parts: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "text", "text": str(block.get("text") or "")})
        elif kind == "image":
            parts.append(_anthropic_image_block(block))
    return parts or ""


def _responses_content(content: Any) -> Any:
    """Responses API: ``input_image`` takes the data URL directly."""
    if not is_blocks(content):
        return [{"type": "input_text", "text": content or ""}]
    parts: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            parts.append({"type": "input_text", "text": str(block.get("text") or "")})
        elif kind == "image":
            parts.append({"type": "input_image", "image_url": _image_data_url(block)})
    return parts


def _chat_messages(
    profile: ModelProfile, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            entry: dict[str, Any] = {
                "role": "assistant",
                "content": blocks_to_text(message.get("content")) or None,
            }
            native = replayable(profile, message.get("native"))
            if native and isinstance(native.get("reasoning_content"), str):
                entry["reasoning_content"] = native["reasoning_content"]
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
                    "content": blocks_to_text(message.get("content")),
                }
            )
        else:
            out.append({"role": role, "content": _chat_content(message.get("content"))})
    return out


def _anthropic_messages(
    profile: ModelProfile, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            native = replayable(profile, message.get("native"))
            if native:
                out.append({"role": "assistant", "content": native["content"]})
                continue
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
                "content": blocks_to_text(message.get("content")),
            }
            # Consecutive tool results belong to one user turn.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        else:
            out.append(
                {"role": role, "content": _anthropic_user_content(message.get("content"))}
            )
    return out


def _responses_input(
    profile: ModelProfile, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        role = message["role"]
        if role == "assistant":
            native = replayable(profile, message.get("native"))
            if native:
                out.extend(native["output"])
                continue
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
                    "output": blocks_to_text(message.get("content")),
                }
            )
        else:
            out.append(
                {
                    "type": "message",
                    "role": "user",
                    "content": _responses_content(message.get("content")),
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
    effort: str | None = None,
) -> dict[str, Any]:
    if not profile.capabilities.images:
        messages = [
            {**message, "content": blocks_to_text(message["content"]) +
             "\n[Image data omitted for this text-only model. Use a vision model if visual inspection is needed.]"}
            if is_blocks(message.get("content")) and any(
                block.get("type") == "image" for block in message["content"]
                if isinstance(block, dict)
            ) else message
            for message in messages
        ]
    family = profile.api_family
    max_tokens = profile.max_output_tokens or 8192
    tool_list = _tools(tools, family)
    if family == APIFamily.ANTHROPIC_MESSAGES:
        body: dict[str, Any] = {
            "model": profile.model,
            "system": system,
            "messages": _anthropic_messages(profile, messages),
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tool_list:
            body["tools"] = tool_list
        reasoning.apply(body, family, profile, effort)
        return body
    if family == APIFamily.OPENAI_RESPONSES:
        body = {
            "model": profile.model,
            "instructions": system,
            "input": _responses_input(profile, messages),
            "max_output_tokens": max_tokens,
            "stream": True,
        }
        if tool_list:
            body["tools"] = tool_list
        if profile.capabilities.encrypted_reasoning:
            body["include"] = ["reasoning.encrypted_content"]
        reasoning.apply(body, family, profile, effort)
        return body
    body = {
        "model": profile.model,
        "messages": [{"role": "system", "content": system}, *_chat_messages(profile, messages)],
        "stream": True,
    }
    if profile.max_output_tokens:
        body["max_tokens"] = max_tokens
    if tool_list:
        body["tools"] = tool_list
    reasoning.apply(body, family, profile, effort)
    return body
