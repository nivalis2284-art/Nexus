"""Tests for multi-provider configuration and reasoning-level mapping."""

from __future__ import annotations

import json

import pytest

from nexus.core import effort
from nexus.core.config import ConfigStore
from nexus.model import APIFamily, ModelCapabilities, ModelProfile


def test_default_config_has_one_provider(tmp_path):
    store = ConfigStore(tmp_path)
    settings = store.ensure()
    assert store.providers()
    # No endpoint ships by default: shipping a real gateway would make every
    # clone point at somebody else's server. First run asks for it.
    assert settings.provider.base_url == ""
    assert settings.provider.models


def test_add_edit_remove_provider(tmp_path):
    store = ConfigStore(tmp_path)
    store.ensure()
    created = store.add_provider(
        {
            "name": "Second",
            "family": "openai_chat_completions",
            "base_url": "https://second.test/v1",
            "models": [{"id": "m-a"}, {"id": "m-b"}],
            "api_key": "k-2",
        }
    )
    assert len(store.providers()) == 2
    assert store.has_key(created.id)

    store.update_provider(created.id, {"name": "Renamed", "api_key": "k-3"})
    refreshed = next(p for p in store.providers() if p.id == created.id)
    assert refreshed.name == "Renamed"
    assert store.read_credentials()[created.id] == "k-3"

    store.remove_provider(created.id)
    assert len(store.providers()) == 1
    assert created.id not in store.read_credentials()


def test_removing_the_selected_provider_reselects(tmp_path):
    store = ConfigStore(tmp_path)
    store.ensure()
    first = store.providers()[0]
    second = store.add_provider(
        {"name": "B", "family": "anthropic_messages", "base_url": "https://b.test",
         "models": [{"id": "b-model"}]}
    )
    store.select(second.id, "b-model", "high")
    store.remove_provider(first.id)
    settings = store.load()
    assert settings.provider.id == second.id
    assert settings.model["id"] == "b-model"


def test_select_persists_effort_and_rejects_unknown(tmp_path):
    store = ConfigStore(tmp_path)
    store.ensure()
    provider = store.providers()[0]
    model = provider.models[0]["id"]
    store.select(provider.id, model, "xhigh")
    assert store.load().effort == "xhigh"
    with pytest.raises(effort.EffortError):
        effort.normalize("ultra")


def test_every_level_is_accepted():
    assert effort.LEVELS == ("low", "medium", "high", "xhigh", "max")
    for level in effort.LEVELS:
        assert effort.normalize(level) == level
    assert effort.normalize("off") is None
    assert effort.normalize("") is None
    assert effort.normalize(None) is None


def _profile(family: APIFamily) -> ModelProfile:
    return ModelProfile(
        api_family=family,
        model="m",
        base_url="https://x.test",
        capabilities=ModelCapabilities(tools=True),
        max_output_tokens=4096,
    )


def test_chat_mapping_uses_reasoning_effort():
    profile = _profile(APIFamily.OPENAI_CHAT_COMPLETIONS)
    for level in effort.LEVELS:
        body = {}
        effort.apply(body, profile.api_family, profile, level)
        assert body == {"reasoning_effort": level}


def test_responses_mapping_uses_reasoning_effort_object():
    profile = _profile(APIFamily.OPENAI_RESPONSES)
    body = {}
    effort.apply(body, profile.api_family, profile, "max")
    assert body == {"reasoning": {"effort": "max"}}


def test_anthropic_mapping_enables_thinking_and_raises_max_tokens():
    profile = _profile(APIFamily.ANTHROPIC_MESSAGES)
    body = {"max_tokens": 1000}
    effort.apply(body, profile.api_family, profile, "xhigh")
    assert body["thinking"]["type"] == "enabled"
    assert body["thinking"]["budget_tokens"] == effort.THINKING_BUDGET["xhigh"]
    assert body["output_config"] == {"effort": "xhigh"}
    # A thinking block counts against max_tokens, so it must fit alongside.
    assert body["max_tokens"] > body["thinking"]["budget_tokens"]


def test_absent_level_sends_nothing():
    profile = _profile(APIFamily.ANTHROPIC_MESSAGES)
    body = {"max_tokens": 1000}
    effort.apply(body, profile.api_family, profile, None)
    assert body == {"max_tokens": 1000}


def test_off_is_never_sent_as_a_value():
    profile = _profile(APIFamily.OPENAI_CHAT_COMPLETIONS)
    body = {}
    effort.apply(body, profile.api_family, profile, "off")
    assert body == {}


def test_per_level_override_wins():
    profile = _profile(APIFamily.OPENAI_CHAT_COMPLETIONS)
    object.__setattr__(profile, "effort_values", {"max": "max_thinking"})
    body = {}
    effort.apply(body, profile.api_family, profile, "max")
    assert body["reasoning_effort"] == "max_thinking"


def test_describe_reports_the_wire_field():
    profile = _profile(APIFamily.ANTHROPIC_MESSAGES)
    info = effort.describe(profile.api_family, "low", profile)
    assert info["field"] == "output_config.effort"
    assert info["value"] == "low"
    assert info["budget_tokens"] == effort.THINKING_BUDGET["low"]


def test_request_body_carries_effort_for_all_families():
    from nexus.core.protocol import request_body

    for family in APIFamily:
        profile = _profile(family)
        body = request_body(profile, "sys", [{"role": "user", "content": "hi"}], [], effort="high")
        text = json.dumps(body)
        assert "high" in text
        if family == APIFamily.ANTHROPIC_MESSAGES:
            assert "output_config" in body
            assert body["thinking"]["type"] == "enabled"
        elif family == APIFamily.OPENAI_RESPONSES:
            assert body["reasoning"]["effort"] == "high"
        else:
            assert body["reasoning_effort"] == "high"
