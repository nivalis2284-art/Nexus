"""Autonomous appointments reuse durable schedules without changing base cadence."""
from datetime import datetime, timedelta

import httpx
import pytest

from nexus_scheduler.runtime import SchedulerRuntime
from nexus_scheduler.store import SchedulerStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    import nexus_scheduler.store as module
    monkeypatch.setattr(module, "now_local", lambda: datetime.fromisoformat("2030-01-02T09:00:00+09:00"))
    return SchedulerStore(tmp_path / "scheduler.sqlite3")


def book(store, **kwargs):
    return store.schedule_wakeup("继续研究", "等待新的思路", "复核第二个方案", **kwargs)


def test_book_update_restart_and_cancel_without_changing_settings(store):
    settings = store.settings()
    builtins = store.schedules()
    first = book(store, delay_minutes=120)
    second = book(store, run_at="2030-01-03T10:00:00+09:00")
    assert first["id"] != second["id"]
    assert first["next_run_at"] == "2030-01-02T11:00:00+09:00"
    assert (first["kind"], first["origin"], first["purpose"], first["notify"]) == (
        "once", "agent", "wake", False,
    )
    assert "等待新的思路" in first["prompt"] and "复核第二个方案" in first["prompt"]
    moved = book(store, delay_minutes=180, id=first["id"])
    assert moved["id"] == first["id"]
    assert len(store.schedules()) == len(builtins) + 2
    restarted = SchedulerStore(store.path)
    assert restarted.get_schedule(first["id"]) == moved
    assert restarted.settings() == settings
    assert [s for s in restarted.schedules() if s["id"].startswith("builtin-")] == builtins
    assert restarted.delete_schedule(first["id"])
    assert restarted.get_schedule(first["id"]) is None
    assert restarted.get_schedule(second["id"]) == second


@pytest.mark.parametrize("timing", [
    {}, {"run_at": "2030-01-03T09:00:00+09:00", "delay_minutes": 5},
    {"delay_minutes": 0}, {"delay_minutes": -1}, {"delay_minutes": True},
    {"delay_minutes": 1.5}, {"run_at": "2030-01-03T09:00:00"},
    {"run_at": "2030-01-01T09:00:00+09:00"},
    {"run_at": "2030-01-02T09:00:00+09:00"}, {"run_at": "invalid"},
])
def test_invalid_time_does_not_create_an_appointment(store, timing):
    before = store.schedules()
    with pytest.raises(ValueError):
        book(store, **timing)
    assert store.schedules() == before


@pytest.mark.parametrize("field", ["title", "reason", "continuation"])
def test_missing_handoff_does_not_create_an_appointment(store, field):
    values = dict(title="Research", reason="Review later", continuation="Compare both options")
    values[field] = "  "
    with pytest.raises(ValueError):
        store.schedule_wakeup(**values, delay_minutes=5)


@pytest.mark.parametrize("existing", [
    {"origin": "user"}, {"purpose": "task"}, {"notify": True},
    {"kind": "interval", "interval_minutes": 60},
])
def test_shortcut_cannot_convert_other_schedules(store, existing):
    saved = store.save_schedule({
        "due_at": "2030-01-03T09:00:00+09:00", "kind": "once",
        "purpose": "wake", "origin": "agent", "notify": False, **existing,
    })
    with pytest.raises(ValueError):
        book(store, delay_minutes=5, id=saved["id"])
    assert store.get_schedule(saved["id"]) == saved


def test_unknown_id_and_builtin_are_not_replaced(store):
    for schedule_id in ("missing", "builtin-interval", "builtin-planning"):
        with pytest.raises(ValueError):
            book(store, delay_minutes=5, id=schedule_id)
    assert len(store.schedules()) == 2


def test_old_delivery_does_not_disable_rearmed_appointment(tmp_path, monkeypatch):
    import nexus_scheduler.store as module
    now = datetime.fromisoformat("2030-01-02T09:00:00+09:00")
    monkeypatch.setattr(module, "now_local", lambda: now)
    delivered, rearmed = [], []
    runtime = None

    def callback(request):
        import json
        delivered.append(json.loads(request.content))
        rearmed.append(book(runtime.store, delay_minutes=20, id=old["id"]))
        return httpx.Response(202)

    runtime = SchedulerRuntime(tmp_path, transport=httpx.MockTransport(callback))
    try:
        runtime.connect("http://nexus.test/wake")
        runtime.store.configure({"enabled": True, "interval_enabled": False, "planning_enabled": False})
        old = book(runtime.store, delay_minutes=1)
        now += timedelta(minutes=2)
        runtime.tick(now.isoformat())
        assert runtime.store.get_schedule(old["id"]) == rearmed[0]
        reason = delivered[0]["reasons"][0]
        assert reason["schedule_id"] == old["id"]
        assert reason["scheduled_for"] == old["next_run_at"]
        assert reason["origin"] == "agent"
        assert reason["prompt"] == old["prompt"]
    finally:
        runtime.close()


def test_failed_delivery_remains_due_and_success_retires_once(tmp_path, monkeypatch):
    import nexus_scheduler.store as module
    now = datetime.fromisoformat("2030-01-02T09:00:00+09:00")
    monkeypatch.setattr(module, "now_local", lambda: now)
    status = 503
    runtime = SchedulerRuntime(tmp_path, transport=httpx.MockTransport(lambda _: httpx.Response(status)))
    try:
        runtime.connect("http://nexus.test/wake")
        runtime.store.configure({"enabled": True, "interval_enabled": False, "planning_enabled": False})
        saved = book(runtime.store, delay_minutes=1)
        now += timedelta(minutes=2)
        runtime.tick(now.isoformat())
        assert runtime.store.get_schedule(saved["id"]) == saved
        status = 202
        runtime.tick(now.isoformat())
        retired = runtime.store.get_schedule(saved["id"])
        assert not retired["enabled"] and retired["next_run_at"] is None
        assert book(runtime.store, delay_minutes=5, id=saved["id"])["enabled"]
    finally:
        runtime.close()
