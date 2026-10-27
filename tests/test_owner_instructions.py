from types import SimpleNamespace

import pytest

from nexus.instructions import load_instructions, save_instructions
from nexus.tools.instructions import tool_specs
from nexus.tools.registry import Registry
from nexus.web.instructions_api import route


def test_missing_file_is_empty_and_exact_text_round_trips(tmp_path):
    assert load_instructions(tmp_path) == ""

    text = "  Answer first.\n先给结论，再解释。  "
    assert save_instructions(tmp_path, text) == text
    assert (tmp_path / "owner-instructions.txt").read_text(encoding="utf-8") == text
    assert load_instructions(tmp_path) == text


def test_legacy_persona_is_never_imported(tmp_path):
    (tmp_path / "persona.json").write_text(
        '{"sections": [{"id": "core", "title": "Core", "content": "OLD"}]}',
        encoding="utf-8",
    )
    assert load_instructions(tmp_path) == ""


@pytest.mark.parametrize("value", [None, 42, b"bytes"])
def test_save_rejects_non_text_without_creating_a_file(tmp_path, value):
    with pytest.raises(ValueError):
        save_instructions(tmp_path, value)
    assert not (tmp_path / "owner-instructions.txt").exists()


def test_route_reads_and_replaces_text(tmp_path):
    state = {"store": SimpleNamespace(home=tmp_path)}
    assert route("GET", "/api/instructions", None, state) == (200, {"text": ""})

    saved = route("PUT", "/api/instructions", {"text": "Prefer short replies."}, state)
    assert saved == (200, {"text": "Prefer short replies."})
    assert route("GET", "/api/instructions", None, state) == saved

    assert route("PUT", "/api/instructions", {"text": 3}, state)[0] == 400
    assert route("POST", "/api/instructions", {}, state)[0] == 405
    assert route("GET", "/api/other", None, state) is None


def test_instruction_tools_are_read_only_until_editing_is_enabled(tmp_path):
    background = Registry()
    for spec in tool_specs(tmp_path):
        background.add(spec)
    assert [spec.name for spec in background.specs()] == ["read_instructions"]

    chat = Registry()
    for spec in tool_specs(tmp_path, writable=True):
        chat.add(spec)
    result = chat.execute("save_instructions", {"text": "Prefer direct answers."}, None)
    assert result.ok
    assert result.data["text"] == "Prefer direct answers."
    assert background.execute("read_instructions", {}, None).content == "Prefer direct answers."
    assert not chat.execute("save_instructions", {"text": "x", "extra": 1}, None).ok


def test_tool_specs_accept_a_store_exposing_home(tmp_path):
    store = SimpleNamespace(home=tmp_path)
    save_instructions(tmp_path, "Saved through the store.")
    registry = Registry()
    for spec in tool_specs(store, writable=True):
        registry.add(spec)
    assert registry.execute("read_instructions", {}, None).content == "Saved through the store."
