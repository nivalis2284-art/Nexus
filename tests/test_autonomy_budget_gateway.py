"""Per-request autonomy accounting at the real gateway boundary.

The fake transport never reaches a paid endpoint. Usage is still parsed by the
real gateway, so the tests distinguish a completed response from a partial one.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from nexus.core.agent import Agent, AgentSuspended
from nexus.core.permission import default_policy
from nexus.core.workspace import Workspace
from nexus.model import (
    APIFamily,
    ModelCapabilities,
    ModelProfile,
    RuntimeCredentials,
)
from nexus.tools import build
from nexus.tools.registry import TaskSuspension

SSE = "text/event-stream"


def profile(*, max_output: int = 128) -> ModelProfile:
    return ModelProfile(
        api_family=APIFamily.OPENAI_CHAT_COMPLETIONS,
        model="fake",
        base_url="https://fake.test/v1",
        capabilities=ModelCapabilities(tools=True),
        context_window_tokens=4_000,
        max_output_tokens=max_output,
    )


def chat_frame(delta: dict, finish: str | None = None) -> str:
    return "data: " + json.dumps({
        "id": "chatcmpl-1",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }) + "\n\n"


def usage_frame(input_tokens: int = 11, output_tokens: int = 7) -> str:
    return "data: " + json.dumps({
        "id": "chatcmpl-1", "choices": [],
        "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                  "total_tokens": input_tokens + output_tokens},
    }) + "\n\n"


def text_stream(text: str, *, with_usage: bool = False) -> str:
    parts = [chat_frame({"content": text}), chat_frame({}, "stop")]
    if with_usage:
        parts.append(usage_frame())
    return "".join(parts) + "data: [DONE]\n\n"


def call_stream(call_id: str, name: str, arguments: dict) -> str:
    return "".join([
        chat_frame({"tool_calls": [{
            "index": 0, "id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }]}),
        chat_frame({}, "tool_calls"),
        "data: [DONE]\n\n",
    ])


class Budget:
    def __init__(self) -> None:
        self.reservations: list[dict] = []
        self.settlements: list[dict] = []
        self.fail_reserve = False

    def reserve(self, profile: ModelProfile, payload: dict) -> str:
        if self.fail_reserve:
            raise PermissionError("quota exhausted")
        ticket = f"ticket-{len(self.reservations) + 1}"
        self.reservations.append({"ticket": ticket, "profile": profile, "payload": payload})
        return ticket

    def settle(self, ticket: str, usage: object | None) -> None:
        self.settlements.append({"ticket": ticket, "usage": usage})


def install(monkeypatch, responses: list[str | httpx.Response], seen: list[dict]):
    import nexus.core.agent as module

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        response = responses.pop(0)
        if isinstance(response, httpx.Response):
            return response
        return httpx.Response(200, headers={"content-type": SSE}, text=response)

    monkeypatch.setattr(
        module, "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def make_agent(tmp_path, budget: Budget, **kwargs) -> Agent:
    registry, context = build(root=tmp_path)
    context.model_budget = budget
    return Agent(
        profile=profile(), credentials=RuntimeCredentials("test-key"),
        registry=registry, context=context, workspace=Workspace(tmp_path),
        policy=default_policy("yolo"), **kwargs,
    )


def drive(agent: Agent, messages: list[dict]) -> list:
    async def run():
        return [event async for event in agent.run(messages)]
    return asyncio.run(run())


def test_completed_request_settles_with_final_usage(tmp_path, monkeypatch):
    budget, seen = Budget(), []
    install(monkeypatch, [text_stream("ok", with_usage=True)], seen)
    events = drive(make_agent(tmp_path, budget), [{"role": "user", "content": "go"}])

    assert events[-1].type == "done"
    assert len(budget.reservations) == len(budget.settlements) == 1
    assert budget.settlements[0]["usage"].total_tokens == 18
    reserved = budget.reservations[0]
    assert reserved["profile"].max_output_tokens == 128
    assert "messages" in reserved["payload"]


@pytest.mark.parametrize(
    "response",
    [
        chat_frame({"content": "partial"}) + usage_frame(),
        chat_frame({"content": "partial"}) + usage_frame()
        + 'data: {"error":{"message":"upstream failed"}}\n\n',
    ],
    ids=["eof-after-usage", "malformed-after-usage"],
)
def test_partial_stream_keeps_the_reservation_even_after_usage(tmp_path, monkeypatch, response):
    budget, seen = Budget(), []
    install(monkeypatch, [response], seen)
    events = drive(make_agent(tmp_path, budget), [{"role": "user", "content": "go"}])

    assert events[-1].type == "error"
    assert budget.settlements == [{"ticket": "ticket-1", "usage": None}]


def test_retry_reserves_and_settles_each_actual_request(tmp_path, monkeypatch):
    budget, seen = Budget(), []
    failed = httpx.Response(503, headers={"content-type": SSE}, text="")
    install(monkeypatch, [failed, text_stream("recovered", with_usage=True)], seen)
    agent = make_agent(tmp_path, budget)
    agent.retry = type("Retry", (), {
        "max_retries": 1,
        "delay_for": lambda self, attempt, retry_after: 0,
    })()
    events = drive(agent, [{"role": "user", "content": "go"}])

    assert events[-1].data["stopped"] == "complete"
    assert [item["ticket"] for item in budget.settlements] == ["ticket-1", "ticket-2"]
    assert budget.settlements[0]["usage"] is None
    assert budget.settlements[1]["usage"].total_tokens == 18


def test_reserve_failure_sends_no_request(tmp_path, monkeypatch):
    budget, seen = Budget(), []
    budget.fail_reserve = True
    install(monkeypatch, [text_stream("must not send")], seen)

    with pytest.raises(PermissionError, match="quota exhausted"):
        drive(make_agent(tmp_path, budget), [{"role": "user", "content": "go"}])
    assert seen == []
    assert budget.settlements == []


def test_compaction_never_hides_budget_exhaustion(tmp_path, monkeypatch):
    budget, seen = Budget(), []
    install(monkeypatch, [text_stream("summary")], seen)
    agent = make_agent(tmp_path, budget, context_limit=100)
    messages = [{"role": "user", "content": "x" * 500}, {"role": "assistant", "content": "y" * 500}]
    budget.fail_reserve = True

    with pytest.raises(PermissionError, match="quota exhausted"):
        drive(agent, messages)
    assert seen == []


def test_cancelled_stream_is_settled_conservatively(tmp_path, monkeypatch):
    import nexus.core.agent as module

    class Gateway:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, profile, body, *, credentials, effort=None, request_budget=None):
            ticket = request_budget.reserve(profile, body)
            try:
                yield type("Event", (), {"type": "text_delta", "text": "partial",
                                         "tool_call": None, "usage": None,
                                         "finish_reason": None, "error": None})()
                raise asyncio.CancelledError()
            finally:
                request_budget.settle(ticket, None)

    monkeypatch.setattr(module, "ModelGateway", Gateway)
    budget = Budget()
    with pytest.raises(asyncio.CancelledError):
        drive(make_agent(tmp_path, budget), [{"role": "user", "content": "go"}])
    assert budget.settlements == [{"ticket": "ticket-1", "usage": None}]


def test_scope_check_failure_is_visible_before_a_model_request(tmp_path, monkeypatch):
    budget, seen = Budget(), []
    install(monkeypatch, [text_stream("must not send")], seen)

    class Scope:
        model_budget = budget

        def check(self):
            raise TaskSuspension("paused by owner", kind="autonomy_control")

    agent = make_agent(tmp_path, budget)
    agent.context.autonomy_scope = Scope()
    with pytest.raises(AgentSuspended, match="paused by owner"):
        drive(agent, [{"role": "user", "content": "go"}])
    assert seen == []


def test_ordinary_chat_without_budget_uses_no_reservation(tmp_path, monkeypatch):
    seen = []
    install(monkeypatch, [text_stream("ordinary", with_usage=True)], seen)
    registry, context = build(root=tmp_path)
    agent = Agent(
        profile=profile(), credentials=RuntimeCredentials("k"), registry=registry,
        context=context, workspace=Workspace(tmp_path),
    )
    events = drive(agent, [{"role": "user", "content": "hello"}])
    assert events[-1].data["stopped"] == "complete"
    assert getattr(context, "model_budget", None) is None
