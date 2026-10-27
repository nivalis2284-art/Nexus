"""Work model binding: next-request switching, task ownership, and image limits."""

from __future__ import annotations

import io
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from nexus.attachments import AttachmentStore
from nexus.core import agent as loop
from nexus.core.config import ConfigStore
from nexus.ling import WorkService, available_work_models, select_work_model
from nexus.model import APIFamily
from nexus.runtime import TaskController
from nexus.runtime.assembly import TaskAssembler
from nexus.runtime.conversation import run_turn
from nexus.runtime.decisions import MainDecisions
from nexus.session import Message, SessionStore
from nexus.task import TaskConflict, TaskStore, TaskValidationError
from nexus.tools import build


def frame(delta, finish=None):
    return "data: " + json.dumps(
        {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    ) + "\n\n"


def say(text):
    return frame({"content": text}) + frame({}, "stop") + "data: [DONE]\n\n"


def call(call_id, name, arguments):
    return (
        frame({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
               "function": {"name": name, "arguments": json.dumps(arguments)}}]})
        + frame({}, "tool_calls") + "data: [DONE]\n\n"
    )


def setup(tmp_path):
    config = ConfigStore(tmp_path / "home")
    settings = config.ensure()
    provider = settings.provider
    config.update_provider(provider.id, {
        "base_url": "https://fake.test/v1",
        "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
        "models": [
            {"id": "text", "context_window_tokens": 32000, "max_output_tokens": 1024,
             "capabilities": {"images": False}},
            {"id": "vision", "context_window_tokens": 32000, "max_output_tokens": 1024,
             "capabilities": {"images": True}},
        ],
    })
    config.select(provider.id, "text", "low")
    config.write_credential(provider.id, "fake-key")
    config.save_preferences(workspace=str(tmp_path), permission="yolo")
    (config.home / "owner-instructions.txt").write_text(
        "OWNER-MODEL-INSTRUCTION: answer from evidence.", encoding="utf-8"
    )
    tasks = TaskStore(config.home / "tasks.sqlite3")
    return config, tasks, provider.id


def install(monkeypatch, responses, seen):
    def reply(request):
        seen.append(json.loads(request.content))
        assert responses, "unexpected extra model request"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=responses.pop(0)
        )
    monkeypatch.setattr(
        loop, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply))
    )


def started(tasks, config, provider_id, text):
    task = tasks.create(
        title="model work", goal=text, workspace=str(config.home),
        provider_id=provider_id, model_id="text", effort="low",
    )
    tasks.add_message(task.id, kind="user", text=text)
    tasks.command(task.id, "running", request_id="running-" + task.id)
    tasks.begin_run(task.id)
    return task


def test_model_switch_applies_after_the_current_request_and_not_to_other_tasks(
    tmp_path, monkeypatch,
):
    config, tasks, provider_id = setup(tmp_path)
    mine = started(tasks, config, provider_id, "first")
    neighbor = tasks.create(
        title="neighbor", goal="neighbor", workspace=str(tmp_path),
        provider_id=provider_id, model_id="vision", effort="max",
    )
    neighbor_before = neighbor.to_dict()
    seen = []

    def reply(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            switched = tasks.set_model(
                mine.id, provider_id=provider_id, model_id="vision", effort="high"
            )
            assert switched.state == "running"
        assert tasks.get(neighbor.id).to_dict() == neighbor_before
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            text=call("pause", "task_pause", {"reason": "boundary checked"}),
        )

    monkeypatch.setattr(
        loop, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply))
    )
    outcome = TaskAssembler(tasks, config)(tasks.get(mine.id), threading.Event())
    assert outcome.status == "paused"
    assert [(item["model"], item.get("reasoning_effort")) for item in seen] == [
        ("text", "low"),
    ]
    assert (tasks.get(mine.id).model_id, tasks.get(mine.id).effort) == ("vision", "high")
    assert tasks.get(neighbor.id).to_dict() == neighbor_before


