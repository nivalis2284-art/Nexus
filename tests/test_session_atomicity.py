"""Concurrent rename must not replace newly appended conversation records."""
import threading

import pytest

from nexus.session import Message, SessionStore


def test_rename_and_append_preserve_both_changes(tmp_path, monkeypatch):
    store = SessionStore(tmp_path)
    session = store.create("fake")
    store.append(session, Message(kind="user", text="original"))
    rewriting = threading.Event()
    release = threading.Event()
    appended = threading.Event()
    rewrite = store.rewrite
    failures = []

    def paused_rewrite(snapshot):
        rewriting.set()
        assert release.wait(3)
        rewrite(snapshot)

    monkeypatch.setattr(store, "rewrite", paused_rewrite)
    def rename():
        try:
            assert store.rename(session.id, "renamed")
        except Exception as error:
            failures.append(error)
    def append():
        try:
            store.append(session, Message(kind="assistant", text="new answer"))
            appended.set()
        except Exception as error:
            failures.append(error)

    renamer = threading.Thread(target=rename)
    appender = threading.Thread(target=append)
    try:
        renamer.start()
        assert rewriting.wait(3)
        appender.start()
        assert not appended.wait(0.05), "append entered during rename's read/rewrite transaction"
    finally:
        release.set()
        renamer.join(timeout=3)
        if appender.ident is not None:
            appender.join(timeout=3)
    assert not renamer.is_alive() and not appender.is_alive()
    assert not failures
    loaded = store.load(session.id)
    assert loaded.title == "renamed"
    assert [m.text for m in loaded.items] == ["original", "new answer"]


def test_automatic_first_title_does_not_replace_owner_title(tmp_path):
    store = SessionStore(tmp_path)
    session = store.create("fake")
    assert store.rename(session.id, "owner title")
    assert store.rename(session.id, "automatic title", only_if_empty=True)
    assert store.load(session.id).title == "owner title"


@pytest.mark.parametrize("tail", [b"", b'{"kind":"assistant","text":"torn', b'{"text":"\xe4'])
def test_append_after_unterminated_record_preserves_new_messages(tmp_path, tail):
    store = SessionStore(tmp_path)
    session = store.create("fake")
    store.append(session, Message(kind="user", text="before restart"))
    original = session.path.read_bytes().rstrip(b"\n") if not tail else session.path.read_bytes() + tail
    session.path.write_bytes(original)
    restored = store.load(session.id)
    store.append(restored, Message(kind="user", text="first after restart"))
    assert [m.text for m in store.load(session.id).items] == ["before restart", "first after restart"]
    assert session.path.read_bytes().startswith(original)
