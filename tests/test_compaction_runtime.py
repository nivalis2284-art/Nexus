import asyncio

import pytest

from nexus.core import compaction
from nexus.core.agent import Agent
from nexus.core.effort import output_reserve
from nexus.core.workspace import Workspace
from nexus.model import APIFamily, ModelCapabilities, ModelEvent, ModelProfile, RuntimeCredentials
from nexus.tools import ToolContext
from nexus.tools.registry import Registry


def make_agent(root, **changes):
    return Agent(profile=ModelProfile(api_family=APIFamily.OPENAI_CHAT_COMPLETIONS,
        model="fake", base_url="https://fake.invalid", context_window_tokens=6000,
        max_output_tokens=256), credentials=RuntimeCredentials("fake"),
        registry=Registry(), context=ToolContext(root), workspace=Workspace(root), **changes)


def test_summary_sees_entire_long_message_in_bounded_requests(tmp_path, monkeypatch):
    seen = []

    class Gateway:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, profile, body, **kwargs):
            seen.append(body)
            yield ModelEvent("text_delta", text="Keep the owner's final constraint.")
            yield ModelEvent("complete", finish_reason="stop")

    monkeypatch.setattr("nexus.core.agent.ModelGateway", Gateway)
    agent = make_agent(tmp_path)
    original = [{"role": "user", "content": "x" * 22000 + "TAIL_CONSTRAINT"},
                {"role": "assistant", "content": "old answer"},
                {"role": "user", "content": "continue"}]
    _, report = asyncio.run(agent._fold(original, original, 6000))
    assert report.summarised
    assert any("TAIL_CONSTRAINT" in body["messages"][1]["content"] for body in seen)
    assert all(compaction.estimate(str(body["messages"])) + 256 < 6000 for body in seen)


def test_checkpoint_projection_keeps_newer_corrections_and_raw_history():
    original = [{"role": "user", "content": "old", "_source_id": "0"},
                {"role": "assistant", "content": "old reply", "_source_id": "1"},
                {"role": "user", "content": "latest correction", "_source_id": "2"},
                {"role": "checkpoint", "content": "summary", "through": "1", "_source_id": "3"}]
    result = compaction.project(original)
    assert [x["content"] for x in result] == ["summary", "latest correction"]
    assert len(original) == 4
    second = [*original, {"role": "checkpoint", "content": "new summary", "through": "3", "_source_id": "4"}]
    assert [x["content"] for x in compaction.project(second)] == ["new summary", "latest correction"]


def test_midrun_owner_input_has_the_same_checkpoint_id_after_reload(tmp_path):
    from nexus.ling import LingInbox
    from nexus.session import SessionStore
    from nexus.web.server import _internal

    sessions = SessionStore(tmp_path / "sessions")
    session = sessions.create("fake")
    inbox = object.__new__(LingInbox)
    inbox.state = {"sessions": sessions}
    inbox._applied_ids = []
    history = []
    inbox._append_input(session, {"id": 9, "text": "latest owner constraint", "data_json": "{}"}, history)
    reloaded = _internal(sessions.load(session.id))
    assert history[-1]["_source_id"] == reloaded[-1]["_source_id"]
    assert compaction.project([*reloaded, {"role": "checkpoint", "through": history[-1]["_source_id"],
                                         "content": "saved constraint"}])[0]["content"] == "saved constraint"


def test_stop_closes_a_silent_model_stream(tmp_path, monkeypatch):
    entered, closed = asyncio.Event(), asyncio.Event()

    class Gateway:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, *args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
                yield ModelEvent("text_delta", text="unreachable")
            finally:
                closed.set()

    monkeypatch.setattr("nexus.core.agent.ModelGateway", Gateway)
    agent = make_agent(tmp_path)

    async def drive():
        async def consume():
            return [event async for event in agent.run([{"role": "user", "content": "hello"}])]
        task = asyncio.create_task(consume())
        await entered.wait()
        agent.stop()
        events = await asyncio.wait_for(task, 1)
        assert closed.is_set()
        assert events[-1].data["stopped"] == "cancelled"

    asyncio.run(drive())


@pytest.mark.parametrize("level", [None, "low", "medium", "high", "xhigh", "max"])
def test_output_reserve_matches_effective_request(level):
    from nexus.core.protocol import request_body

    profile = ModelProfile(api_family=APIFamily.ANTHROPIC_MESSAGES,
        model="fake", base_url="https://fake.invalid", max_output_tokens=8192,
        capabilities=ModelCapabilities(thinking=True, effort_levels=("low", "medium", "high", "xhigh", "max")))
    body = request_body(profile, "system", [{"role": "user", "content": "hello"}], [], effort=level)
    assert output_reserve(profile, level) == body["max_tokens"]


