from copy import deepcopy

import pytest

from nexus.core.protocol import request_body
from nexus.core.replay import capture
from nexus.model import APIFamily, ModelCapabilities, ModelProfile, ProviderContinuation


@pytest.mark.parametrize("family", list(APIFamily))
def test_native_replay_is_ordered_source_bound_and_immutable(family):
    profile = ModelProfile(api_family=family, model="same", base_url="https://same.invalid",
        capabilities=ModelCapabilities(encrypted_reasoning=True))
    if family == APIFamily.ANTHROPIC_MESSAGES:
        response = {"content": [
            {"type": "text", "text": "first"},
            {"type": "thinking", "thinking": "reason", "signature": "sig"},
            {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "x"}}]}
        key = "content"
    elif family == APIFamily.OPENAI_RESPONSES:
        response = {"output": [
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "first"}]},
            {"type": "reasoning", "id": "r1", "summary": [], "encrypted_content": "encrypted"},
            {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": '{"path":"x"}'}]}
        key = "output"
    else:
        response = {"choices": [{"message": {"reasoning_content": "reason"}}]}
        key = None
    native = capture(profile, ProviderContinuation(family, "response", response, ()))
    messages = [{"role": "assistant", "content": "first", "native": native,
                 "tool_calls": [{"id": "c1", "name": "read_file", "arguments": '{"path":"x"}'}]},
                {"role": "tool", "tool_call_id": "c1", "content": "found"}]
    before = deepcopy(messages)
    body = request_body(profile, "system", messages, [])
    if family == APIFamily.ANTHROPIC_MESSAGES:
        assert body["messages"][0]["content"] == response[key]
    elif family == APIFamily.OPENAI_RESPONSES:
        assert body["input"][:-1] == response[key]
        assert body["include"] == ["reasoning.encrypted_content"]
    else:
        assert body["messages"][1]["reasoning_content"] == "reason"
    foreign = request_body(profile.model_copy(update={"model": "foreign"}), "system", messages, [])
    assert "sig" not in str(foreign)
    assert "encrypted_content': 'encrypted'" not in str(foreign)
    assert "reasoning_content" not in str(foreign)
    assert messages == before
    assert request_body(profile, "system", messages, []) == body


def test_unsigned_display_thinking_is_never_promoted():
    profile = ModelProfile(api_family=APIFamily.ANTHROPIC_MESSAGES,
        model="fake", base_url="https://fake.invalid")
    continuation = ProviderContinuation(profile.api_family, None,
        {"content": [{"type": "thinking", "thinking": "display"}]}, ())
    assert capture(profile, continuation) is None


@pytest.mark.parametrize("family", list(APIFamily))
def test_agent_records_native_state_and_uses_it_on_the_next_tool_request(tmp_path, monkeypatch, family):
    import asyncio
    from nexus.core.agent import Agent
    from nexus.core.permission import default_policy
    from nexus.core.workspace import Workspace
    from nexus.model import ModelEvent, RuntimeCredentials, ToolCall
    from nexus.tools import build

    (tmp_path / "x").write_text("actual result", encoding="utf-8")
    profile = ModelProfile(api_family=family, model="same", base_url="https://same.invalid")
    if family == APIFamily.ANTHROPIC_MESSAGES:
        response = {"content": [{"type": "thinking", "thinking": "read first", "signature": "saved-signature"},
            {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "x"}}]}
    elif family == APIFamily.OPENAI_RESPONSES:
        response = {"output": [{"type": "reasoning", "encrypted_content": "saved-encrypted", "summary": []},
            {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": '{"path":"x"}'}]}
    else:
        response = {"choices": [{"message": {"reasoning_content": "saved-reasoning"}}]}
    seen = []

    class Gateway:
        def __init__(self, *a, **kw):
            pass

        async def stream(self, _profile, body, **kw):
            seen.append(body)
            if len(seen) == 1:
                yield ModelEvent("tool_call", tool_call=ToolCall("c1", "read_file", '{"path":"x"}', {"path": "x"}))
                yield ModelEvent("complete", finish_reason="tool_use" if family == APIFamily.ANTHROPIC_MESSAGES else "tool_calls",
                    continuation=ProviderContinuation(family, None, response, ()))
            else:
                yield ModelEvent("text_delta", text="verified")
                yield ModelEvent("complete", finish_reason="stop")

    monkeypatch.setattr("nexus.core.agent.ModelGateway", Gateway)
    tools, context = build({}, root=tmp_path)
    saved = []
    agent = Agent(profile=profile, credentials=RuntimeCredentials("fake"), registry=tools, context=context,
        workspace=Workspace(tmp_path), policy=default_policy("yolo"), on_message=lambda m: saved.append(deepcopy(m)))

    async def drive():
        return [event async for event in agent.run([{"role": "user", "content": "read x"}])]

    assert asyncio.run(drive())[-1].data["stopped"] == "complete"
    assert saved[0]["native"] == capture(profile, ProviderContinuation(family, None, response, ()))
    assert any(marker in str(seen[1]) for marker in ("saved-signature", "saved-encrypted", "saved-reasoning"))
    assert "actual result" in str(seen[1])
