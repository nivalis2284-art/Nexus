"""Fake-provider integration for task execution and durable boundaries."""

from __future__ import annotations

import json
import threading

import httpx

from nexus.core.config import ConfigStore
from nexus.model import APIFamily
from nexus.runtime.assembly import TaskAssembler
from nexus.task import TaskStore


def frame(delta, finish=None):
    return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


def text_frames(text):
    return frame({"content": text}) + frame({}, "stop") + "data: [DONE]\n\n"


def call_frames(call_id, name, arguments):
    return (
        frame({"tool_calls": [{"index": 0, "id": call_id, "type": "function",
               "function": {"name": name, "arguments": json.dumps(arguments)}}]})
        + frame({}, "tool_calls") + "data: [DONE]\n\n"
    )


def configure(tmp_path):
    store = ConfigStore(tmp_path / "home")
    settings = store.ensure()
    provider = settings.provider
    store.update_provider(provider.id, {
        "base_url": "https://fake.test/v1",
        "family": APIFamily.OPENAI_CHAT_COMPLETIONS.value,
        "models": [{"id": "fake", "context_window_tokens": 32000,
                    "max_output_tokens": 1024}],
    })
    store.select(provider.id, "fake", "off")
    store.write_credential(provider.id, "test-key")
    store.save_preferences(workspace=str(tmp_path), permission="yolo")
    return store, provider.id


def test_continue_after_reopen_uses_saved_context(tmp_path, monkeypatch):
    """A new service-style assembler resumes the work checkpoint, not a blank chat."""
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="resume", goal="continue", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    tasks.update_progress(item.id, expected_version=item.progress_version, changes={
        "steps": [{"content": "already done", "status": "completed"}],
        "next_step": "verify the next thing", "work_summary": "prior plan",
    })
    reopened = TaskStore(tasks.path)
    reopened.command(item.id, "running", request_id="resume-start")
    reopened.begin_run(item.id)
    responses = [call_frames("finish", "task_finish", {"summary": "resumed"})]
    seen = []
    import nexus.core.agent as module
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=responses.pop(0))
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    TaskAssembler(reopened, config)(reopened.get(item.id), threading.Event())
    system = seen[0]["messages"][0]["content"]
    assert "verify the next thing" in system
    assert "prior plan" in system
    assert "already done" in system


def test_task_runner_uses_persistent_context_and_does_not_claim_completion(tmp_path, monkeypatch):
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="runtime", goal="write target.txt", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    tasks.command(item.id, "running", request_id="test-running")
    tasks.begin_run(item.id)
    responses = [
        call_frames("call1", "write_file", {"path": "target.txt", "content": "hello"}),
        text_frames("created it"),
        call_frames("pause", "task_pause", {"reason": "The file is ready for review."}),
    ]
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=responses.pop(0))
    import nexus.core.agent as module
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = TaskAssembler(tasks, config)(item, threading.Event())
    assert result.status == "paused"
    assert (tmp_path / "target.txt").read_text() == "hello"
    loaded = tasks.get(item.id)
    assert loaded.state != "completed"
    assert any(m["kind"] == "tool" and m.get("name") == "write_file" for m in tasks.messages(item.id))


def test_question_end_of_task_is_persisted_then_reported_waiting(tmp_path, monkeypatch):
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="wait", goal="ask", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    tasks.command(item.id, "running", request_id="test-running")
    tasks.begin_run(item.id)
    responses = [call_frames("q1", "ask_user", {"questions": [{"question": "choose?"}]})]
    import nexus.core.agent as module
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=responses.pop(0))
    )))
    result = TaskAssembler(tasks, config)(item, threading.Event())
    assert result.status == "waiting_user"
    assert tasks.get(item.id).state == "waiting_user"
    pending = tasks.pending_interactions(item.id)
    assert pending and pending[0]["kind"] == "question"


def test_answering_question_survives_reopen_and_reaches_next_model_run(tmp_path, monkeypatch):
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="recovery", goal="answer once", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    tasks.command(item.id, "running", request_id="first-run")
    tasks.begin_run(item.id)
    first = [call_frames("q1", "ask_user", {"questions": [{"question": "choose?", "options": ["yes"]}]})]
    import nexus.core.agent as module
    def first_handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=first.pop(0))
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(first_handler)))
    assert TaskAssembler(tasks, config)(tasks.get(item.id), threading.Event()).status == "waiting_user"
    pending = tasks.pending_interactions(item.id)[0]
    reopened = TaskStore(tasks.path)
    reopened.interaction_answer(pending["id"], answer="yes", expected_version=None)
    reopened.command(item.id, "running", request_id="resume")
    reopened.begin_run(item.id)
    second = [call_frames("finish", "task_finish", {"summary": "done"})]
    seen = []
    def second_handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=second.pop(0))
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(second_handler)))
    TaskAssembler(reopened, config)(reopened.get(item.id), threading.Event())
    serialized = json.dumps(seen[0], ensure_ascii=False)
    assert "yes" in serialized
    assert not reopened.pending_interactions(item.id)


