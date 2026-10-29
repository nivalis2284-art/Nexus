"""Focused lifecycle tests for the plugin manager using temporary plugins."""

from __future__ import annotations

import io
import json
import time
import zipfile
from types import SimpleNamespace

import pytest

from nexus.plugins.api import PluginHost
from nexus.plugins.manager import PluginManager
from nexus.tools import build

BACKEND = r'''
from pathlib import Path

from nexus.plugins.api import EmptyArgs, ToolSpec


class DemoPlugin:
    def __init__(self, host):
        self.host = host
        self.seen_events = []

    def start(self):
        self.data = Path(self.host.data_dir)
        (self.data / "started.txt").write_text(self.host.id, encoding="utf-8")

    def context(self, scope):
        note = {"role": "user", "content": "temporary context from " + self.host.id}
        messages = [*scope.get("messages", []), note]
        return {
            "messages": messages,
            "materials": [{
                "purpose": "reference",
                "content": "Plugin saw " + str(scope.get("owner", "nobody")),
            }],
        }

    def tools(self, scope):
        return [ToolSpec(
            "demo_hello",
            "Return a greeting from the temporary plugin.",
            EmptyArgs,
            self.hello,
            read_only=True,
        )]

    def hello(self, context, arguments):
        Path(self.host.data_dir, "tool-used.txt").write_text("yes", encoding="utf-8")
        return {"content": "hello from plugin", "plugin_id": self.host.id}

    def on_event(self, event):
        self.seen_events.append(event["seq"])

    def close(self):
        Path(self.host.data_dir, "closed.txt").write_text("yes", encoding="utf-8")


def create_plugin(host):
    return DemoPlugin(host)
'''


class FakeMCP:
    def __init__(self, *, service_error: str = ""):
        self.service_error = service_error
        self.servers = {}
        self.removed = []

    def add(self, config, *, cwd=None):
        if self.service_error:
            raise RuntimeError(self.service_error)
        self.servers[config.name] = SimpleNamespace(name=config.name)
        return []

    def remove(self, name):
        self.removed.append(name)
        self.servers.pop(name, None)


def make_manager(tmp_path, *, mcp=None):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "plugin-root"
    root.mkdir()
    state = {"store": SimpleNamespace(home=home), "mcp": mcp or FakeMCP()}
    return PluginManager(state, root=root)


def plugin_zip(plugin_id="demo-plugin", *, services=None):
    manifest = {
        "id": plugin_id,
        "name": "Temporary demo",
        "version": "1.0",
        "entry": "backend.py:create_plugin",
        "pages": [{"id": "home", "path": "page.js"}],
        "settings_schema": {"theme": {"type": "string"}},
    }
    if services is not None:
        manifest["services"] = services
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("plugin.json", json.dumps(manifest))
        archive.writestr("backend.py", BACKEND)
        archive.writestr("page.js", "export default 'page';\n")
    return content.getvalue()


def load_sync(manager, plugin_id):
    """Load deterministically; the manager's public load uses a worker thread."""
    item = manager.entries[plugin_id]
    item.enabled, item.status, item.error = True, "loading", ""
    manager._save()
    manager._load(item)
    assert item.status == "ready", item.error
    return item


def wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_zip_lifecycle_pages_context_tools_cleanup_and_settings_restore(tmp_path):
    manager = make_manager(tmp_path)
    try:
        imported = manager.import_zip(plugin_zip())
        listed = next(item for item in imported["plugins"] if item["id"] == "demo-plugin")
        assert listed["pages"] == [{"id": "home", "path": "page.js"}]
        assert manager.asset("demo-plugin", "page.js").read_text(encoding="utf-8") == (
            "export default 'page';\n"
        )

        item = load_sync(manager, "demo-plugin")
        assert item.instance.host.data_dir.joinpath("started.txt").read_text(encoding="utf-8") == (
            "demo-plugin"
        )

        materials, reports = manager.context({"kind": "test", "owner": "alpha", "messages": []})
        assert materials == [{
            "purpose": "reference",
            "content": "Plugin saw alpha",
            "plugin_id": "demo-plugin",
            "source": "demo-plugin",
        }]
        assert reports == [{"id": "demo-plugin", "status": "used", "required": False}]

        registry, context = build(root=tmp_path / "workspace")
        manager.decorate(registry, context, {"kind": "test"})
        result = registry.execute("demo_hello", {}, context)
        assert result.ok, result.content
        assert result.content == "hello from plugin"
        assert result.data["plugin_id"] == "demo-plugin"
        assert (item.instance.host.data_dir / "tool-used.txt").read_text(encoding="utf-8") == "yes"

        manager.configure("demo-plugin", {"theme": "warm"})
        with manager.lease("demo-plugin"):
            manager.operate("demo-plugin", "unload")
            assert item.status == "draining"
            assert item.instance is not None
        assert item.status == "unloaded"
        assert item.instance is None
        data_dir = manager.data_dir("demo-plugin")
        assert (data_dir / "closed.txt").read_text(encoding="utf-8") == "yes"
        assert (data_dir / "started.txt").exists()

        source = item.path
        manager.operate("demo-plugin", "uninstall")
        assert "demo-plugin" not in manager.entries
        assert not source.exists()
        assert (data_dir / "started.txt").exists()

        manager.import_zip(plugin_zip())
        restored = manager.snapshot()["plugins"]
        restored = next(value for value in restored if value["id"] == "demo-plugin")
        assert restored["settings"] == {"theme": "warm"}
        assert restored["enabled"] is False
    finally:
        manager.close()


def test_zip_path_traversal_is_rejected_before_extraction(tmp_path):
    manager = make_manager(tmp_path)
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("plugin.json", json.dumps({"id": "escape-check", "name": "Bad"}))
        archive.writestr("../escaped.txt", "outside")
    try:
        with pytest.raises(ValueError, match="目录外"):
            manager.import_zip(content.getvalue())
        assert not (tmp_path / "escaped.txt").exists()
        assert not any((tmp_path / "plugin-root").glob(".import-*"))
    finally:
        manager.close()


def test_events_pause_on_unload_and_resume_on_reload(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip("pause-check"))
        load_sync(manager, "pause-check")
        pending = manager.receive("pause-check", {"id": "incoming-1", "type": "notice"})
        assert pending["status"] == "pending"

        manager.operate("pause-check", "unload")
        assert manager.events.get(pending["id"])["status"] == "paused"

        load_sync(manager, "pause-check")
        assert manager.events.get(pending["id"])["status"] == "pending"
        assert manager.events.pending(["pause-check"])["id"] == pending["id"]
    finally:
        manager.close()


def test_plugin_context_messages_are_disposable(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip("context-check"))
        load_sync(manager, "context-check")
        permanent = [{"role": "user", "content": "owner message"}]
        before = json.dumps(permanent)
        registry, tool_context = build(root=tmp_path / "workspace")
        agent = SimpleNamespace(
            registry=registry,
            context=tool_context,
            skills="",
            profile=None,
            plugin_scope={"kind": "test", "owner": "owner-1"},
        )

        prepared = manager.prepare_agent(agent, permanent)

        assert json.dumps(permanent) == before
        assert prepared[-1] == {
            "role": "user",
            "content": "temporary context from context-check",
        }
        assert agent.plugin_context.endswith("Plugin saw owner-1")
        assert registry.get("demo_hello") is not None
    finally:
        manager.close()


def test_service_failure_leaves_a_readable_host_snapshot(tmp_path):
    manager = make_manager(tmp_path, mcp=FakeMCP(service_error="temporary service unavailable"))
    try:
        manager.import_zip(plugin_zip("service-check", services=[{"name": "broken", "command": "nope"}]))
        item = manager.entries["service-check"]
        item.enabled, item.status = True, "loading"
        manager._load(item)

        assert item.status == "error"
        assert "temporary service unavailable" in item.error
        via_host = PluginHost(manager, "service-check").query("state")
        listed = next(value for value in via_host["plugins"]["plugins"]
                      if value["id"] == "service-check")
        assert listed["status"] == "error"
        assert "temporary service unavailable" in listed["error"]
        assert manager.snapshot()["plugins"][0]["id"] == "service-check"
    finally:
        manager.close()


def test_restart_keeps_enabled_and_a_new_manager_reads_it(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip("restart-check"))
        load_sync(manager, "restart-check")
        manager.operate("restart-check", "restart")
        assert wait_until(lambda: manager.entries["restart-check"].status == "ready"), (
            manager.entries["restart-check"].error
        )
        assert manager.entries["restart-check"].enabled is True

        reloaded = PluginManager(manager.state, root=manager.root)
        listed = next(value for value in reloaded.snapshot()["plugins"]
                      if value["id"] == "restart-check")
        assert listed["enabled"] is True
    finally:
        manager.close()