def test_second_run_uses_switch_and_keeps_tool_pair_and_effect_once(tmp_path, monkeypatch):
    config, tasks, provider_id = setup(tmp_path)
    task = started(tasks, config, provider_id, "effect once")
    seen = []

    def reply(request):
        seen.append(json.loads(request.content))
        if len(seen) == 1:
            tasks.set_model(task.id, provider_id=provider_id,
                            model_id="vision", effort="xhigh")
        if len(seen) == 1:
            text = call("b1", "bash", {"command": "python -c \"print('one')\""})
        elif len(seen) == 2:
            text = call("pause", "task_pause", {"reason": "first boundary"})
        else:
            text = call("finish", "task_finish", {"summary": "finished"})
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=text
        )

    monkeypatch.setattr(
        loop, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply))
    )
    TaskAssembler(tasks, config)(tasks.get(task.id), threading.Event())
    assert tasks.get(task.id).state == "paused"
    tasks.command(task.id, "running", request_id="next-run")
    final = TaskAssembler(tasks, config)(tasks.get(task.id), threading.Event())
    assert final.status == "complete"
    assert [item["model"] for item in seen] == ["text", "vision", "vision"]
    from nexus.task import TaskStore as Store
    history = Store(tasks.path).messages_for_model(task.id)
    tool_messages = [item for item in history if item.get("role") == "tool"]
    assert [item["tool_call_id"] for item in tool_messages] == ["b1", "pause", "finish"]
    assert sum(item.get("tool_call_id") == "b1" for item in history) == 1
    from nexus.core.compaction import assert_paired
    assert_paired(history)
    with tasks._connect() as db:
        operations = db.execute(
            "SELECT tool_name,status FROM operations WHERE task_id=? ORDER BY created_at",
            (task.id,),
        ).fetchall()
    assert [(row["tool_name"], row["status"]) for row in operations] == [
        ("bash", "succeeded"),
    ]


def test_work_model_tool_changes_only_own_task(tmp_path):
    config, tasks, provider_id = setup(tmp_path)
    mine = tasks.create(
        title="mine", goal="mine", workspace=str(tmp_path),
        provider_id=provider_id, model_id="text", effort="low",
    )
    neighbor = tasks.create(
        title="neighbor", goal="neighbor", workspace=str(tmp_path),
        provider_id=provider_id, model_id="text", effort="max",
    )
    expected = neighbor.to_dict()
    state = {"store": config, "tasks": tasks, "task_controller": TaskController(tasks)}
    context = SimpleNamespace(
        work_model=lambda **values: select_work_model(state, mine.id, **values).to_dict(),
        work_models=lambda: available_work_models(state),
    )
    registry, _ = build(root=tmp_path)
    from nexus.tools.work import WORK_MODEL_TOOL, WORK_MODELS_TOOL
    registry.add(WORK_MODEL_TOOL)
    registry.add(WORK_MODELS_TOOL)
    result = registry.execute(
        "work_model",
        {"provider_id": provider_id, "model_id": "vision", "effort": "xhigh"},
        context,
    )
    assert result.ok, result.content
    assert json.loads(result.content)["task_id"] == mine.id
    assert (tasks.get(mine.id).model_id, tasks.get(mine.id).effort) == ("vision", "xhigh")
    assert tasks.get(neighbor.id).to_dict() == expected
    listed = json.loads(registry.execute("work_models", {}, context).content)
    assert {item["model_id"] for item in listed} == {"text", "vision"}


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02"
    b"\x00\x00\x00\x0bIDATx\xda\x63\xf8\xcf\xc0\xf0\x1f\x00"
    b"\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_work_start_honors_provider_model_effort_and_read_only(tmp_path):
    config, tasks, provider_id = setup(tmp_path)
    sessions = SessionStore(config.sessions_dir)
    session = sessions.create("fake")
    controller = TaskController(tasks)
    state = {"store": config, "tasks": tasks, "sessions": sessions,
             "task_controller": controller}
    attachments = AttachmentStore(config.home)
    image = attachments.save(
        "sessions", session.id, "pixel.png", "image/png", io.BytesIO(PNG), len(PNG)
    )
    sessions.append(session, Message(
        kind="user", text="image task", data={"attachments": [image]}
    ))
    work = WorkService(state, session, config.load())
    accepted = work.start(
        "observe only", read_only=True, provider_id=provider_id, model_id="vision",
        effort="high", context="owner context",
    )
    stored = tasks.get(accepted["id"])
    assert stored.read_only is True
    assert (stored.provider_id, stored.model_id, stored.effort) == (
        provider_id, "vision", "high",
    )
    message = tasks.messages(stored.id)[0]
    assert "owner context" in message["text"]
    assert any(item["mime"] == "image/png" for item in message["attachments"])


