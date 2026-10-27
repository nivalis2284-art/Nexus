from __future__ import annotations

import threading

import pytest

from nexus.core.config import ConfigStore
from nexus.instructions import load_instructions
from nexus.task import TaskConflict
from nexus.runtime.settings import save_settings


@pytest.fixture()
def state(tmp_path):
    store = ConfigStore(tmp_path / "home")
    store.ensure()
    workspace = tmp_path / "project"
    workspace.mkdir()
    store.save_preferences(workspace=str(workspace))
    return {"store": store}


def test_empty_provider_models_are_rejected_without_writing(state):
    store = state["store"]
    with pytest.raises(ValueError, match="model"):
        store.add_provider({"name": "Empty", "family": "anthropic_messages",
                            "base_url": "https://empty.test", "models": []})
    provider = store.providers()[0]
    with pytest.raises(ValueError, match="model"):
        store.update_provider(provider.id, {"models": []})
    assert store.providers()[0].models == provider.models


def test_permission_only_save_does_not_take_busy(state):
    busy = threading.Lock()
    busy.acquire()
    saved = save_settings(state, {"permission": "yolo"}, busy)
    assert saved.permission == "yolo"
    busy.release()


def test_workspace_change_waits_for_idle_and_applies_after_validation(state, tmp_path):
    store = state["store"]
    workspace = store.load().workspace
    busy = threading.Lock()
    busy.acquire()
    with pytest.raises(TaskConflict):
        save_settings(state, {"workspace": str(tmp_path)}, busy)
    assert store.load().workspace == str(workspace)
    busy.release()
    target = tmp_path / "other"
    target.mkdir()
    seen = []
    saved = save_settings(state, {"workspace": str(target)}, busy,
                          lambda path: seen.append(path))
    assert saved.workspace == str(target.resolve())
    assert seen == [target.resolve()]


def test_effect_failure_restores_config_and_instructions(state, tmp_path):
    store = state["store"]
    workspace = store.load().workspace
    store.save_preferences(permission="ask")
    before = store.read_config()
    target = tmp_path / "other"
    target.mkdir()

    def fail(path):
        raise RuntimeError("effect failed")

    with pytest.raises(RuntimeError):
        save_settings(state, {"permission": "yolo", "workspace": str(target),
                              "instructions": "changed"}, threading.Lock(), fail)
    assert store.read_config() == before
    assert store.load().workspace == str(workspace)
    assert load_instructions(store.home) == ""


def test_rollback_failure_reports_partial_state(state, tmp_path, monkeypatch):
    store = state["store"]
    target = tmp_path / "other"
    target.mkdir()
    real_write = store.write_config

    def fail(path):
        raise RuntimeError("effect failed")

    def fail_restore(value):
        if value.get("workspace") != str(target.resolve()):
            raise OSError("disk full")
        real_write(value)

    monkeypatch.setattr(store, "write_config", fail_restore)
    with pytest.raises(RuntimeError, match="partial state.*disk full"):
        save_settings(state, {"workspace": str(target)}, threading.Lock(), fail)


def test_missing_fields_leave_settings_unchanged(state):
    store = state["store"]
    before = store.read_config()
    save_settings(state, {}, threading.Lock())
    assert store.read_config() == before


@pytest.mark.parametrize("value", [
    {"permission": "always"},
    {"max_steps": 0},
    {"max_steps": "5"},
    {"instructions": 3},
])
def test_invalid_settings_are_rejected_before_any_write(state, value):
    store = state["store"]
    before = store.read_config()
    with pytest.raises(ValueError):
        save_settings(state, value, threading.Lock())
    assert store.read_config() == before
    assert load_instructions(store.home) == ""


@pytest.mark.parametrize("invalid", ["", "   ", None])
def test_whitespace_workspace_is_rejected(state, invalid):
    store = state["store"]
    workspace = store.load().workspace
    with pytest.raises(ValueError, match="workspace"):
        save_settings(state, {"workspace": invalid}, threading.Lock())
    assert store.load().workspace == str(workspace)
