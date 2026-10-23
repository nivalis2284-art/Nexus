import sqlite3
import threading

from nexus import skills
from nexus.ling import LingInbox
from nexus.persona import PersonaStore
from nexus.session import SessionStore
from nexus.task import TaskStore


def test_persona_gbk_entry_reads_and_saves_without_losing_text(tmp_path):
    folder = tmp_path / "persona-skills" / "test"
    folder.mkdir(parents=True)
    path = folder / "SKILL.md"
    source = (
        "---\n"
        "name: test\n"
        "description: 旧摘要\n"
        "when: 需要核对时\n"
        "custom:\n"
        "  owner: 团队\n"
        "---\n"
        "中文正文：保留原样。\n"
    ).encode("gbk")
    path.write_bytes(source)

    store = PersonaStore(tmp_path)
    entries = store.load()["entries"]
    assert entries[0]["name"] == "test"
    assert entries[0]["description"] == "旧摘要"
    assert entries[0]["body"] == "中文正文：保留原样。\n"
    assert path.read_bytes() == source

    document = store.load()
    document["entries"][0]["body"] = "保存后的中文正文。\n"
    document["entries"][0]["description"] = "新摘要"
    saved = store.save(document)
    assert saved["entries"][0]["body"] == "保存后的中文正文。\n"

    encoded = path.read_bytes()
    text = encoded.decode("utf-8").replace("\r\n", "\n")
    metadata, body = skills.parse_frontmatter(text)
    assert body == "保存后的中文正文。\n"
    assert metadata["name"] == "test"
    assert metadata["description"] == "新摘要"
    assert metadata["when"] == "需要核对时"
    assert "custom:\n  owner: 团队\n" in text


def test_ling_worker_survives_inbox_read_error(tmp_path, monkeypatch):
    state = {"tasks": TaskStore(tmp_path / "tasks.db"),
             "sessions": SessionStore(tmp_path / "sessions")}
    original = LingInbox._next
    failed = threading.Event()
    executed = threading.Event()
    def next_item(self):
        if not failed.is_set():
            failed.set()
            raise sqlite3.OperationalError("temporary read error")
        return original(self)
    monkeypatch.setattr(LingInbox, "_next", next_item)
    inbox = LingInbox(state, lambda *args: executed.set(), threading.Lock())
    try:
        assert failed.wait(2)
        assert "temporary read error" in inbox.last_error
        inbox.enqueue("hello", [], [], "test-input")
        assert executed.wait(3)
        assert inbox._thread.is_alive()
    finally:
        inbox.close()


def test_pending_decision_cannot_start_after_pause(tmp_path):
    import pytest
    from nexus.runtime import TaskController
    from nexus.task import TaskConflict
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create(title="approval", goal="test", workspace=str(tmp_path))
    controller = TaskController(store)
    store.command(task.id, "running", request_id="run")
    interaction = store.interaction_create(task_id=task.id, run_id=None, kind="approval", payload={})
    store.command(task.id, "waiting_user", request_id="wait")
    controller.pause(task.id, request_id="pause")
    with pytest.raises(TaskConflict, match="pending interaction"):
        controller.submit(task.id, request_id="restart")
    assert store.pending_interactions(task.id)[0]["id"] == interaction
    assert controller.queued_ids() == []


def test_interaction_uses_current_version_and_rejects_stale_writer(tmp_path):
    import pytest
    from nexus.task import TaskConflict
    store = TaskStore(tmp_path / "tasks.db")
    task = store.create(title="approval", goal="test", workspace=str(tmp_path))
    store.command(task.id, "running", request_id="run")
    with pytest.raises(TaskConflict, match="version changed"):
        store.interaction_create(task_id=task.id, run_id=None, kind="question", payload={},
                                 control_version=task.control_version)
    current = store.get(task.id)
    interaction = store.interaction_create(task_id=task.id, run_id=None, kind="question", payload={})
    store.interaction_answer(interaction, answer="yes", expected_version=current.control_version)
    assert not store.pending_interactions(task.id)
    store.command(task.id, "paused", request_id="pause")
    with pytest.raises(TaskConflict, match="suspended"):
        store.interaction_create(task_id=task.id, run_id="old-run", kind="approval", payload={})
