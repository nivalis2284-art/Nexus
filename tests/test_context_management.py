"""Context management persistence and the read-only main snapshot."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus.core.config import ConfigStore
from nexus.core.prompt import ROLE, ROLE_RULES
from nexus.instructions import load_instructions, save_instructions
from nexus.ling import LING_INSTRUCTIONS
from nexus.runtime.context_management import save_owner_items, save_role, snapshot
from nexus.runtime.history import history_version
from nexus.session import Message, SessionStore


def _state(tmp_path):
    config = ConfigStore(tmp_path / "home")
    config.ensure()
    config.save_preferences(workspace=str(tmp_path), permission="ask")
    return {"store": config, "sessions": SessionStore(config.sessions_dir),
            "tasks": SimpleNamespace(list=lambda: []),
            "task_controller": SimpleNamespace(is_active=lambda _: False,
                                               queued_ids=lambda: set()),
            "ling": None, "mcp": None}, config


def _bind_ling(state, session):
    state["ling"] = SimpleNamespace(
        session_id=session.id, state=state, current_input=0, running=True,
    )


def test_owner_items_keep_boundaries_through_text_round_trip(tmp_path):
    items = ["Answer first.\nThen explain.", "Keep the original formatting.  "]
    save_owner_items(tmp_path, items)
    text = load_instructions(tmp_path)
    assert text == "\n\n".join(items)
    # Settings sends the unchanged text on every save; an identical text save
    # must not collapse the structured entries into one.
    assert save_instructions(tmp_path, text) == text
    from nexus.runtime.context_management import _owner_items
    assert _owner_items(tmp_path) == items


def test_owner_text_edit_replaces_items_and_legacy_text_is_one_item(tmp_path):
    save_owner_items(tmp_path, ["first", "second"])
    save_instructions(tmp_path, "new owner text")
    from nexus.runtime.context_management import _owner_items
    assert _owner_items(tmp_path) == ["new owner text"]
    (tmp_path / "owner-instructions.txt").write_text("legacy", encoding="utf-8")
    assert _owner_items(tmp_path) == ["legacy"]


def test_roles_default_exactly_and_persist_independently(tmp_path):
    from nexus.role_instructions import load_role
    assert load_role(tmp_path, "main") == LING_INSTRUCTIONS + "\n\n" + ROLE_RULES
    assert load_role(tmp_path, "work") == ROLE
    assert load_role(tmp_path, "subagent") == ROLE
    save_role(tmp_path, "main", "custom main")
    save_role(tmp_path, "work", "custom work")
    assert load_role(tmp_path, "main") == "custom main"
    assert load_role(tmp_path, "work") == "custom work"
    assert load_role(tmp_path, "subagent") == ROLE
    with pytest.raises(ValueError):
        save_role(tmp_path, "other", "text")
    with pytest.raises(ValueError):
        save_role(tmp_path, "main", "  ")


def test_snapshot_uses_session_indices_and_only_effective_history(tmp_path):
    state, config = _state(tmp_path)
    save_owner_items(config.home, ["one", "two"])
    save_role(config.home, "main", "MAIN-ROLE-MARKER")
    sessions = state["sessions"]
    session = sessions.create("test")
    for item in [Message("user", "old"), Message("assistant", "old reply"),
                 Message("checkpoint", "<compacted-summary>summary</compacted-summary>",
                         {"through": "1"}),
                 Message("user", "new"), Message("tool", "result", {"name": "read_file"}),
                 Message("notice", "notice")]:
        sessions.append(session, item)
    _bind_ling(state, session)
    value = snapshot(state, sessions.load(session.id))
    assert value["version"] == history_version(sessions.load(session.id))
    assert value["owner_instructions"] == ["one", "two"]
    assert [role["id"] for role in value["roles"]] == ["main", "work", "subagent"]
    assert value["roles"][0]["text"] == "MAIN-ROLE-MARKER"
    assert value["summary"] == {
        "index": 2, "text": "summary", "through": "1"
    }
    assert [(item["index"], item["role"], item["text"]) for item in value["messages"]] == [
        (3, "user", "new"), (4, "tool", "result"), (5, "notice", "notice")
    ]
    assert {section["id"] for section in value["sections"]}.isdisjoint({"owner", "role"})
    assert {"environment", "priority", "permission", "tools"} <= {
        section["id"] for section in value["sections"]
    }


def test_snapshot_has_no_summary_without_a_checkpoint(tmp_path):
    state, _ = _state(tmp_path)
    sessions = state["sessions"]
    session = sessions.create("test")
    sessions.append(session, Message("user", "hello"))
    _bind_ling(state, session)
    value = snapshot(state, session)
    assert value["summary"] is None
    assert value["messages"][0]["index"] == 0
    assert value["messages"][0]["role"] == "user"
