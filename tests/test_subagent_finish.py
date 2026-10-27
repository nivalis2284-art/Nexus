"""Provider truncation must remain a failure when returned through a child tool."""

import asyncio
from types import SimpleNamespace

import pytest

from nexus.core.subagent import SubagentRequest, run_subagent


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


@pytest.mark.parametrize("reason", ["length", "max_tokens", "content_filter", "incomplete", "complete", "max_steps"])
def test_subagent_preserves_provider_stop(reason):
    async def runner(prompt, budget):
        yield SimpleNamespace(type="delta", text="result")
        yield SimpleNamespace(type="done", data={"stopped": reason, "steps": 1})
    result = asyncio.run(run_subagent(SubagentRequest(task="work"), make_runner=runner))
    assert result.stopped == reason
    assert result.text == "result"
