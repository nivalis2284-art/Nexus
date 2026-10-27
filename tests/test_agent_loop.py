"""End-to-end test: a scripted fake model drives the real agent loop.

No network. The gateway is stubbed at the transport boundary, so the agent's
request building, tool dispatch, permission gate and persistence all run for
real against a temporary workspace.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nexus.core.agent import Agent
from nexus.core.permission import default_policy
from nexus.core.retry import RetryPolicy
from nexus.core.workspace import inspect
from nexus.model import (
    APIFamily,
    ModelCapabilities,
    ModelError,
    ModelEvent,
    ModelProfile,
    RuntimeCredentials,
    Usage,
)
from nexus.tools import build

SSE = "text/event-stream"


def profile() -> ModelProfile:
    return ModelProfile(
        api_family=APIFamily.OPENAI_CHAT_COMPLETIONS,
        model="fake",
        base_url="https://fake.test/v1",
        capabilities=ModelCapabilities(tools=True),
        context_window_tokens=32000,
        max_output_tokens=1024,
    )


def chat_frame(delta: dict, finish: str | None = None) -> str:
    payload = {
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return "data: " + json.dumps(payload) + "\n\n"


def stream_of(frames: list[str]) -> str:
    return "".join(frames) + "data: [DONE]\n\n"


def tool_call_stream(call_id: str, name: str, arguments: dict) -> str:
    first = chat_frame(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }
    )
    return stream_of([first, chat_frame({}, "tool_calls")])


def text_stream(text: str) -> str:
    return stream_of([chat_frame({"content": text}), chat_frame({}, "stop")])


def make_client(responses: list[str], seen: list[dict]):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": SSE}, text=responses.pop(0))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "notes.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    return tmp_path


def run(agent: Agent, messages: list[dict], responses: list[str], seen: list[dict]):
    import nexus.core.agent as module

    original = module._client

    def patched():
        return make_client(responses, seen)

    module._client = patched
    try:
        async def drive():
            events = []
            async for event in agent.run(messages):
                events.append(event)
            return events

        return asyncio.run(drive())
    finally:
        module._client = original


def build_agent(root: Path, *, policy=None) -> Agent:
    registry, context = build({})
    context.root = root
    return Agent(
        profile=profile(),
        credentials=RuntimeCredentials("test-key"),
        registry=registry,
        context=context,
        workspace=inspect(root),
        policy=policy or default_policy("yolo"),
    )


def test_reads_a_file_and_answers(workspace: Path):
    seen: list[dict] = []
    agent = build_agent(workspace)
    messages = [{"role": "user", "content": "read notes.txt"}]
    events = run(
        agent,
        messages,
        [
            tool_call_stream("call_1", "read_file", {"path": "notes.txt"}),
            text_stream("It contains alpha and beta."),
        ],
        seen,
    )
    # "context" is a per-step accounting event; it carries no transcript change.
    transcript = [e for e in events if e.type != "context"]
    assert [e.type for e in transcript] == [
        "tool_start", "tool_result",
        "delta", "done",
    ]
    assert "1: alpha" in transcript[1].data["content"]
    assert transcript[2].text == "It contains alpha and beta."
    # The tool result went back to the model on the second request.
    second = seen[1]["messages"]
    assert any(m["role"] == "tool" and "alpha" in m["content"] for m in second)
    assert seen[0]["tools"][0]["function"]["name"] == "read_file"


def test_edits_a_file_with_exact_match(workspace: Path):
    seen: list[dict] = []
    agent = build_agent(workspace)
    messages = [{"role": "user", "content": "change beta to gamma"}]
    run(
        agent,
        messages,
        [
            tool_call_stream(
                "c1", "edit_file",
                {"path": "notes.txt", "old_text": "beta", "new_text": "gamma"},
            ),
            text_stream("Done."),
        ],
        seen,
    )
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "alpha\ngamma\n"


def test_ambiguous_edit_is_refused(workspace: Path):
    (workspace / "dup.txt").write_text("x\nx\n", encoding="utf-8")
    seen: list[dict] = []
    agent = build_agent(workspace)
    run(
        agent,
        [{"role": "user", "content": "edit dup"}],
        [
            tool_call_stream(
                "c1", "edit_file", {"path": "dup.txt", "old_text": "x", "new_text": "y"}
            ),
            text_stream("Could not."),
        ],
        seen,
    )
    assert (workspace / "dup.txt").read_text(encoding="utf-8") == "x\nx\n"


def test_path_escape_is_denied(workspace: Path):
    seen: list[dict] = []
    agent = build_agent(workspace)
    run(
        agent,
        [{"role": "user", "content": "read outside"}],
        [
            tool_call_stream("c1", "read_file", {"path": "../secret.txt"}),
            text_stream("Denied."),
        ],
        seen,
    )
    second = seen[1]["messages"]
    tool_message = [m for m in second if m["role"] == "tool"][0]
    assert "outside the workspace" in tool_message["content"]


def test_bash_runs_without_a_shell(workspace: Path):
    seen: list[dict] = []
    agent = build_agent(workspace)
    events = run(
        agent,
        [{"role": "user", "content": "echo hi"}],
        [
            tool_call_stream("c1", "bash", {"command": 'python -c "print(42)"'}),
            text_stream("ok"),
        ],
        seen,
    )
    transcript = [e for e in events if e.type != "context"]
    result = transcript[1].data
    assert result["ok"] is True
    assert "42" in result["content"]


def test_approval_gate_can_decline(workspace: Path):
    asked: list[str] = []

    async def approve(request) -> bool:
        asked.append(request.tool)
        return False

    registry, context = build({})
    context.root = workspace
    agent = Agent(
        profile=profile(),
        credentials=RuntimeCredentials("k"),
        registry=registry,
        context=context,
        workspace=inspect(workspace),
        policy=default_policy("ask"),
        approve=approve,
    )
    seen: list[dict] = []
    run(
        agent,
        [{"role": "user", "content": "write"}],
        [
            tool_call_stream("c1", "write_file", {"path": "new.txt", "content": "hi"}),
            text_stream("ok"),
        ],
        seen,
    )
    assert asked == ["write_file"]
    assert not (workspace / "new.txt").exists()


def test_max_steps_stops_the_loop(workspace: Path):
    seen: list[dict] = []
    registry, context = build({})
    context.root = workspace
    agent = Agent(
        profile=profile(),
        credentials=RuntimeCredentials("k"),
        registry=registry,
        context=context,
        workspace=inspect(workspace),
        policy=default_policy("yolo"),
        max_steps=2,
    )
    events = run(
        agent,
        [{"role": "user", "content": "loop"}],
        [
            tool_call_stream("c1", "glob", {"pattern": "*"}),
            tool_call_stream("c2", "glob", {"pattern": "*"}),
        ],
        seen,
    )
    assert events[-1].type == "done"
    assert events[-1].data["stopped"] == "max_steps"


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_abnormal_finish_reason_is_not_reported_complete(workspace: Path, finish: str):
    """A truncated or filtered answer must not look like a finished turn."""
    seen: list[dict] = []
    agent = build_agent(workspace)
    messages = [{"role": "user", "content": "answer fully"}]
    events = run(
        agent,
        messages,
        [stream_of([chat_frame({"content": "partial"}), chat_frame({}, finish)])],
        seen,
    )
    done = events[-1]
    assert done.type == "done"
    assert done.data["stopped"] == finish
    assert done.data["finish_reason"] == finish
    assert messages[-1]["message_id"]
    assert {key: value for key, value in messages[-1].items() if key != "message_id"} == {
        "role": "assistant",
        "content": "partial",
        "thinking": None,
        "usage": None,
    }


def test_usage_does_not_leak_from_a_previous_tool_step(workspace: Path):
    """Each model response reports its own usage and no earlier step's counts."""
    seen: list[dict] = []
    agent = build_agent(workspace)
    first = stream_of([
        chat_frame({"tool_calls": [{
            "index": 0,
            "id": "c1",
            "type": "function",
            "function": {"name": "glob", "arguments": '{"pattern":"*"}'},
        }]}),
        chat_frame({}, "tool_calls"),
    ])
    # Usage belongs to the first response only.
    first = first.replace(
        'data: [DONE]\n\n',
        'data: {"id":"chatcmpl-1","choices":[],"usage":{"prompt_tokens":11,'
        '"completion_tokens":7,"total_tokens":18}}\n\ndata: [DONE]\n\n',
    )
    events = run(
        agent,
        [{"role": "user", "content": "list then answer"}],
        [first, text_stream("finished")],
        seen,
    )
    done = events[-1]
    assert done.data["stopped"] == "complete"
    assert done.data["usage"] == {}


