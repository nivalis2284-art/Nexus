"""Persona injection through Ling, wakes, isolated runs and compaction."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from nexus.core import agent as loop
from nexus.core import compaction
from nexus.persona import PersonaStore, render_persona
from nexus.runtime.assembly import TaskAssembler
from nexus.task import TaskStore
from nexus.web import server as web
from test_web_runtime import model, stream
from test_web_runtime import runtime as runtime


CORE = "PERSONA-CORE: I am Ling, with a steady and candid voice."
SECTION = "PERSONA-SECTION: lead with the useful point."
DESCRIPTION = "PERSONA-DESCRIPTION: entry summary used in the request."
BODY = "PERSONA-BODY: detailed background available only on demand."
DISABLED = "PERSONA-DISABLED-DESCRIPTION: this should stay out of context."


def persona_document(
    *,
    core: str = CORE,
    section: str = SECTION,
    description: str = DESCRIPTION,
    body: str = BODY,
    enabled: bool = True,
) -> dict:
    return {
        "sections": [
            {"id": "core", "title": "核心设定", "content": core},
            {"id": "principles", "title": "回复原则", "content": section},
        ],
        "entries": [
            {
                "id": "voice",
                "name": "voice",
                "description": description,
                "body": body,
                "enabled": enabled,
            },
            {
                "id": "archived",
                "name": "archived",
                "description": DISABLED,
                "body": "PERSONA-DISABLED-BODY: never resident.",
                "enabled": False,
            },
        ],
    }


def save_persona(store, **changes) -> dict:
    return PersonaStore(store.home).save(persona_document(**changes))


def tool_names(request: dict) -> set[str]:
    return {
        item["function"]["name"]
        for item in request.get("tools") or []
    }


def system_of(request: dict) -> str:
    return request["messages"][0]["content"]


@pytest.fixture
def ling_runtime(runtime):
    handler, session, store = runtime
    tasks = TaskStore(store.home / "tasks.sqlite3")
    web.STATE["tasks"] = tasks
    web.STATE["task_controller"] = SimpleNamespace(active_task=None, queued_ids=list)
    web.STATE["automation"] = SimpleNamespace(
        context=lambda run: "wake material: inspect the completed work",
        agent=None,
    )
    ling = SimpleNamespace(
        current_input=1,
        _condition=threading.Condition(),
        live={},
    )
    try:
        yield handler, session, store, ling
    finally:
        close = getattr(tasks, "close", None)
        if close is not None:
            close()


def test_ling_and_wake_start_with_sections_and_descriptions_only(
    ling_runtime, monkeypatch
):
    handler, session, store, ling = ling_runtime
    document = save_persona(store)
    preview = render_persona(document)
    seen = model(monkeypatch, [stream(text="chat reply"), stream(text="wake reply")])

    handler._run_turn(session, store.load(), lambda event: None, ling=ling)
    chat = seen[0]
    chat_system = system_of(chat)
    assert chat_system.startswith(preview)
    assert chat_system.index(CORE) < chat_system.index("You are Ling")
    assert SECTION in chat_system
    assert DESCRIPTION in chat_system
    assert DISABLED not in chat_system
    assert BODY not in chat_system
    assert "persona_manage" in tool_names(chat)
    snapshot = PersonaStore(store.home).snapshot()
    assert snapshot["last_request"]["content"] == preview
    assert snapshot["last_request"]["source"] == "chat"
    assert snapshot["last_request"]["model"] == "fake"

    handler._run_turn(
        session,
        store.load(),
        lambda event: None,
        ling=ling,
        wake={"id": "wake-1"},
    )
    wake = seen[1]
    wake_system = system_of(wake)
    assert wake_system.startswith(preview)
    assert "wake material: inspect the completed work" in wake_system
    assert BODY not in wake_system
    names = tool_names(wake)
    assert "persona_manage" not in names
    assert "automation_finish" in names
    snapshot = PersonaStore(store.home).snapshot()
    assert snapshot["last_request"]["content"] == preview
    assert snapshot["last_request"]["source"] == "wake"


def test_ordinary_chat_and_child_do_not_use_persona_or_replace_snapshot(
    ling_runtime, monkeypatch
):
    handler, session, store, ling = ling_runtime
    document = save_persona(store)
    seen = model(
        monkeypatch,
        [
            stream(text="ling reply"),
            stream(calls=[("subagent", {"task": "inspect without persona"})]),
            stream(text="child finished"),
            stream(text="ordinary parent finished"),
        ],
    )

    handler._run_turn(session, store.load(), lambda event: None, ling=ling)
    before = PersonaStore(store.home).snapshot()["last_request"]
    assert before["content"] == render_persona(document)

    handler._run_turn(session, store.load(), lambda event: None)
    ordinary, child = seen[1], seen[2]
    assert CORE not in system_of(ordinary)
    assert DESCRIPTION not in system_of(ordinary)
    assert "persona_manage" not in tool_names(ordinary)
    assert CORE not in system_of(child)
    assert DESCRIPTION not in system_of(child)
    assert BODY not in system_of(child)
    assert "persona_manage" not in tool_names(child)
    assert "You are Ling" not in system_of(child)
    assert PersonaStore(store.home).snapshot()["last_request"] == before


def test_independent_task_does_not_use_persona_or_replace_snapshot(
    ling_runtime, monkeypatch
):
    handler, session, store, ling = ling_runtime
    document = save_persona(store)
    seen = model(
        monkeypatch,
        [
            stream(text="ling reply"),
            stream(calls=[("task_finish", {"summary": "task done"})]),
        ],
    )
    handler._run_turn(session, store.load(), lambda event: None, ling=ling)
    before = PersonaStore(store.home).snapshot()["last_request"]
    assert before["content"] == render_persona(document)

    tasks = web.STATE["tasks"]
    settings = store.load()
    task = tasks.create(
        title="independent persona isolation",
        goal="finish the independent task",
        workspace=settings.workspace,
        provider_id=settings.provider.id,
        model_id="fake",
    )
    tasks.command(task.id, "running", request_id="persona-isolation-running")
    tasks.begin_run(task.id)
    TaskAssembler(tasks, store)(tasks.get(task.id), threading.Event())

    request = seen[1]
    assert CORE not in system_of(request)
    assert DESCRIPTION not in system_of(request)
    assert BODY not in system_of(request)
    assert "persona_manage" not in tool_names(request)
    assert "task_finish" in tool_names(request)
    assert PersonaStore(store.home).snapshot()["last_request"] == before


def test_external_persona_save_applies_to_the_next_request(ling_runtime, monkeypatch):
    handler, session, store, ling = ling_runtime
    save_persona(store)
    seen = model(monkeypatch, [stream(text="old persona"), stream(text="new persona")])

    handler._run_turn(session, store.load(), lambda event: None, ling=ling)
    assert DESCRIPTION in system_of(seen[0])
    old = PersonaStore(store.home).snapshot()["last_request"]

    new_core = "PERSONA-CORE-NEW: use the corrected voice."
    new_description = "PERSONA-DESCRIPTION-NEW: state the correction first."
    new_body = "PERSONA-BODY-NEW: detailed corrected background."
    document = save_persona(
        store, core=new_core, description=new_description, body=new_body
    )
    handler._run_turn(session, store.load(), lambda event: None, ling=ling)

    system = system_of(seen[1])
    assert system.startswith(render_persona(document))
    assert new_core in system
    assert new_description in system
    assert new_body not in system
    assert CORE not in system
    assert DESCRIPTION not in system
    current = PersonaStore(store.home).snapshot()["last_request"]
    assert current["content"] == render_persona(document)
    assert current["time"] >= old["time"]
    assert current != old


def test_persona_manage_sections_and_entries_reach_the_next_request(
    ling_runtime, monkeypatch
):
    handler, session, store, ling = ling_runtime
    save_persona(store)
    new_description = "PERSONA-DESCRIPTION-MANAGED: the owner approved this correction."
    new_body = "PERSONA-BODY-MANAGED: newly saved detail from persona_manage."
    new_section = "PERSONA-SECTION-MANAGED: use this section in the next request."
    seen = model(
        monkeypatch,
        [
            stream(
                calls=[
                    (
                        "persona_manage",
                        {
                            "action": "save_section",
                            "section": {"title": "新栏目", "content": new_section},
                        },
                    ),
                    (
                        "persona_manage",
                        {
                            "action": "save_entry",
                            "entry": {
                                "id": "voice",
                                "description": new_description,
                                "body": new_body,
                            },
                        },
                    ),
                    (
                        "persona_manage",
                        {"action": "move_section", "id": "principles", "offset": -1},
                    ),
                ]
            ),
            stream(text="saved the requested changes"),
            stream(
                calls=[
                    (
                        "read_skill",
                        {"name": "voice", "category": "persona"},
                    ),
                    (
                        "persona_manage",
                        {
                            "action": "delete_entry",
                            "id": "archived",
                        },
                    ),
                    (
                        "persona_manage",
                        {
                            "action": "delete_section",
                            "id": "core",
                        },
                    ),
                ]
            ),
            stream(text="read and cleaned up"),
        ],
    )

    first_events = []
    handler._run_turn(session, store.load(), first_events.append, ling=ling)
    assert DESCRIPTION in system_of(seen[0])
    assert new_description not in system_of(seen[0])
    assert new_description in system_of(seen[1])
    assert new_body not in system_of(seen[1])
    assert new_section in system_of(seen[1])
    assert any(
        event["type"] == "tool_result"
        and event["tool"]["name"] == "persona_manage"
        and event["tool"]["ok"]
        for event in first_events
    )
    current = PersonaStore(store.home).load()
    assert [item["id"] for item in current["sections"]] == ["principles", "core", current["sections"][-1]["id"]]
    assert current["sections"][-1]["content"] == new_section

    second_events = []
    handler._run_turn(session, store.load(), second_events.append, ling=ling)
    results = [
        event["tool"]
        for event in second_events
        if event["type"] == "tool_result"
        and event["tool"]["name"] == "read_skill"
    ]
    assert results and results[0]["ok"]
    assert results[0]["content"] == new_body
    assert new_description in system_of(seen[2])
    assert new_body not in system_of(seen[2])
    assert new_section in system_of(seen[2])
    after = PersonaStore(store.home).load()
    assert "core" not in [item["id"] for item in after["sections"]]
    assert "archived" not in [item["id"] for item in after["entries"]]


def test_compaction_keeps_persona_in_the_next_request(ling_runtime, monkeypatch):
    handler, session, store, ling = ling_runtime
    document = save_persona(store)
    preview = render_persona(document)
    for index in range(10):
        web.STATE["sessions"].append(
            session,
            web.Message(kind="user", text=f"old-{index} " + "x" * 1500),
        )
    settings = store.load()
    settings.context_limit = 2000

    async def fold(self, original, trimmed, window):
        return (
            [{"role": "user", "content": "PERSONA-COMPACTION-CHECKPOINT"}],
            compaction.CompactionReport(summarised=True, epochs=1),
        )

    monkeypatch.setattr(loop.Agent, "_fold", fold)
    seen = model(monkeypatch, [stream(text="after compaction")])
    events = []
    handler._run_turn(session, settings, events.append, ling=ling)

    assert "compacted" in [event["type"] for event in events]
    system = system_of(seen[0])
    assert system.startswith(preview)
    assert CORE in system
    assert DESCRIPTION in system
    assert BODY not in system
    assert PersonaStore(store.home).snapshot()["last_request"]["content"] == preview
