"""Unlimited background work still stops at explicit completion or cancellation."""
from test_agent_loop import build_agent, run, text_stream, tool_call_stream
from test_continuity_runtime import call_frames, install_stream, started_task

from nexus.runtime.assembly import TaskAssembler
import threading


def test_unlimited_agent_can_pass_the_old_default_step_limit(tmp_path):
    agent = build_agent(tmp_path)
    agent.max_steps = None
    seen = []
    responses = [tool_call_stream(f"read-{i}", "glob", {"pattern": "*"}) for i in range(42)]
    events = run(agent, [{"role": "user", "content": "inspect"}],
                 responses + [text_stream("done")], seen)
    assert events[-1].data["stopped"] == "complete"
    assert events[-1].data["steps"] == 43
    assert len(seen) == 43


def test_unlimited_agent_obeys_explicit_cancellation(tmp_path):
    agent = build_agent(tmp_path)
    agent.max_steps = None
    agent.on_tool_result = lambda *args: agent.stop()
    seen = []
    events = run(agent, [{"role": "user", "content": "inspect"}],
                 [tool_call_stream("read", "glob", {"pattern": "*"})], seen)
    assert events[-1].data["stopped"] == "cancelled"
    assert len(seen) == 1


def test_background_task_ignores_chat_step_limit_and_finishes_after_40_steps(tmp_path, monkeypatch):
    config, store, task = started_task(tmp_path)
    config.save_preferences(max_steps=1)
    seen = []
    responses = [call_frames(f"read-{i}", "glob", {"pattern": "*"}) for i in range(42)]
    responses.append(call_frames("finish", "task_finish", {"summary": "Inspection complete."}))
    install_stream(monkeypatch, responses, seen)
    result = TaskAssembler(store, config)(store.get(task.id), threading.Event())
    assert result.status == "complete"
    assert store.get(task.id).state == "completed"
    assert len(seen) == 43