def test_approval_survives_reopen_and_resumes_exact_action(tmp_path, monkeypatch):
    config, provider_id = configure(tmp_path)
    config.save_preferences(workspace=str(tmp_path), permission="ask")
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="approval", goal="write once", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    tasks.command(item.id, "running", request_id="approval-first")
    tasks.begin_run(item.id)
    first = [call_frames("a1", "write_file", {"path": "approved.txt", "content": "ok"})]
    import nexus.core.agent as module
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=first.pop(0))
    )))
    assert TaskAssembler(tasks, config)(tasks.get(item.id), threading.Event()).status == "waiting_user"
    pending = tasks.pending_interactions(item.id)
    assert pending and pending[0]["kind"] == "approval"
    assert not (tmp_path / "approved.txt").exists()

    reopened = TaskStore(tasks.path)
    reopened.interaction_answer(pending[0]["id"], answer=True,
                                expected_version=reopened.get(item.id).control_version)
    reopened.command(item.id, "running", request_id="approval-resume")
    reopened.begin_run(item.id)
    second = [call_frames("a2", "write_file", {"path": "approved.txt", "content": "ok"}),
              call_frames("finish", "task_finish", {"summary": "done"})]
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=second.pop(0))
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    TaskAssembler(reopened, config)(reopened.get(item.id), threading.Event())
    assert (tmp_path / "approved.txt").read_text() == "ok"
    assert len(seen) == 2
    assert not reopened.pending_interactions(item.id)
    assert not reopened.unfinished_operations(item.id)
    finished = [event for event in reopened.events(item.id) if event["type"] == "operation.finished"]
    assert len(finished) == 1, "one tool execution must commit one result"


def test_unfinished_model_stop_is_a_visible_task_pause(tmp_path, monkeypatch):
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="stopped", goal="finish", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    tasks.command(item.id, "running", request_id="stop-test")
    tasks.begin_run(item.id)
    import nexus.core.agent as module
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                       text=frame({"content": "cut off"}) + frame({}, "length")
                                       + "data: [DONE]\n\n")
    )))
    result = TaskAssembler(tasks, config)(tasks.get(item.id), threading.Event())
    assert result.status == "paused"
    assert result.wait_reason == "length"
    assert any("未正常结束" in message["text"] for message in tasks.messages(item.id))
    assert tasks.get(item.id).state != "completed"


def test_resumed_turn_does_not_duplicate_saved_history(tmp_path, monkeypatch):
    config, provider_id = configure(tmp_path)
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="history", goal="keep history", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    tasks.command(item.id, "running", request_id="history-run")
    tasks.begin_run(item.id)
    responses = [text_frames("first answer"),
                 call_frames("pause", "task_pause", {"reason": "Wait for the owner's review."}),
                 text_frames("second answer"),
                 call_frames("finish", "task_finish", {"summary": "Finished after review."})]
    import nexus.core.agent as module
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                       text=responses.pop(0))
    )))
    TaskAssembler(tasks, config)(tasks.get(item.id), threading.Event())
    reopened = TaskStore(tasks.path)
    reopened.command(item.id, "running", request_id="explicit-resume")
    reopened.begin_run(item.id)
    TaskAssembler(reopened, config)(reopened.get(item.id), threading.Event())
    messages = reopened.messages(item.id)
    assert [message["text"] for message in messages if message["kind"] == "assistant" and message["text"]] == [
        "first answer", "second answer",
    ]
    assert len([message for message in messages if message["kind"] == "user"]) == 1


def test_second_approval_wait_keeps_background_registry(tmp_path, monkeypatch):
    config, provider_id = configure(tmp_path)
    config.save_preferences(permission="ask")
    tasks = TaskStore(tmp_path / "tasks.sqlite3")
    item = tasks.create(title="twice", goal="writes", workspace=str(tmp_path),
                        provider_id=provider_id, model_id="fake")
    assembler = TaskAssembler(tasks, config)
    import nexus.core.agent as module
    responses = []
    monkeypatch.setattr(module, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                       text=responses.pop(0)))))
    registry = None
    try:
        for index in range(2):
            tasks.command(item.id, "running", request_id=f"run-{index}")
            tasks.begin_run(item.id)
            responses.append(call_frames(f"approval-{index}", "write_file",
                                         {"path": f"file-{index}.txt", "content": "ok"}))
            result = assembler(tasks.get(item.id), threading.Event())
            assert result.status == "waiting_user"
            assert tasks.get(item.id).state == "waiting_user"
            if registry is None:
                registry = assembler._jobs[item.id]
            else:
                assert assembler._jobs[item.id] is registry
            pending = tasks.pending_interactions(item.id)
            assert len(pending) == 1
            tasks.interaction_answer(pending[0]["id"], answer=True,
                                     expected_version=tasks.get(item.id).control_version)
    finally:
        if registry is not None:
            registry.close_all()