def test_subscription_cursor_does_not_redeliver_an_acknowledged_event(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip("event-check"))
        item = load_sync(manager, "event-check")
        item.instance.host.subscribe(["demo.changed"])
        manager.start("http://127.0.0.1:1")
        manager.publish("demo.changed", {"value": 1}, event_id="change-1")
        manager.publish("demo.other", {"value": 2}, event_id="change-2")

        assert wait_until(lambda: manager.events.changes("event-check") == [])
        assert item.instance.seen_events == [1]
        assert [change["id"] for change in manager.events.changes("event-check")] == []
    finally:
        manager.close()


def test_plugin_prompt_uses_owner_instructions(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip("prompt-check"))
        load_sync(manager, "prompt-check")
        from nexus.core import prompt
        seen = {}
        monkeypatch.setattr(prompt, "build_system_prompt",
                            lambda *args, **kwargs: seen.update(kwargs) or "")
        registry, tool_context = build(root=tmp_path / "workspace")
        agent = SimpleNamespace(
            registry=registry,
            context=tool_context,
            plugin_scope={"kind": "test"},
            skills="",
            profile=SimpleNamespace(context_window_tokens=8000, max_output_tokens=1000),
            context_limit=8000,
            workspace=None,
            role="role",
            owner_context="OWNER-INSTRUCTIONS",
            task_context="",
            interaction_context="",
        )
        manager.prepare_agent(agent, [{"role": "user", "content": "hi"}])
        assert seen["owner_instructions"] == "OWNER-INSTRUCTIONS"
        assert "persona" not in seen
    finally:
        manager.close()


def test_settings_remain_fixed_during_an_in_flight_call(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip("snapshot-check"))
        item = load_sync(manager, "snapshot-check")
        manager.configure("snapshot-check", {"profile": {"theme": "warm"}})
        seen = []

        def change_during_call(context, arguments):
            seen.append(item.instance.host.settings)
            manager.configure("snapshot-check", {"profile": {"theme": "cool"}})
            seen.append(item.instance.host.settings)
            return {"content": "done"}

        with manager.lease("snapshot-check", scope={"kind": "test"}):
            change_during_call(None, {})
        assert seen == [{"profile": {"theme": "warm"}}, {"profile": {"theme": "warm"}}]
        assert manager.entries["snapshot-check"].settings == {"profile": {"theme": "cool"}}
    finally:
        manager.close()


def test_stop_without_active_work_is_reported(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip("stop-check"))
        load_sync(manager, "stop-check")
        with pytest.raises(ValueError, match="没有可停止"):
            manager.operate("stop-check", "stop")
    finally:
        manager.close()


def test_custom_hooks_capture_independent_payloads_and_cleanup(tmp_path):
    manager = make_manager(tmp_path)
    try:
        for plugin_id in ('source', 'listener'):
            manager.import_zip(plugin_zip(plugin_id))
        listener = load_sync(manager, 'listener')
        seen = []
        listener.instance.host.on_hook('plugin.source.records.created',
            lambda payload, scope: seen.append((payload, scope)))
        pending = next(h for h in manager.hooks.snapshot()['hooks'] if h['name'].startswith('plugin.'))
        assert pending['available'] is False
        backend = manager.entries['source'].path / 'backend.py'
        backend.write_text('''
class Plugin:
    def __init__(self, host): self.host = host
    def start(self): self.name = self.host.define_hook('records.created', '记录已创建')
def create_plugin(host): return Plugin(host)
''', encoding='utf-8')
        source = load_sync(manager, 'source')
        name = source.instance.name
        original = {'ids': ['one']}
        with manager.lease('source', scope={'task_id': 't1'}):
            reports = source.instance.host.emit_hook(name, original)
        assert reports == [{'plugin_id': 'listener', 'key': 'default', 'ok': True}]
        assert seen == [(original, {'task_id': 't1'})]
        seen[0][0]['ids'].append('changed')
        assert original == {'ids': ['one']}
        with pytest.raises(ValueError, match='自己声明'):
            listener.instance.host.emit_hook(name, original)
        listener.instance.host.off_hook(name)
        assert source.instance.host.emit_hook(name, original) == []
        listener.instance.host.on_hook(name, lambda payload, scope: None)
        manager.operate('source', 'unload')
        pending = next(h for h in manager.hooks.snapshot()['hooks'] if h['name'] == name)
        assert pending['available'] is False
        manager.operate('listener', 'unload')
        assert all(h['name'] != name for h in manager.hooks.snapshot()['hooks'])
    finally:
        manager.close()