def test_oversized_input_fails_without_discarding_goal(tmp_path):
    agent = make_agent(tmp_path)
    messages = [{"role": "user", "content": "GOAL" * 10000}]

    async def drive():
        return [event async for event in agent.run(messages)]

    events = asyncio.run(drive())
    assert events[-1].type == "error"
    assert "Context capacity" in events[-1].text
    assert messages[0]["content"] == "GOAL" * 10000


def test_actual_input_usage_is_enforced_when_compaction_cannot_run(tmp_path, monkeypatch):
    from nexus.model import ToolCall, Usage

    requests = []

    class Gateway:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, profile, body, **kwargs):
            requests.append(body)
            yield ModelEvent("tool_call", tool_call=ToolCall("c1", "missing_tool", "{}", {}))
            yield ModelEvent("complete", finish_reason="tool_calls", usage=Usage(input_tokens=3743))

    monkeypatch.setattr("nexus.core.agent.ModelGateway", Gateway)
    agent = make_agent(tmp_path)
    agent.max_epochs = 0

    async def drive():
        return [event async for event in agent.run([{"role": "user", "content": "keep this goal"}])]

    events = asyncio.run(drive())
    assert events[-1].type == "error"
    assert "Context capacity" in events[-1].text
    assert len(requests) == 1


def test_new_directory_instructions_are_seen_before_first_edit(tmp_path):
    from nexus.core.permission import default_policy
    from nexus.tools import build

    nested = tmp_path / "src"
    nested.mkdir()
    (nested / "AGENTS.md").write_text("Keep LICENSE_TAG in this directory.", encoding="utf-8")
    (nested / "a.txt").write_text("original", encoding="utf-8")
    agent = make_agent(tmp_path, policy=default_policy("yolo"))
    agent.registry, agent.context = build({}, root=tmp_path)
    import json
    call = {"name": "write_file", "arguments": json.dumps({"path": "src/a.txt", "content": "changed"})}
    outcome = asyncio.run(agent._invoke(call))
    assert outcome.error_code == "instructions_updated"
    assert "LICENSE_TAG" in outcome.content
    assert (nested / "a.txt").read_text(encoding="utf-8") == "original"
    assert asyncio.run(agent._invoke(call)).ok


@pytest.mark.parametrize("task_history", [False, True])
def test_checkpoint_survives_reload_without_resummarizing(tmp_path, monkeypatch, task_history):
    from nexus.runtime.conversation import persist
    from nexus.session import Message, SessionStore
    from nexus.task import TaskStore
    from nexus.web.server import _internal

    calls = []

    class Gateway:
        def __init__(self, *args, **kwargs):
            pass

        async def stream(self, profile, body, **kwargs):
            summary = body["messages"][0]["content"] == "You are a compaction engine."
            calls.append(summary)
            yield ModelEvent("text_delta", text="Remember TAIL_CONSTRAINT" if summary else "done")
            yield ModelEvent("complete", finish_reason="stop")

    monkeypatch.setattr("nexus.core.agent.ModelGateway", Gateway)
    if task_history:
        store = TaskStore(tmp_path / "tasks.sqlite3")
        task = store.create(title="test", goal="verify", workspace=str(tmp_path))
        store.add_message(task.id, kind="user", text="x" * 24000 + "TAIL_CONSTRAINT")
        store.add_message(task.id, kind="assistant", text="old answer")
        store.add_message(task.id, kind="user", text="continue")
        load = lambda: store.messages_for_model(task.id)
        save = lambda m: store.save_run_messages(task.id, [m])
    else:
        store = SessionStore(tmp_path / "sessions")
        session = store.create("fake")
        store.append(session, Message(kind="user", text="x" * 24000 + "TAIL_CONSTRAINT"))
        store.append(session, Message(kind="assistant", text="old answer"))
        store.append(session, Message(kind="user", text="continue"))
        load = lambda: _internal(store.load(session.id))
        save = lambda m: persist({"sessions": store}, session, [m])
    for _ in range(2):
        agent = make_agent(tmp_path, on_message=save)
        agent.profile = agent.profile.model_copy(update={"context_window_tokens": 10000})

        async def drive():
            return [event async for event in agent.run(load())]

        assert asyncio.run(drive())[-1].type == "done"
        if _ == 0:
            summaries_after_first = sum(calls)
    assert sum(calls) == summaries_after_first
    assert any(compaction.SUMMARY_TAG_OPEN in str(m.get("content")) for m in load())
    if task_history:
        assert len(store.messages(task.id)[0]["text"]) > 24000
    else:
        assert len(store.load(session.id).items[0].text) > 24000
