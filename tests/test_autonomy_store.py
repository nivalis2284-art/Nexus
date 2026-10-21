"""Durable mechanics, deliberately separate from real autonomous behavior QA."""
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from nexus.autonomy.store import AutonomyStore
from nexus.task import TaskConflict, TaskStore


@pytest.fixture
def store(tmp_path):
    TaskStore(tmp_path / "tasks.db")
    result = AutonomyStore(tmp_path / "tasks.db")
    data = tmp_path / "data"
    data.mkdir()
    result.configure({"read_roots": [str(data)], "output_root": str(tmp_path / "out"),
                      "token_limit": 10000, "contact_after_seconds": 0,
                      "min_review_seconds": 1})
    result.command("enable")
    return result


def decision(source):
    return {"reason": "A real source left an open question", "goals": [{
        "id": "question", "expected_version": 0, "status": "waiting", "title": "question",
        "question": "what changed?", "reason": "read the actual document",
        "understanding": "uncertain", "sources": [source]}], "plans": [], "watches": [],
        "actions": [], "message": {"text": "new observation", "sources": [source]}}


def test_atomic_commit_survives_restart_and_message_read_is_not_event_consumption(store):
    fact = store.add_event("real-source", "source_change", {"content": "a fact"})
    run = store.begin_review()
    store.commit_review(run, decision(fact["id"]))
    restarted = AutonomyStore(store.path)
    restarted.recover()
    assert restarted.get("goal", "question")["sources"] == [fact["id"]]
    message = restarted.messages()[0]
    restarted.read_message(message["id"])
    assert restarted.messages()[0]["read_at"]
    assert not restarted.events(pending=True)
    with pytest.raises(TaskConflict):
        restarted.commit_review(run, decision(fact["id"]))
    assert len(restarted.messages()) == 1


def test_user_input_and_pause_reject_stale_decisions_without_partial_goal_or_message(store):
    fact = store.add_event("source", "source_change", {})
    run = store.begin_review()
    store.note_user("new actual input", "input:1")
    with pytest.raises(TaskConflict):
        store.commit_review(run, decision(fact["id"]))
    assert store.get("goal", "question") is None
    assert not store.messages()
    assert store.events(pending=True)
    store.fail_review(run["id"], "changed", delay=0)
    run = store.begin_review()
    store.command("pause")
    with pytest.raises(TaskConflict):
        store.commit_review(run, decision(fact["id"]))
    restarted = AutonomyStore(store.path)
    restarted.recover()
    assert restarted.control()["state"] == "paused"
    assert restarted.begin_review() is None


def test_quota_reservation_is_atomic_unknown_is_charged_and_configuration_never_resets(store):
    def reserve(_):
        try:
            return store.reserve(3000)
        except PermissionError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        tickets = [x for x in pool.map(reserve, range(4)) if x]
    assert len(tickets) == 3
    assert store.budget()["remaining"] == 1000
    store.settle(tickets[0], {"input_tokens": 600, "output_tokens": 100})
    store.settle(tickets[0], {"total_tokens": 5})
    store.settle(tickets[1], None)
    store.recover()
    assert store.budget() == {"limit": 10000, "spent": 6700, "reserved": 0,
                              "remaining": 3300, "unknown": 2}
    store.configure({"token_limit": 12000})
    assert store.budget()["spent"] == 6700


def test_withdrawal_cancels_plans_and_survives_provider_replacement(store):
    fact = store.add_event("source", "source_change", {})
    run = store.begin_review()
    value = decision(fact["id"])
    value["plans"] = [{"id": "tomorrow", "goal_id": "question", "status": "pending", "due_at": "2030-01-01T00:00:00+00:00"}]
    value["watches"] = [{"id": "watch", "goal_id": "question", "status": "active", "path": store.settings()["read_roots"][0]}]
    store.commit_review(run, value)
    store.withdraw("question", 1)
    assert store.get("plan", "tomorrow")["status"] == "cancelled"
    assert store.get("watch", "watch")["status"] == "stopped"
    restarted = AutonomyStore(store.path)
    restarted.recover()
    assert restarted.get("goal", "question")["status"] == "withdrawn"
    with restarted.transaction() as db, pytest.raises(TaskConflict):
        restarted.put("goal", "question", {}, "active", db)
    assert restarted.messages()[0]["text"] == "new observation"


def test_recover_releases_abandoned_claim_but_keeps_sources(store):
    store.add_event("source", "source_change", {})
    run = store.begin_review()
    assert store.begin_review() is None
    store.recover()
    again = store.begin_review(time.time()+2)
    assert again and again["id"] != run["id"]
    assert again["events"][0]["source_key"] == "source"
