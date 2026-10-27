import json
import threading
import time

import httpx
import pytest

from nexus.runtime.assembly import TaskAssembler
from nexus.runtime.controller import TaskController
from nexus.runtime.subagents import SubagentService, WAITING
from nexus.task import TaskConflict, TaskStore
from test_task_runtime import configure, call_frames


def until(fn, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError("condition not reached")


def runtime(tmp_path):
    config, provider = configure(tmp_path)
    store = TaskStore(tmp_path / "tasks.sqlite3")
    runner = TaskAssembler(store, config)
    controller = TaskController(store, run=runner)
    runner.controller = controller
    controller.start()
    parent = store.create(title="parent", goal="parent", workspace=str(tmp_path),
                          provider_id=provider, model_id="fake")
    return config, store, controller, parent


def install(monkeypatch, handler):
    from nexus.core import agent
    real = httpx.AsyncClient

    def reply(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=handler(json.loads(request.content)))

    monkeypatch.setattr(agent, "_client", lambda: real(transport=httpx.MockTransport(reply)))


def test_parent_and_two_children_overlap_and_writers_do_not_deadlock(tmp_path, monkeypatch):
    config, store, controller, parent = runtime(tmp_path)
    calls = {}
    lock = threading.Lock()
    both = threading.Event()
    started = set()

    def provider(body):
        system = body["messages"][0]["content"]
        name = "parent" if "Task: parent\n" in system else "child-a" if "Task: child-a\n" in system else "child-b"
        with lock:
            calls[name] = calls.get(name, 0) + 1
            count = calls[name]
        if name == "parent":
            if count == 1:
                return call_frames("spawn-a", "subagent", {"task": "child-a"})
            if count == 2:
                return call_frames("spawn-b", "subagent", {"task": "child-b"})
            if not all(c["state"] == "completed" for c in store.children(parent.id)):
                return call_frames("wait", "subagent_wait", {})
            return call_frames("parent-finish", "task_finish", {"summary": "Combined both actual child results"})
        if count == 1:
            with lock:
                started.add(name)
                if len(started) == 2:
                    both.set()
            assert both.wait(4), "second child could not enter while parent was active"
            return call_frames(name + "-write", "write_file", {"path": name + ".txt", "content": name})
        return call_frames(name + "-finish", "task_finish", {"summary": name + " complete"})

    install(monkeypatch, provider)
    try:
        controller.submit(parent.id, request_id="start")
        until(lambda: store.get(parent.id).state == "completed")
        assert both.is_set()
        assert len(store.children(parent.id)) == 2
        for name in ("child-a", "child-b"):
            assert (tmp_path / (name + ".txt")).read_text() == name
        results = [m for m in store.messages(parent.id) if "Subagent observation" in m["text"]]
        assert len(results) == 2
        assert not controller.owns_write(parent.id)
    finally:
        controller.close()


def test_child_waiting_for_approval_does_not_block_sibling(tmp_path, monkeypatch):
    config, store, controller, parent = runtime(tmp_path)
    config.save_preferences(permission="ask")

    def provider(body):
        system = body["messages"][0]["content"]
        if "Task: needs approval\n" in system:
            if not (tmp_path / "approved.txt").exists():
                return call_frames("write", "write_file", {"path": "approved.txt", "content": "once"})
            return call_frames("done", "task_finish", {"summary": "approved result"})
        return call_frames("done", "task_finish", {"summary": "sibling done"})

    install(monkeypatch, provider)
    service = SubagentService(store, controller, config, parent.id, "task")
    try:
        first = service.start("needs approval")["id"]
        second = service.start("sibling")["id"]
        until(lambda: store.get(first).state == "waiting_user")
        until(lambda: store.get(second).state == "completed")
        request = store.pending_interactions(first)[0]
        assert store.interaction(request["id"])["task_id"] == first
        assert not store.pending_interactions(parent.id)
        controller.answer(first, request["id"], answer=True, expected_version=store.get(first).control_version)
        controller.resume(first, request_id="approved")
        until(lambda: store.get(first).state == "completed")
        assert (tmp_path / "approved.txt").read_text() == "once"
        writes = [x for x in store.messages(first) if x.get("kind") == "tool" and x.get("name") == "write_file" and x.get("ok")]
        assert len(writes) == 1
    finally:
        controller.close()


def test_child_messages_models_fork_cancel_and_recovery(tmp_path):
    config, store, controller, parent = runtime(tmp_path)
    # Keep the queue unstarted while inspecting admission and persisted facts.
    controller.close()
    controller = TaskController(store, run=lambda *_: "paused")
    history = [{"role": "user", "content": "shared background"}]
    service = SubagentService(store, controller, config, parent.id, "task", history=lambda: history)
    child = service.start("scope", context_mode="fork", effort="high")["id"]
    service.message(child, "amend only this child")
    assert store.get(child).effort == "high"
    assert any(m["text"] == "shared background" for m in store.messages(child))
    assert any(m["text"] == "amend only this child" for m in store.messages(child))
    assert not store.messages(parent.id)
    service.model(child, parent.provider_id, "fake", "max")
    assert store.get(child).effort == "max"
    other = SubagentService(store, controller, config, "other-session", "session")
    with pytest.raises(TaskConflict):
        other.read(child)
    controller.cancel(parent.id, request_id="cancel-parent")
    assert store.get(child).state == "cancelled"
    reopened = TaskStore(store.path)
    assert reopened.child_relation(child)["parent_id"] == parent.id
    assert reopened.messages(child)
    controller.close()


def test_fork_keeps_image_blocks_and_does_not_share_mutable_history(tmp_path):
    config, store, controller, parent = runtime(tmp_path)
    controller.close()
    controller = TaskController(store, run=lambda *_: "paused")
    blocks = [{"type": "text", "text": "visual context"},
              {"type": "image", "mime": "image/png", "data": "original-bytes"}]
    history = [{"role": "user", "content": blocks}]
    service = SubagentService(store, controller, config, parent.id, "task", history=lambda: history)
    child = service.start("inspect context", context_mode="fork")["id"]
    blocks[0]["text"] = "changed parent"
    projected = store.messages_for_model(child, include_images=True)
    assert projected[0]["content"][0]["text"] == "visual context"
    assert projected[0]["content"][1]["data"] == "original-bytes"
    controller.cancel(child, request_id="cleanup")
    controller.close()


def test_restart_keeps_children_paused_and_never_replays_results(tmp_path):
    from nexus.runtime.subagents import collect_children

    config, store, controller, parent = runtime(tmp_path)
    controller.close()
    controller = TaskController(store, run=lambda *_: "paused")
    service = SubagentService(store, controller, config, parent.id, "task")
    child = service.start("check saved context")["id"]
    store.command(child, "running", request_id="orphan-running")
    run = store.begin_run(child)
    store.operation_begin(run_id=run["id"], tool_call_id="unknown-write", tool_name="write_file", arguments={})
    restarted = TaskStore(store.path)
    assert restarted.recover_interrupted() == [child]
    assert restarted.get(child).state == "paused"
    assert restarted.unfinished_operations(child)[0]["status"] == "unknown"
    assert restarted.child_relation(child)["parent_id"] == parent.id
    controller.cancel(parent.id, request_id="owner-cancel")
    collect_children(restarted, controller, parent.id)
    collect_children(restarted, controller, parent.id)
    assert restarted.get(parent.id).state == "cancelled"
    assert len(restarted.messages(parent.id)) == 1
    assert not controller.queued_ids()
    controller.close()


def test_pause_all_also_stops_a_parent_waiting_for_children(tmp_path):
    from nexus.runtime.subagents import collect_children

    config, store, controller, parent = runtime(tmp_path)
    controller.close()
    controller = TaskController(store, run=lambda *_: "paused")
    service = SubagentService(store, controller, config, parent.id, "task")
    child = service.start("unfinished child")["id"]
    store.command(parent.id, "running", request_id="parent-running")
    store.command(parent.id, "paused", request_id="parent-wait", reason=WAITING, source="agent")
    controller.pause_all()
    collect_children(store, controller, parent.id)
    assert store.get(parent.id).state == "paused"
    assert store.get(parent.id).wait_reason != WAITING
    assert store.get(child).state == "paused"
    assert not controller.queued_ids()
    controller.close()
