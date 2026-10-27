"""Durable main-conversation decisions: nonblocking, owned, idempotent."""

from __future__ import annotations

import threading

import pytest

from nexus.runtime.decisions import MainDecisions
from nexus.task import TaskConflict, TaskStore, TaskValidationError


def decisions(tmp_path, session_id="main"):
    return MainDecisions(TaskStore(tmp_path / "tasks.sqlite3"), session_id)


def test_a_pending_request_never_blocks_other_work(tmp_path):
    store = decisions(tmp_path)
    request = store.create("approval", {"workspace": "workspace", "tool": "write_file", "arguments": {"path": "a"}})
    seen = []

    def other_work():
        seen.append(store.create("question", {"questions": [{"question": "next?"}]}))

    thread = threading.Thread(target=other_work)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert seen[0]["kind"] == "question"
    assert [item["id"] for item in store.pending()] == [request["id"], seen[0]["id"]]


def test_requests_are_scoped_to_their_conversation(tmp_path):
    owner = decisions(tmp_path, "owner")
    other = decisions(tmp_path, "other")
    request = owner.create("approval", {"workspace": "workspace", "tool": "bash", "arguments": {"command": "rm x"}})
    assert other.pending() == []
    with pytest.raises(TaskConflict, match="another conversation"):
        other.answer(request["id"], True)
    assert owner.pending()[0]["status"] == "pending"


def test_answers_are_idempotent_and_typed(tmp_path):
    store = decisions(tmp_path)
    request = store.create("approval", {"workspace": "workspace", "tool": "write_file", "arguments": {"path": "a", "content": "x"}})
    with pytest.raises(TaskValidationError, match="boolean"):
        store.answer(request["id"], "yes")
    first = store.answer(request["id"], True)
    assert first["status"] == "answered" and first["answer"] is True
    assert store.answer(request["id"], True)["answer"] is True
    with pytest.raises(TaskConflict, match="already resolved"):
        store.answer(request["id"], False)
    assert store.granted("write_file", {"path": "a", "content": "x"}, "workspace")
    assert not store.granted("write_file", {"path": "b", "content": "x"}, "workspace")


def test_question_answers_are_text_or_text_list(tmp_path):
    store = decisions(tmp_path)
    request = store.create("question", {"questions": [{"question": "q?"}]})
    with pytest.raises(TaskValidationError, match="text"):
        store.answer(request["id"], 7)
    store.answer(request["id"], ["one", "two"])
    assert store.consume_question([{"question": "q?"}]) == ["one", "two"]
    assert store.consume_question([{"question": "q?"}]) is None


def test_duplicate_pending_request_is_reused(tmp_path):
    store = decisions(tmp_path)
    payload = {"workspace": "workspace", "tool": "edit_file", "arguments": {"path": "a", "old": "x", "new": "y"}}
    assert store.create("approval", payload)["id"] == store.create("approval", payload)["id"]
    assert len(store.pending()) == 1


def test_dispatch_is_one_shot_and_unknown_outcome_is_not_replayed(tmp_path):
    store = decisions(tmp_path)
    payload = {"workspace": "workspace", "tool": "bash", "arguments": {"command": "touch a"}}
    request = store.create("approval", payload)
    store.answer(request["id"], True)
    assert store.dispatch("bash", payload["arguments"], "workspace") == request["id"]
    assert store.dispatch("bash", payload["arguments"], "workspace") is None
    assert not store.granted("bash", payload["arguments"], "workspace")
    store.finish(request["id"], unknown=True)
    assert store.dispatch("bash", payload["arguments"], "workspace") is None
    assert store.pending()[0]["status"] == "unknown"
    with pytest.raises(TaskConflict, match="unknown"):
        store.answer(request["id"], True)


def test_restart_marks_an_inflight_dispatch_unknown(tmp_path):
    first = decisions(tmp_path)
    request = first.create("approval", {"workspace": "workspace", "tool": "bash", "arguments": {"command": "x"}})
    first.answer(request["id"], True)
    first.dispatch("bash", {"command": "x"}, "workspace")
    second = decisions(tmp_path)
    pending = second.pending()
    assert [item["status"] for item in pending] == ["unknown"]
    with pytest.raises(TaskConflict):
        second.answer(request["id"], True)


def test_finishing_without_a_request_is_harmless(tmp_path):
    decisions(tmp_path).finish(None)


def test_approval_cannot_be_reused_in_another_workspace(tmp_path):
    store = decisions(tmp_path)
    arguments = {"path": "a.txt", "content": "x"}
    request = store.create("approval", {
        "tool": "write_file", "arguments": arguments, "workspace": "project-A",
    })
    store.answer(request["id"], True)
    assert not store.granted("write_file", arguments, "project-B")
    assert store.dispatch("write_file", arguments, "project-B") is None
    assert store.granted("write_file", arguments, "project-A")
    assert store.dispatch("write_file", arguments, "project-A") == request["id"]

def test_unknown_review_only_closes_the_notice(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    decisions = MainDecisions(store, "owner")
    request = decisions.create("approval", {"workspace": "workspace", "tool": "write_file", "arguments": {"path": "x"}})
    with pytest.raises(TaskConflict):
        decisions.review_unknown(request["id"])
    decisions.answer(request["id"], True)
    decisions.dispatch("write_file", {"path": "x"}, "workspace")
    decisions = MainDecisions(store, "owner")
    assert decisions.pending()[0]["status"] == "unknown"
    assert decisions.review_unknown(request["id"])["status"] == "reviewed"
    assert decisions.pending() == []
    assert not decisions.granted("write_file", {"path": "x"}, "workspace")
    assert decisions.dispatch("write_file", {"path": "x"}, "workspace") is None
    assert decisions.review_unknown(request["id"])["status"] == "reviewed"
