import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core.protocol import request_body
from nexus.model import APIFamily, ModelCapabilities, ModelProfile
from nexus.tools import screenshot


def test_screen_capture_returns_image_and_saves_cache(tmp_path, monkeypatch):
    from PIL import Image, ImageGrab
    monkeypatch.setattr(screenshot, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(ImageGrab, "grab", lambda **kwargs: Image.new("RGB", (20, 10), "red"))
    context = SimpleNamespace(agent=SimpleNamespace(profile=SimpleNamespace(capabilities=SimpleNamespace(images=True))))
    result = screenshot.capture(context, {"target": "screen"})
    assert base64.b64decode(result["image"]["data"]).startswith(b"\xff\xd8")
    assert len(list((tmp_path / "screenshots").glob("*.jpg"))) == 1


def test_task_history_retains_tool_image(tmp_path):
    from nexus.task import TaskStore
    store = TaskStore(tmp_path / "tasks.sqlite3")
    task = store.create(title="test", goal="inspect", workspace=str(tmp_path))
    blocks = [{"type": "text", "text": "saved"}, {"type": "image", "mime": "image/jpeg", "data": "QUJD"}]
    store.save_run_messages(task.id, [
        {"role": "assistant", "tool_calls": [{"id": "a", "name": "screenshot", "arguments": "{}"}]},
        {"role": "tool", "tool_call_id": "a", "name": "screenshot", "content": blocks},
    ])
    result = next(m for m in store.messages_for_model(task.id, include_images=True) if m["role"] == "tool")
    assert result["content"] == blocks


def test_command_temporary_environment_uses_cache(tmp_path, monkeypatch):
    from nexus import cache
    monkeypatch.setattr(cache, "CACHE", tmp_path / "cache")
    env = cache.process_environment({"OTHER": "value"})
    assert env["TEMP"] == env["TMP"] == env["TMPDIR"] == str(tmp_path / "cache")
    assert env["OTHER"] == "value" and (tmp_path / "cache").is_dir()


@pytest.mark.parametrize("family", list(APIFamily))
def test_tool_images_follow_all_results_for_each_model_protocol(family):
    profile = ModelProfile(api_family=family, model="test", base_url="https://example.test",
                           capabilities=ModelCapabilities(images=True, tools=True))
    calls = [{"id": "a", "name": "screenshot", "arguments": "{}"},
             {"id": "b", "name": "other", "arguments": "{}"}]
    messages = [{"role": "user", "content": "look"},
                {"role": "assistant", "content": "", "tool_calls": calls},
                {"role": "tool", "tool_call_id": "a", "content": [{"type": "text", "text": "saved"},
                    {"type": "image", "mime": "image/jpeg", "data": "QUJD"}]},
                {"role": "tool", "tool_call_id": "b", "content": "other result"}]
    body = request_body(profile, "system", messages, [])
    assert "QUJD" in str(body)
    assert str(body).index("other result") < str(body).index("QUJD")
    assert isinstance(messages[2]["content"], list)
    text_profile = profile.model_copy(update={"capabilities": ModelCapabilities(images=False, tools=True)})
    assert "QUJD" not in str(request_body(text_profile, "system", messages, []))