def test_work_start_rejects_text_only_model_for_required_images(tmp_path):
    config, tasks, provider_id = setup(tmp_path)
    sessions = SessionStore(config.sessions_dir)
    session = sessions.create("fake")
    controller = TaskController(tasks)
    state = {"store": config, "tasks": tasks, "sessions": sessions,
             "task_controller": controller}
    image = AttachmentStore(config.home).save(
        "sessions", session.id, "pixel.png", "image/png", io.BytesIO(PNG), len(PNG)
    )
    sessions.append(session, Message(
        kind="user", text="image task", data={"attachments": [image]}
    ))
    work = WorkService(state, session, config.load())
    before = [task.id for task in tasks.list()]
    with pytest.raises(TaskValidationError, match="图片"):
        work.start("use this image", provider_id=provider_id, model_id="text")
    assert [task.id for task in tasks.list()] == before


def test_existing_image_task_can_switch_without_losing_originals(tmp_path):
    config, tasks, provider_id = setup(tmp_path)
    task = tasks.create(
        title="images", goal="images", workspace=str(tmp_path),
        provider_id=provider_id, model_id="vision",
    )
    image = AttachmentStore(config.home).save(
        "tasks", task.id, "pixel.png", "image/png", io.BytesIO(PNG), len(PNG),
    )
    tasks.add_message(task.id, kind="user", text="look", data={"attachments": [image]})
    select_work_model({"store": config, "tasks": tasks}, task.id, provider_id, "text")
    assert tasks.get(task.id).model_id == "text"
    history = tasks.messages_for_model(task.id, include_images=False)
    assert image["name"] in history[0]["content"]
    assert tasks.messages(task.id)[0]["attachments"] == [image]
    assert AttachmentStore(config.home).get("tasks", task.id, image["id"])


def test_owner_instructions_reach_task_prompt(tmp_path, monkeypatch):
    config, tasks, provider_id = setup(tmp_path)
    task = tasks.create(
        title="task", goal="read saved facts", workspace=str(tmp_path),
        provider_id=provider_id, model_id="text",
    )
    tasks.command(task.id, "running", request_id="owner-task-running")
    tasks.begin_run(task.id)
    seen = []
    install(monkeypatch, [
        call("pause", "task_pause", {"reason": "owner checked"}),
    ], seen)
    TaskAssembler(tasks, config)(tasks.get(task.id), threading.Event())
    assert "OWNER-MODEL-INSTRUCTION" in seen[0]["messages"][0]["content"]

    sessions = SessionStore(config.sessions_dir)
    session = sessions.create("text")
    sessions.append(session, Message(kind="user", text="owner main request"))
    controller = TaskController(tasks)
    ling = SimpleNamespace(
        current_input=0, decisions=MainDecisions(tasks, session.id),
        apply_pending=lambda *args: None, _condition=threading.Condition(),
        live={}, agent=None, cancel_event=None, result_status="",
        stop_through_input=0, current_report=False,
    )
    main_seen = []
    install(monkeypatch, [say("main done")], main_seen)
    run_turn(
        {"store": config, "tasks": tasks, "sessions": sessions,
         "task_controller": controller, "skills": {}, "jobs": {}, "closing": False},
        session, config.load(), lambda event: None, ling=ling,
        history=[{"role": "user", "content": "owner main request"}],
    )
    assert "OWNER-MODEL-INSTRUCTION" in main_seen[0]["messages"][0]["content"]
    controller.close()

from nexus.web.task_api import TaskHTTP
def test_stale_model_update_does_not_change_binding(tmp_path):
    config, tasks, provider = setup(tmp_path)
    task = tasks.create(title="work", goal="work", workspace=str(tmp_path), provider_id=provider, model_id="text")
    api = TaskHTTP({"store": config, "tasks": tasks}, lambda *args: None)
    with pytest.raises(TaskConflict):
        api.update(None, task.id, {"expected_version": 0, "model_id": "vision"})
    assert tasks.get(task.id).model_id == "text"

def test_invalid_steps_do_not_partially_change_model(tmp_path):
    config, tasks, provider = setup(tmp_path)
    task = tasks.create(title="work", goal="work", workspace=str(tmp_path), provider_id=provider, model_id="text")
    api = TaskHTTP({"store": config, "tasks": tasks}, lambda *args: None)
    with pytest.raises(TaskValidationError):
        api.update(None, task.id, {"expected_version": task.control_version, "model_id": "vision", "steps": [{"content": "invalid", "status": "wrong"}]})
    assert tasks.get(task.id).model_id == "text"
    assert tasks.get(task.id).control_version == task.control_version