def test_hook_preferences_survive_restart_and_errors_do_not_block_observers(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip())
        item = load_sync(manager, 'demo-plugin')
        calls = []

        def broken(payload, scope):
            raise RuntimeError('capture failed')

        item.instance.host.on_hook('host.after_tool', broken, key='a')
        item.instance.host.on_hook('host.after_tool', lambda payload, scope: calls.append(payload), key='b')
        manager.hook('after_tool', {'kind': 'test'}, name='read', args={}, result={})
        hook = next(h for h in manager.hooks.snapshot()['hooks'] if h['name'] == 'host.after_tool')
        assert 'capture failed' in hook['subscribers'][0]['error']
        assert len(calls) == 1
        manager.hooks.configure('host.after_tool', False, 'demo-plugin', 'a')
        manager.hooks.configure('host.after_tool', False)
        manager.hook('after_tool', {}, name='read')
        assert len(calls) == 1
        state, root = manager.state, manager.root
        manager.close()
        manager = PluginManager(state, root=root)
        item = load_sync(manager, 'demo-plugin')
        item.instance.host.on_hook('host.after_tool', broken, key='a')
        item.instance.host.on_hook('host.after_tool', lambda payload, scope: calls.append(payload), key='b')
        hook = next(h for h in manager.hooks.snapshot()['hooks'] if h['name'] == 'host.after_tool')
        assert not hook['enabled'] and not hook['subscribers'][0]['enabled']
        manager.hooks.configure('host.after_tool', True)
        manager.hook('after_tool', {}, name='read')
        assert len(calls) == 2
        assert not next(h for h in manager.hooks.snapshot()['hooks']
                        if h['name'] == 'host.after_tool')['subscribers'][0]['error']
    finally:
        manager.close()


def test_tool_hooks_include_legacy_callbacks_and_preserve_required_failure(tmp_path):
    manager = make_manager(tmp_path)
    try:
        manager.import_zip(plugin_zip())
        item = manager.entries['demo-plugin']
        item.manifest['tool_hook_required'] = True
        backend = BACKEND.replace('    def close(self):', '''    def before_tool(self, scope, **values):
        raise RuntimeError('required capture failed')

    def close(self):''')
        (item.path / 'backend.py').write_text(backend, encoding='utf-8')
        load_sync(manager, 'demo-plugin')
        registry, context = build(root=tmp_path / 'workspace')
        manager.decorate(registry, context, {'kind': 'test'})
        result = registry.execute('demo_hello', {}, context)
        assert not result.ok and 'required capture failed' in result.content
        assert not (manager.data_dir(item.id) / 'tool-used.txt').exists()
        manager.hooks.configure('host.before_tool', False, item.id, 'legacy')
        seen = []
        item.instance.host.on_hook('host.after_tool', lambda payload, scope: seen.append(payload))
        result = registry.execute('demo_hello', {}, context)
        assert result.ok and seen[0]['name'] == 'demo_hello'
        assert seen[0]['result']['plugin_id'] == item.id
    finally:
        manager.close()


def test_unload_waits_for_active_hook_and_removes_new_deliveries(tmp_path):
    import threading

    manager = make_manager(tmp_path)
    entered, release = threading.Event(), threading.Event()
    worker = None
    try:
        manager.import_zip(plugin_zip())
        item = load_sync(manager, 'demo-plugin')

        def capture(payload, scope):
            entered.set()
            assert release.wait(3)

        item.instance.host.on_hook('host.before_tool', capture)
        worker = threading.Thread(target=lambda: manager.hook('before_tool', {}, name='read'))
        worker.start()
        assert entered.wait(3)
        manager.operate(item.id, 'unload')
        assert item.status == 'draining' and item.instance is not None
        assert manager.hooks.emit('host.before_tool', {}) == []
        release.set()
        worker.join(3)
        assert not worker.is_alive() and item.status == 'unloaded'
    finally:
        release.set()
        if worker:
            worker.join(3)
        manager.close()
