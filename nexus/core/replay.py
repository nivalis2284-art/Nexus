"""Minimal, source-bound native assistant continuation; display text is not replay."""

from copy import deepcopy

from ..model import APIFamily


def _response(family, response):
    if not isinstance(response, dict):
        return None
    if family == APIFamily.ANTHROPIC_MESSAGES:
        blocks = response.get("content", [])
        if not isinstance(blocks, list):
            return None
        if not any(isinstance(b, dict) and (
            b.get("type") == "thinking" and b.get("signature")
            or b.get("type") == "redacted_thinking" and b.get("data")
        ) for b in blocks):
            return None
        valid = []
        for block in blocks:
            if not isinstance(block, dict):
                return None
            kind = block.get("type")
            if kind == "thinking":
                if not isinstance(block.get("thinking"), str) or not isinstance(block.get("signature"), str) or not block["signature"]:
                    return None
                valid.append({"type": kind, "thinking": block["thinking"], "signature": block["signature"]})
            elif kind == "redacted_thinking" and isinstance(block.get("data"), str):
                valid.append({"type": kind, "data": block["data"]})
            elif kind == "text" and isinstance(block.get("text"), str):
                valid.append({"type": kind, "text": block["text"]})
            elif kind == "tool_use" and isinstance(block.get("id"), str) and isinstance(block.get("name"), str) and isinstance(block.get("input"), dict):
                valid.append({key: deepcopy(block[key]) for key in ("type", "id", "name", "input")})
            else:
                return None
        return {"content": valid}
    if family == APIFamily.OPENAI_CHAT_COMPLETIONS:
        message = response
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
        value = message.get("reasoning_content")
        return {"reasoning_content": value} if isinstance(value, str) and value else None
    if family == APIFamily.OPENAI_RESPONSES:
        items = response.get("output", [])
        if not isinstance(items, list) or not any(isinstance(i, dict) and i.get("type") == "reasoning" and i.get("encrypted_content") for i in items):
            return None
        output = []
        fields = {
            "reasoning": ("type", "id", "summary", "encrypted_content"),
            "message": ("type", "id", "role", "content", "status"),
            "function_call": ("type", "id", "call_id", "name", "arguments", "status"),
        }
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in fields:
                return None
            if item["type"] == "reasoning" and not isinstance(item.get("encrypted_content"), str):
                return None
            output.append({key: deepcopy(item[key]) for key in fields[item["type"]] if key in item})
        return {"output": output}
    return None


def capture(profile, continuation):
    if continuation is None or continuation.api_family != profile.api_family:
        return None
    response = _response(profile.api_family, continuation.response)
    if response is None:
        return None
    return {"api_family": profile.api_family.value, "model": profile.model,
            "base_url": profile.base_url, "response": response}


def replayable(profile, native):
    if not isinstance(native, dict) or any(native.get(key) != value for key, value in (
        ("api_family", profile.api_family.value), ("model", profile.model), ("base_url", profile.base_url)
    )):
        return None
    return _response(profile.api_family, native.get("response"))
