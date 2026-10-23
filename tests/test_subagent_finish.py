"""Provider truncation must remain a failure when returned through a child tool."""

import asyncio
from types import SimpleNamespace

import pytest

from nexus.core.subagent import SubagentRequest, run_subagent
from nexus.tools.subagent_tool import spawn


def test_final_report_excludes_updates_before_tool_calls():
    async def run(prompt, budget):
        yield SimpleNamespace(type="delta", text="It looks broken.")
        yield SimpleNamespace(type="tool_start", data={"id": "c1", "name": "read_file"})
        yield SimpleNamespace(type="tool_result", data={"id": "c1", "content": "fixed"})
        yield SimpleNamespace(type="delta", text="Checked: ")
        yield SimpleNamespace(type="delta", text="it works.")
        yield SimpleNamespace(type="done", data={"steps": 2, "stopped": "complete"})

    result = asyncio.run(run_subagent(SubagentRequest(task="check it"), make_runner=run))
    assert result.text == "Checked: it works."
    assert len(result.trace) == 2


@pytest.mark.parametrize("reason", ["length", "max_tokens", "content_filter", "incomplete"])
def test_subagent_preserves_abnormal_provider_stop(reason):
    context = SimpleNamespace(spawn_subagent=lambda *args: SimpleNamespace(
        text="partial result", stopped=reason, steps=1,
    ))
    result = spawn(context, {"task": "finish the work", "max_steps": 12})
    assert result["ok"] is False
    assert "partial result" in result["content"]
    assert f"stopped={reason}" in result["content"]
    assert result["stopped"] == reason


@pytest.mark.parametrize("reason", ["complete", "max_steps"])
def test_subagent_only_marks_a_verified_normal_stop_as_complete(reason):
    context = SimpleNamespace(spawn_subagent=lambda *args: SimpleNamespace(
        text="result", stopped=reason,
    ))
    result = spawn(context, {"task": "work", "max_steps": 12})
    assert result["ok"] is (reason == "complete")
    assert f"stopped={reason}" in result["content"]
