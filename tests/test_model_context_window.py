"""Per-model context windows travel through config into the Agent budget."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from nexus.core.agent import Agent
from nexus.core.config import ConfigStore
from nexus.core.permission import default_policy
from nexus.core.workspace import inspect
from nexus.model import APIFamily, ModelCapabilities, ModelProfile, RuntimeCredentials
from nexus.tools import build


def _provider(store: ConfigStore):
    return store.add_provider({
        "name": "Windows",
        "family": "openai_chat_completions",
        "base_url": "https://windows.test/v1",
        "models": [
            {"id": "small", "context_window_tokens": 32000,
             "max_output_tokens": 2048, "capabilities": {"tools": True}},
            {"id": "large", "context_window_tokens": 200000,
             "max_output_tokens": 8192, "temperature": 0.2},
        ],
    })


def test_two_models_keep_independent_values_after_reload(tmp_path: Path):
    store = ConfigStore(tmp_path)
    store.ensure()
    created = _provider(store)

    reloaded = ConfigStore(tmp_path).providers()
    provider = next(item for item in reloaded if item.id == created.id)
    windows = {model["id"]: model["context_window_tokens"] for model in provider.models}
    assert windows == {"small": 32000, "large": 200000}
    assert provider.models[0]["max_output_tokens"] == 2048
    assert provider.models[1]["temperature"] == 0.2


def test_rename_preserves_other_model_fields(tmp_path: Path):
    store = ConfigStore(tmp_path)
    store.ensure()
    created = _provider(store)
    models = [dict(model) for model in created.models]
    models[0]["id"] = "smaller"
    store.update_provider(created.id, {"models": models})

    provider = next(item for item in ConfigStore(tmp_path).providers() if item.id == created.id)
    renamed = next(model for model in provider.models if model["id"] == "smaller")
    assert renamed["context_window_tokens"] == 32000
    assert renamed["max_output_tokens"] == 2048
    assert renamed["capabilities"] == {"tools": True}


def test_emptying_a_window_removes_the_override(tmp_path: Path):
    store = ConfigStore(tmp_path)
    store.ensure()
    created = _provider(store)
    models = [dict(model) for model in created.models]
    models[0]["context_window_tokens"] = ""
    store.update_provider(created.id, {"models": models})

    provider = next(item for item in ConfigStore(tmp_path).providers() if item.id == created.id)
    assert "context_window_tokens" not in provider.models[0]
    store.select(provider.id, "small")
    settings = store.load()
    assert settings.context_window == 64_000
    assert settings.profile.context_window_tokens is None


@pytest.mark.parametrize("value", [0, -5, 1.5, "64000", True])
def test_invalid_window_is_rejected(tmp_path: Path, value):
    store = ConfigStore(tmp_path)
    store.ensure()
    provider = store.providers()[0]
    model = dict(provider.models[0])
    model["context_window_tokens"] = value
    with pytest.raises(ValueError, match="positive integer"):
        store.update_provider(provider.id, {"models": [model]})
    assert store.providers()[0].models[0].get("context_window_tokens") != value


def test_global_limit_keeps_priority(tmp_path: Path):
    store = ConfigStore(tmp_path)
    store.ensure()
    created = _provider(store)
    store.select(created.id, "large")
    store.write_config({**store.read_config(), "context_limit": 120000})
    settings = store.load()
    assert settings.context_window == 120000
    assert settings.profile.context_window_tokens == 200000


def test_agent_budget_uses_profile_window_when_no_global_limit(tmp_path: Path):
    store = ConfigStore(tmp_path)
    store.ensure()
    created = _provider(store)
    store.select(created.id, "small")
    settings = store.load()
    profile = ModelProfile.model_validate(json.loads(settings.profile.model_dump_json()))
    assert profile.context_window_tokens == 32000

    registry, context = build({})
    context.root = tmp_path
    agent = Agent(
        profile=profile,
        credentials=RuntimeCredentials("test-key"),
        registry=registry,
        context=context,
        workspace=inspect(tmp_path),
        policy=default_policy("yolo"),
        context_limit=settings.context_limit,
    )
    context_event = {}

    async def run():
        async for event in agent.run([{"role": "user", "content": "hello"}]):
            if event.type == "context":
                context_event.update(event.data)
                break

    import nexus.core.agent as module

    original = module._client
    try:
        import httpx

        def handler(_request):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                text='data: {"id":"x","choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

        module._client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
        asyncio.run(run())
    finally:
        module._client = original
    assert context_event["window"] == 32000


def test_settings_default_matches_agent_fallback(tmp_path: Path):
    store = ConfigStore(tmp_path)
    settings = store.ensure()
    provider = settings.provider
    provider.models = [{"id": "plain"}]
    provider.base_url = "https://plain.test/v1"
    store.save_providers([provider])
    settings = store.load()
    assert settings.context_window == 64_000
    assert settings.profile.context_window_tokens is None


def test_add_provider_validates_window_before_saving(tmp_path: Path):
    store = ConfigStore(tmp_path)
    store.ensure()
    before = len(store.providers())
    with pytest.raises(ValueError, match="positive integer"):
        store.add_provider({
            "name": "Bad", "family": "openai_chat_completions",
            "base_url": "https://bad.test",
            "models": [{"id": "bad", "context_window_tokens": 0}],
        })
    assert len(store.providers()) == before
