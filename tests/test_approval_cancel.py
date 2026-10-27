"""Regression: a pending owner decision is durable and never holds the run lock.

The retired in-memory waiter parked every new message behind a pending dialog.
Main decisions now persist the request and release the turn; answers are
applied on a later model boundary and retries are idempotent.
"""

from __future__ import annotations

import pytest

from nexus.runtime.decisions import MainDecisions
from nexus.task import TaskConflict, TaskStore


def decisions(tmp_path):
    return MainDecisions(TaskStore(tmp_path / "tasks.sqlite3"), "main")


def test_an_unanswered_request_survives_and_does_not_block_new_input(tmp_path):
    first = decisions(tmp_path)
    token = first.create("approval", {
        "tool": "bash", "arguments": {"command": "true"}, "workspace": "workspace"
    })
    reopened = decisions(tmp_path)
    assert reopened.pending()[0]["id"] == token["id"]
    assert reopened.create("question", {"questions": [{"question": "next?"}]})["status"] == "pending"


def test_no_pending_requests_is_harmless(tmp_path):
    assert decisions(tmp_path).pending() == []


def test_a_resolved_request_does_not_affect_the_next_one(tmp_path):
    store = decisions(tmp_path)
    first = store.create("question", {"questions": [{"question": "first?"}]})
    store.answer(first["id"], "done")
    second = store.create("question", {"questions": [{"question": "second?"}]})
    assert second["status"] == "pending"
    assert store.consume_question([{"question": "second?"}]) is None


def test_a_late_answer_to_a_finished_question_is_harmless(tmp_path):
    store = decisions(tmp_path)
    token = store.create("question", {"questions": [{"question": "q?"}]})
    store.answer(token["id"], "done")
    store.consume_question([{"question": "q?"}])
    with pytest.raises(TaskConflict, match="already resolved"):
        store.answer(token["id"], "late")


def test_the_latest_unanswered_question_is_still_visible(tmp_path):
    store = decisions(tmp_path)
    request = store.create("question", {"questions": [{"question": "still waiting?"}]})
    assert store.pending() == [request]
