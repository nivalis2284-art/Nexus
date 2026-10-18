"""Session rename: persisted titles, fallbacks and bounds.

Everything goes through real files in ``tmp_path``; a second ``SessionStore``
instance proves the title is on disk and not just in the in-memory object.
"""

from __future__ import annotations

from pathlib import Path

from nexus.session.store import Message, SessionStore


def test_rename_round_trip_persists(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = store.create(model="fake-model")

    assert store.rename(session.id, "  新的名字  ") is True

    # A fresh store instance reads only from disk.
    loaded = SessionStore(tmp_path).load(session.id)
    assert loaded is not None
    assert loaded.title == "新的名字"


def test_rename_missing_session_returns_false(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    assert store.rename("sdoesnotexist", "x") is False
    # An invalid id must also be a plain False, not a ValueError.
    assert store.rename("../../etc/passwd", "x") is False
    assert list(tmp_path.glob("*.jsonl")) == []


def test_rename_blank_title_falls_back(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = store.create(model="fake-model")

    assert store.rename(session.id, "   \n\t ") is True
    loaded = store.load(session.id)
    assert loaded is not None
    assert loaded.title == "新会话"


def test_rename_truncates_overlong_title(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = store.create(model="fake-model")
    long_title = "长" * 120

    assert store.rename(session.id, long_title) is True
    loaded = store.load(session.id)
    assert loaded is not None
    assert len(loaded.title) == 80
    assert loaded.title == "长" * 80


def test_rename_keeps_history(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = store.create(model="fake-model")
    store.append(session, Message(kind="user", text="你好"))
    store.append(session, Message(kind="assistant", text="你好，我是灵。"))

    assert store.rename(session.id, "保留历史") is True

    loaded = SessionStore(tmp_path).load(session.id)
    assert loaded is not None
    assert loaded.title == "保留历史"
    assert [item.text for item in loaded.items] == ["你好", "你好，我是灵。"]
    assert [item.kind for item in loaded.items] == ["user", "assistant"]


def test_list_reflects_renamed_title(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = store.create(model="fake-model")
    store.append(session, Message(kind="user", text="原始问题"))
    before = store.list()
    assert len(before) == 1
    assert before[0]["title"] == "原始问题"

    assert store.rename(session.id, "改名后的会话") is True

    after = SessionStore(tmp_path).list()
    assert len(after) == 1
    assert after[0]["id"] == session.id
    assert after[0]["title"] == "改名后的会话"
    # Renaming must not be read as a new message.
    assert after[0]["messages"] == 1
