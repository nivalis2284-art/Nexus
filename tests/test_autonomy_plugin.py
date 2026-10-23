"""Focused checks for the autonomy plugin, without a model or live service."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "autonomy"


def load_plugin():
    entry = PLUGIN / "backend.py"
    name = "nexus_autonomy_plugin_test"
    spec = importlib.util.spec_from_file_location(
        name, entry, submodule_search_locations=[str(entry.parent)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def notification(notice_id: int, task_id: str, kind: str = "completed", **values):
    return {
        "id": notice_id,
        "task_id": task_id,
        "kind": kind,
        "title": "Task result",
        "text": "saved notice",
        "created_at": "2026-10-23T10:00:00+09:00",
        "event_type": values.pop("event_type", "task.control"),
        "current_state": values.pop("current_state", "completed"),
        "source": values.pop("source", "agent"),
        "work_summary": values.pop("work_summary", "The result is ready."),
        "wait_reason": values.pop("wait_reason", ""),
        **values,
    }


class FakeHost:
    def __init__(self, home: Path):
        self.home = home
        self.id = "autonomy"
        self.data_dir = home / "plugins" / "autonomy"
        self.data_dir.mkdir(parents=True)
        self.legacy_home = home
        self.settings = {}
        self.enabled = True
        self.base_url = "http://127.0.0.1:8766"
        self.event_url = self.base_url + "/api/plugin-events/autonomy"
        self.notices = []
        self.tasks = []
        self.emitted = []
        self.services = {}
        self.service_calls = {}
        self.service_handlers = {}
        self.commands = []
        self.subscriptions = []
        self.activity = None

    def subscribe(self, names):
        self.subscriptions.append(list(names))

    def connect_service(self, name, config):
        config = dict(config)
        prior = self.services.get(name) or {}
        handler = (self.service_handlers.get(name) or prior.get("handler")
                   or config.pop("handler", None))
        self.service_handlers[name] = handler
        config = {key: value for key, value in config.items() if key != "handler"}
        self.services[name] = {**config, "handler": handler}
        self.service_calls[name] = []
        return {"name": "plugin_autonomy_" + name}

    def call_service(self, name, tool, **args):
        self.service_calls.setdefault(name, []).append((tool, args))
        handler = self.service_handlers.get(name) or self.services[name].get("handler")
        if handler is None:
            raise AssertionError(f"missing fake service handler for {name}")
        return handler(tool, **args)

    def save_settings(self, values):
        self.settings = dict(values)
        return {"plugins": []}

    def query(self, topic, **args):
        if topic == "tasks.list":
            return [dict(value) for value in self.tasks]
        if topic == "tasks.get":
            task_id = args.get("id")
            return dict(next(value for value in self.tasks if value["id"] == task_id))
        if topic == "tasks.messages":
            return [{"kind": "notice", "text": "message body"}]
        if topic == "tasks.notifications":
            after = args.get("after", 0)
            limit = args.get("limit", 100)
            return [value for value in self.notices if value["id"] > after][:limit]
        if topic == "activity.runs":
            return self.runs
        if topic == "activity.current":
            return self.activity
        raise AssertionError(topic)

    def emit(self, payload):
        self.emitted.append(payload)
        return {"id": "event", "status": "pending"}

    def command(self, action, **args):
        self.commands.append((action, args))
        if action == "work.start":
            self.tasks.append({
                "id": "task-new", "title": args.get("title") or "new work",
                "goal": args.get("goal"), "state": "queued", "archived": False,
                "work_summary": "", "wait_reason": "", "steps": [],
                "evidence": [], "decisions": [],
            })
            return {"id": "task-new", "state": "queued"}
        if action == "work.control":
            return {"task_id": args["task_id"], "state": "paused"}
        if action == "task.pause":
            task = next(value for value in self.tasks if value["id"] == args["id"])
            task["state"] = "paused"
            return dict(task)
        if action == "task.hold":
            return {"held": bool(args.get("held", True))}
        if action == "work.list":
            return {"tasks": [
                {**task, "active": False, "queued": task.get("state") == "queued"}
                for task in self.tasks
            ]}
        if action == "work.read":
            task_id = args["task_id"]
            section = args.get("section", "summary")
            if section == "summary":
                return next(value for value in self.tasks if value["id"] == task_id)
            if section == "operations":
                return {"task_id": task_id, "section": "operations", "total": 1,
                        "items": [{"id": "op-a", "status": "unknown"}], "next_offset": None}
            return {"task_id": task_id, "section": section, "total": 0, "items": [], "next_offset": None}
        raise AssertionError(action)

    def complete_activity(self, reply="", summary=""):
        self.commands.append(("activity.finish", {"reply": reply, "summary": summary}))
        return {"finished": True}


@pytest.fixture
def environment(tmp_path):
    host = FakeHost(tmp_path)
    host.runs = []
    host.services["scheduler"] = {
        "handler": lambda tool, **args: {
            "settings": {
                "enabled": False, "interval_enabled": True, "interval_minutes": 60,
                "planning_enabled": True, "planning_time": "09:00",
                "watch_interval_minutes": 5, **(args.get("values") or {}),
            },
            "schedules": [{"id": "saved", "title": "Saved", "enabled": True}],
            "watches": [{"id": "watch", "url": "https://example.test"}],
            "error": "",
        }
    }
    module = load_plugin()
    plugin = module.create_plugin(host)
    plugin.start()
    return plugin, host


def test_start_exposes_only_scheduler_management_tools_and_keeps_new_automation_off(environment):
    _plugin, host = environment
    service = host.services["scheduler"]
    assert set(service["model_tools"]) == {
        "scheduler_state", "scheduler_configure", "schedule_save", "schedule_wakeup",
        "schedule_delete", "schedule_run", "watch_save", "watch_delete", "watch_check",
    }
    assert "scheduler_connect" not in service["model_tools"]
    assert "scheduler_import" not in service["model_tools"]
    assert service["args"][-2:] == ["--data", "{data}/scheduler"]
    assert host.settings["enabled"] is False
    assert host.subscriptions == [["task.notification", "task.owner_control", "activity.finished"]]


def test_feedback_uses_stable_ids_and_cursor_never_replays(environment):
    plugin, host = environment
    host.notices = [notification(4, "task-a")]
    plugin.runtime.on_event({"type": "task.notification", "data": host.notices[0]})
    assert host.emitted[0]["id"] == "work-feedback:4"
    assert host.emitted[0]["data"]["reasons"][0]["task_id"] == "task-a"
    plugin.runtime.catch_up_feedback()
    assert len(host.emitted) == 1
    assert plugin.runtime.store.feedback_cursor() == 4


def test_reported_completion_requires_reply_and_owner_controls_clear_pause_memory(environment):
    plugin, host = environment
    responsibility = plugin.runtime.save_responsibility(
        title="Maintenance", instruction="Watch the feed", origin="user"
    )
    plugin.runtime.store.link_work("task-a", responsibility_id=responsibility["id"], report_requested=True)
    plugin.runtime.store.remember_paused_tasks(responsibility["id"], ["task-a"])
    host.notices = [notification(5, "task-a")]
    plugin.runtime.catch_up_feedback()
    payload = host.emitted[-1]
    assert payload["reply_required"] is True
    assert payload["data"]["notify"] is True
    assert payload["data"]["reasons"][0]["report_requested"] is True
    plugin.runtime.on_event({"type": "task.owner_control", "data": {"task_id": "task-a"}})
    assert plugin.runtime.store.get_responsibility(responsibility["id"])["paused_task_ids"] == []


def test_work_start_strips_private_fields_and_links_the_resulting_task(environment):
    plugin, host = environment
    responsibility = plugin.runtime.save_responsibility(
        title="Maintenance", instruction="Watch the feed"
    )
    result = plugin.runtime.start_work(None, {
        "goal": "Investigate", "title": "Investigate", "responsibility_id": responsibility["id"],
        "report_requested": True,
    })
    assert host.commands == [
        ("work.start", {"goal": "Investigate", "title": "Investigate"})
    ]
    assert result["responsibility_id"] == responsibility["id"]
    assert result["report_requested"] is True
    assert plugin.runtime.store.work_metadata("task-new")["report_requested"] is True


def test_plain_work_needs_no_responsibility_and_control_forgets_links(environment):
    plugin, host = environment
    result = plugin.runtime.start_work(None, {"goal": "Ordinary work"})
    assert result["id"] == "task-new"
    assert result["responsibility_id"] is None
    plugin.runtime.control_work(None, {"task_id": "task-new", "operation": "resume"})
    assert host.commands[-1] == ("work.control", {"task_id": "task-new", "operation": "resume"})


def test_responsibility_pause_suspends_running_work_and_resume_does_not_dispatch(environment):
    plugin, host = environment
    host.tasks = [
        {"id": "task-a", "title": "Work", "state": "running", "archived": False},
    ]
    responsibility = plugin.runtime.save_responsibility(title="R", instruction="I")
    plugin.runtime.store.link_work("task-a", responsibility["id"])
    paused = plugin.runtime.control_responsibility(responsibility["id"], "pause")
    assert paused["state"] == "paused"
    assert paused["paused_task_ids"] == ["task-a"]
    assert ("task.pause", {"id": "task-a"}) in host.commands
    commands = list(host.commands)
    resumed = plugin.runtime.control_responsibility(responsibility["id"], "resume")
    assert resumed["state"] == "active"
    assert host.commands == commands + [("task.hold", {"id": "task-a", "held": False})]
    assert resumed["paused_task_ids"] == ["task-a"]
    assert not any(command[0] == "work.control" for command in host.commands)


def test_pause_holds_pending_scheduled_work_without_pausing_it(environment):
    plugin, host = environment
    host.tasks = [{"id": "pending-a", "title": "Scheduled", "state": "pending", "archived": False}]
    role = plugin.runtime.save_responsibility(title="R", instruction="I")
    plugin.runtime.store.link_work("pending-a", role["id"])
    paused = plugin.runtime.control_responsibility(role["id"], "pause")
    assert ("task.hold", {"id": "pending-a", "held": True}) in host.commands
    assert not any(command == ("task.pause", {"id": "pending-a"}) for command in host.commands)
    assert paused["paused_task_ids"] == []
    resumed = plugin.runtime.control_responsibility(role["id"], "resume")
    assert ("task.hold", {"id": "pending-a", "held": False}) in host.commands
    assert resumed["paused_task_ids"] == []


def test_context_has_rules_and_responsibilities_but_other_developers_stay_clean(environment):
    plugin, _host = environment
    materials = plugin.runtime.context({"kind": "ling"})
    assert {item["purpose"] for item in materials} == {"instructions", "reference"}
    assert any("自主活动规则" in item["content"] for item in materials)
    assert "automation_finish" not in json.dumps(materials, ensure_ascii=False)
    assert plugin.runtime.tools({"kind": "chat"}) == []
    assert plugin.runtime.tools({"kind": "task"}) == []
    assert plugin.runtime.tools({"kind": "ling"})


def test_context_prefers_activity_and_includes_reasons(environment):
    plugin, _host = environment
    activity = {
        "id": "activity-1", "type": "wake", "text": "", "reply_required": False,
        "data": {
            "kind": "wake", "prompt": "Revisit the plan", "notify": False,
            "reasons": [{"type": "planning", "title": "Daily"}],
        },
    }
    materials = plugin.runtime.context({"kind": "ling", "activity": activity})
    joined = json.dumps(materials, ensure_ascii=False)
    assert "Revisit the plan" in joined
    assert "daily review wake" in joined or "每日整体回顾" in joined
    assert "activity-1" in joined


def test_other_plugin_activity_does_not_claim_autonomy_protocol(environment):
    plugin, _host = environment
    activity = {
        "id": "foreign", "plugin_id": "other", "type": "event", "text": "foreign",
        "data": {"reasons": [{"type": "wake"}]},
    }
    materials = plugin.runtime.context({"kind": "ling", "activity": activity})
    joined = json.dumps(materials, ensure_ascii=False)
    assert "自主活动规则" not in joined
    assert "automation_finish" not in joined
    assert "长期交代" in joined
    names = {tool.name for tool in plugin.runtime.tools({"kind": "ling", "activity": activity})}
    assert "automation_finish" not in names
    assert {"work_list", "responsibility_list"} <= names


def test_page_snapshot_includes_settings_schedules_watches_runs_and_responsibilities(environment):
    plugin, host = environment
    host.runs = [{
        "id": "event-a", "external_id": "run-a", "type": "wake", "text": "",
        "data": {"kind": "wake", "prompt": "Review", "notify": False,
                 "reasons": [{"type": "planning"}],
                 "read_at": "2026-10-23T10:02:00+09:00"},
        "status": "done", "result": {"reply": "", "summary": "Reviewed."},
        "created_at": "2026-10-23T10:00:00+09:00", "error": "",
    }]
    plugin.runtime.save_responsibility(title="R", instruction="I")
    snapshot = plugin.runtime.call("snapshot", {})
    assert snapshot["settings"]["enabled"] is False
    assert snapshot["schedules"][0]["id"] == "saved"
    assert snapshot["watches"][0]["id"] == "watch"
    assert snapshot["runs"][0]["summary"] == "Reviewed."
    assert snapshot["runs"][0]["read_at"] == "2026-10-23T10:02:00+09:00"
    assert snapshot["responsibilities"][0]["title"] == "R"


def test_legacy_private_data_is_backed_up_and_migrated_once(tmp_path):
    legacy_task = tmp_path / "tasks.sqlite3"
    with sqlite3.connect(legacy_task) as db:
        db.executescript(
            """
            CREATE TABLE responsibilities (
              id TEXT PRIMARY KEY, title TEXT, instruction TEXT, state TEXT,
              origin TEXT, summary TEXT, paused_task_ids_json TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE work_metadata (
              task_id TEXT PRIMARY KEY, responsibility_id TEXT, report_requested INTEGER
            );
            INSERT INTO responsibilities VALUES (
              'old-r', 'Old', 'Instruction', 'active', 'user', '', '[]',
              '2026-01-01T00:00:00+09:00', '2026-01-01T00:00:00+09:00'
            );
            INSERT INTO work_metadata VALUES ('old-task', 'old-r', 1);
            """
        )
    legacy_automation = tmp_path / "automation.sqlite3"
    with sqlite3.connect(legacy_automation) as db:
        db.executescript(
            """
            CREATE TABLE feedback_state (id INTEGER PRIMARY KEY, notification_id INTEGER, updated_at TEXT);
            INSERT INTO feedback_state VALUES (1, 9, '2026-01-01T00:00:00+09:00');
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE schedules (id TEXT, weekdays TEXT, enabled INTEGER, notify INTEGER);
            CREATE TABLE watches (id TEXT, enabled INTEGER);
            INSERT INTO settings VALUES ('enabled', 'false');
            """
        )
    host = FakeHost(tmp_path / "home")
    host.runs = []
    host.services["scheduler"] = {"handler": lambda tool, **args: {"settings": {}, "schedules": [], "watches": [], "error": ""}}
    host.legacy_home = tmp_path
    plugin = load_plugin().create_plugin(host)
    plugin.start()
    assert plugin.runtime.store.get_responsibility("old-r")["title"] == "Old"
    assert plugin.runtime.store.work_metadata("old-task")["report_requested"] is True
    assert plugin.runtime.store.feedback_cursor() == 9
    assert (host.data_dir / "legacy-backup" / "tasks.sqlite3").is_file()


def test_scheduler_callback_has_stable_deduplicable_id():
    sys.path.insert(0, str(PLUGIN / "services" / "scheduler" / "src"))
    try:
        from nexus_scheduler.runtime import SchedulerRuntime
    finally:
        sys.path.pop(0)
    sent = []
    runtime = SchedulerRuntime(PLUGIN, transport=SimpleNamespace())
    runtime._post = lambda payload: sent.append(payload) or True
    reason = {"schedule_id": "a", "scheduled_for": "2030-01-01T09:00:00+09:00"}
    runtime._deliver([reason])
    runtime._deliver([reason])
    assert sent[0]["id"] == sent[1]["id"]
    assert sent[0]["data"]["reasons"] == [reason]
    assert sent[0]["type"] == "wake"


def test_manual_run_uses_a_fresh_id_while_due_occurrences_stay_stable():
    sys.path.insert(0, str(PLUGIN / "services" / "scheduler" / "src"))
    try:
        from nexus_scheduler.runtime import SchedulerRuntime
    finally:
        sys.path.pop(0)
    sent = []
    runtime = SchedulerRuntime(PLUGIN, transport=SimpleNamespace())
    runtime._post = lambda payload: sent.append(payload) or True
    reason = {"schedule_id": "a", "scheduled_for": "2030-01-01T09:00:00+09:00"}
    runtime._deliver([reason], kind="manual")
    runtime._deliver([reason], kind="manual")
    assert sent[0]["id"] != sent[1]["id"]
    assert sent[0]["type"] == sent[1]["type"] == "manual"


def test_work_tools_delegate_original_facts_then_add_metadata(environment):
    plugin, host = environment
    host.tasks = [{"id": "task-a", "title": "Work", "state": "paused", "archived": False}]
    responsibility = plugin.runtime.save_responsibility(title="R", instruction="I")
    plugin.runtime.store.link_work("task-a", responsibility["id"], report_requested=True)
    tools = {tool.name: tool for tool in plugin.runtime.tools({"kind": "ling"})}
    context = SimpleNamespace(work_service=SimpleNamespace())
    listed = tools["work_list"].handler(context, {})
    assert listed["tasks"][0]["active"] is False
    assert listed["tasks"][0]["responsibility_id"] == responsibility["id"]
    read = tools["work_read"].handler(context, {
        "task_id": "task-a", "section": "operations", "offset": 0, "limit": 20, "text_offset": 0,
    })
    assert read["items"][0]["id"] == "op-a"
    missing = plugin.runtime.read_task("task-a", section="summary")
    assert missing["id"] == "task-a"
    assert missing["responsibility_id"] == responsibility["id"]


def test_legacy_paused_tasks_restore_hold_without_pausing_again(tmp_path):
    legacy_task = tmp_path / "tasks.sqlite3"
    with sqlite3.connect(legacy_task) as db:
        db.executescript(
            """
            CREATE TABLE responsibilities (
              id TEXT PRIMARY KEY, title TEXT, instruction TEXT, state TEXT,
              origin TEXT, summary TEXT, paused_task_ids_json TEXT,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE work_metadata (
              task_id TEXT PRIMARY KEY, responsibility_id TEXT, report_requested INTEGER
            );
            INSERT INTO responsibilities VALUES (
              'old-r', 'Old', 'Instruction', 'paused', 'user', '', '["old-task"]',
              '2026-01-01T00:00:00+09:00', '2026-01-01T00:00:00+09:00'
            );
            INSERT INTO work_metadata VALUES ('old-task', 'old-r', 0);
            """
        )
    host = FakeHost(tmp_path / "home")
    host.runs = []
    host.legacy_home = tmp_path
    host.services["scheduler"] = {"handler": lambda tool, **args: {"settings": {}, "schedules": [], "watches": [], "error": ""}}
    plugin = load_plugin().create_plugin(host)
    plugin.start()
    assert ("task.hold", {"id": "old-task", "held": True}) in host.commands
    assert not any(command[0] == "task.pause" for command in host.commands)
    plugin.runtime.on_event({"type": "task.owner_control", "data": {"task_id": "old-task"}})
    assert plugin.runtime.store.get_responsibility("old-r")["paused_task_ids"] == []


def test_existing_plugin_scheduler_database_wins_over_old_automation_snapshot(tmp_path):
    home = tmp_path / "home"
    plugin_data = home / "plugins" / "autonomy"
    plugin_data.mkdir(parents=True)
    existing = plugin_data / "scheduler" / "scheduler.sqlite3"
    existing.parent.mkdir(parents=True)
    source = PLUGIN / "services" / "scheduler" / "src"
    sys.path.insert(0, str(source))
    try:
        from nexus_scheduler.store import SchedulerStore
    finally:
        sys.path.pop(0)
    store = SchedulerStore(existing)
    store.configure({"enabled": True, "interval_minutes": 19})
    store.save_schedule({
        "id": "newer", "kind": "once", "purpose": "wake",
        "due_at": "2031-02-03T04:05:06+09:00", "origin": "user",
    })
    old_automation = home / "automation.sqlite3"
    with sqlite3.connect(old_automation) as db:
        db.executescript(
            """
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE schedules (id TEXT, weekdays TEXT, enabled INTEGER, notify INTEGER);
            CREATE TABLE watches (id TEXT, enabled INTEGER);
            INSERT INTO settings VALUES ('enabled', 'true'), ('interval_minutes', '90');
            INSERT INTO schedules VALUES ('older', '[]', 1, 0);
            """
        )
    from plugins.autonomy.store import PluginStore

    plugin_store = PluginStore(plugin_data / "autonomy.sqlite3")
    result = plugin_store.migrate_legacy(
        home,
        scheduler_path=existing,
        notification_head=lambda: 0,
    )
    assert existing.is_file()
    assert result["legacy_rules"] is None
    assert SchedulerStore(existing).settings()["interval_minutes"] == 19
    assert SchedulerStore(existing).get_schedule("newer") is not None


def test_scheduler_enabled_change_is_authoritative_across_plugin_reload(tmp_path):
    host = FakeHost(tmp_path)
    host.runs = []
    host.settings = {"enabled": False}
    host.services["scheduler"] = {"handler": lambda tool, **args: {
        "settings": {"enabled": tool != "scheduler_configure" or bool((args.get("values") or {}).get("enabled"))},
        "schedules": [], "watches": [], "error": "",
    }}
    first = load_plugin().create_plugin(host)
    first.start()
    first.runtime.call_scheduler("scheduler_configure", values={"enabled": True})
    second = load_plugin().create_plugin(host)
    second.start()
    calls = host.service_calls["scheduler"]
    assert not any(
        tool == "scheduler_configure" and (args.get("values") or {}).get("enabled") is False
        for tool, args in calls
    )


def test_finishing_responsibility_releases_hold_without_dispatching(tmp_path):
    host = FakeHost(tmp_path)
    host.runs = []
    host.tasks = [{"id": "task-a", "title": "Work", "state": "paused", "archived": False}]
    host.services["scheduler"] = {"handler": lambda tool, **args: {"settings": {}, "schedules": [], "watches": [], "error": ""}}
    plugin = load_plugin().create_plugin(host)
    plugin.start()
    record = plugin.runtime.save_responsibility(title="R", instruction="I")
    plugin.runtime.store.link_work("task-a", record["id"])
    plugin.runtime.store.remember_paused_tasks(record["id"], ["task-a"])
    ended = plugin.runtime.control_responsibility(record["id"], "end")
    assert ended["state"] == "ended"
    assert ("task.hold", {"id": "task-a", "held": False}) in host.commands
    assert not any(command[0] in ("task.resume", "work.control") for command in host.commands)
    assert ended["paused_task_ids"] == ["task-a"]
    assert plugin.runtime.store.task_paused("task-a") is False