def test_usage_does_not_leak_from_a_failed_attempt(workspace: Path, monkeypatch):
    """Usage observed before a retryable failure is not the final response's."""
    attempts = 0

    class Gateway:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, *args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                yield ModelEvent("usage", usage=Usage(input_tokens=11, output_tokens=7))
                yield ModelEvent("error", error=ModelError("transport_error", "failed"))
                return
            yield ModelEvent("text_delta", text="recovered")
            yield ModelEvent("complete", finish_reason="stop")

    import nexus.core.agent as module

    monkeypatch.setattr(module, "ModelGateway", Gateway)
    agent = build_agent(workspace)
    agent.retry = RetryPolicy(max_retries=1, initial_delay=0, max_delay=0, jitter_ratio=0)
    seen: list[dict] = []
    events = run(agent, [{"role": "user", "content": "retry"}], [], seen)
    assert attempts == 2
    assert events[-1].data["stopped"] == "complete"
    assert events[-1].data["usage"] == {}


def test_anthropic_request_shape(workspace: Path):
    from nexus.core.protocol import request_body

    registry, _ = build({})
    anthropic = ModelProfile(
        api_family=APIFamily.ANTHROPIC_MESSAGES,
        model="claude",
        base_url="https://api.anthropic.com/v1",
        context_window_tokens=200000,
        max_output_tokens=4096,
    )
    body = request_body(
        anthropic,
        "system",
        [{"role": "user", "content": "hi"}],
        registry.schema(),
    )
    assert body["system"] == "system"
    assert body["max_tokens"] == 4096
    assert body["tools"][0]["input_schema"]["type"] == "object"
    assert "function" not in body["tools"][0]
