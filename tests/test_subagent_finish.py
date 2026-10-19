"""Provider truncation must remain a failure when returned through a child tool."""

from types import SimpleNamespace

import pytest

from nexus.tools.subagent_tool import spawn


@pytest.mark.parametrize("reason", ["length", "max_tokens", "content_filter", "incomplete"])
def test_subagent_preserves_abnormal_provider_stop(reason):
    context = SimpleNamespace(spawn_subagent=lambda *args: SimpleNamespace(
        text="partial result", stopped=reason, steps=1,
    ))
    result = spawn(context, {"task": "finish the work", "max_steps": 12})
    assert result["ok"] is False
    assert result["content"] == "partial result"
    assert result["stopped"] == reason


@pytest.mark.parametrize("reason", ["complete", "max_steps"])
def test_subagent_keeps_existing_normal_and_step_budget_results(reason):
    context = SimpleNamespace(spawn_subagent=lambda *args: SimpleNamespace(
        text="result", stopped=reason,
    ))
    assert spawn(context, {"task": "work", "max_steps": 12})["ok"] is True
