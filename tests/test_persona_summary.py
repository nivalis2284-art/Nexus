"""Summary suggestions use the selected model without saving or using tools."""

import asyncio
import json

import httpx
import pytest

from nexus.core import agent as loop
from nexus.core.config import ConfigStore
from nexus.model import APIFamily
from nexus.web import persona_api


def configured(tmp_path, family=APIFamily.OPENAI_CHAT_COMPLETIONS):
    store = ConfigStore(tmp_path)
    provider = store.ensure().provider
    store.update_provider(provider.id, {
        "family": family.value, "base_url": "https://model.test/v1",
        "models": [{"id": "selected-model", "max_output_tokens": 1024}],
    })
    store.select(provider.id, "selected-model", "high")
    store.write_credential(provider.id, "test-key")
    return store


def test_summary_sends_only_entry_and_selected_model(tmp_path, monkeypatch):
    store = configured(tmp_path)
    seen = []

    def reply(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=(
            'data: {"choices":[{"index":0,"delta":{"content":"我愿意表达独立判断。"},'
            '"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        ))

    monkeypatch.setattr(loop, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    before = sorted(path.name for path in tmp_path.iterdir())
    result = asyncio.run(persona_api.generate_summary(store.load(), "判断", "表达有依据的不同意见。"))
    assert result == "我愿意表达独立判断。"
    assert sorted(path.name for path in tmp_path.iterdir()) == before
    assert len(seen) == 1
    sent = seen[0]
    assert sent["model"] == "selected-model"
    assert sent["reasoning_effort"] == "high"
    assert not sent.get("tools")
    assert len(sent["messages"]) == 2
    assert json.loads(sent["messages"][1]["content"]) == {
        "name": "判断", "body": "表达有依据的不同意见。",
    }


@pytest.mark.parametrize("status, text", [(503, "unavailable"), (200, 'data: [DONE]\n\n')])
def test_summary_failure_does_not_return_partial_suggestion(tmp_path, monkeypatch, status, text):
    store = configured(tmp_path)
    monkeypatch.setattr(loop, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, headers={"content-type": "text/event-stream"}, text=text),
    )))
    with pytest.raises(persona_api.SummaryGenerationError):
        asyncio.run(persona_api.generate_summary(store.load(), "判断", "内容"))
