"""Direct plugin chat calls, stream ownership, and lifecycle boundaries."""

from __future__ import annotations

import json
import queue
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from test_task_runtime import configure, text_frames

from nexus.application import Application
from nexus.core import agent as loop
from nexus.plugins.api import PluginHost
from nexus.plugins.manager import PluginManager
from nexus.runtime.stream import runtime_updates
from nexus.task import TaskConflict, TaskValidationError


class StubApplication:
    def __init__(self):
        self.snapshot_hook = lambda: None
        self.send_hook = lambda body: {'accepted': body}

    def chat_snapshot(self):
        self.snapshot_hook()
        return {'session_id': 'session', 'messages': []}

    def submit_input(self, body):
        return self.send_hook(body)


def make_manager(tmp_path, *, app=None, state=None, backend=None, plugin_ids=('alpha',)):
    home, root = tmp_path / 'host-home', tmp_path / 'plugin-root'
    home.mkdir()
    root.mkdir()
    for plugin_id in plugin_ids:
        path = root / plugin_id
        path.mkdir()
        manifest = {'id': plugin_id}
        if backend:
            manifest['entry'] = 'backend.py:create_plugin'
            (path / 'backend.py').write_text(backend, encoding='utf-8')
        (path / 'plugin.json').write_text(json.dumps(manifest), encoding='utf-8')
    if state is None:
        state = {'store': SimpleNamespace(home=home), 'mcp': SimpleNamespace(remove=lambda name: None),
                 'application': app or StubApplication()}
    return PluginManager(state, root=root)


def ready(manager, plugin_id='alpha', instance=None):
    item = manager.entries[plugin_id]
    item.enabled, item.status = True, 'ready'
    item.instance = instance or SimpleNamespace()
    return PluginHost(manager, plugin_id)


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return False


def test_direct_chat_uses_real_application_input_and_duplicate_handling(tmp_path, monkeypatch):
    config, _ = configure(tmp_path)
    seen = []

    def reply(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                              text=text_frames('plugin result'))

    monkeypatch.setattr(loop, '_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    app = Application().start(home=config.home)
    manager = make_manager(tmp_path, state=app.state)
    try:
        host = ready(manager)
        assert host.chat_api_version == 1
        snapshot = host.chat_snapshot()
        assert snapshot['session_id'] == app.inbox().session_id
        with host.chat_subscribe() as updates:
            accepted = host.chat_send('complete from plugin', 'plugin-input')
            duplicate = host.chat_send('complete from plugin', 'plugin-input')
            assert accepted['queued'] and duplicate['duplicate']
            assert accepted['input_id'] == duplicate['input_id']
            with pytest.raises(TaskConflict):
                host.chat_send('different input', 'plugin-input')
            with pytest.raises(TaskValidationError):
                host.chat_send('message', '')
            assert updates.get(timeout=2)['scope'] == 'ling'
            session_id = accepted['session_id']
            assert wait_until(lambda: any(item.text == 'plugin result' for item in
                                          app.state['sessions'].load(session_id).items))
        assert len(seen) == 1
    finally:
        manager.close()
        app.close()


def test_events_are_copied_for_each_plugin_and_shared_feed(tmp_path):
    manager = make_manager(tmp_path, plugin_ids=('alpha', 'beta'))
    try:
        alpha, beta = ready(manager), ready(manager, 'beta')
        feed = runtime_updates(manager.state)
        ordinary = feed.subscribe()
        with alpha.chat_subscribe() as first, beta.chat_subscribe() as second:
            with pytest.raises(queue.Empty):
                first.get(timeout=.01)
            payload = {'nested': ['original']}
            feed.publish('ling', 'session', {'type': 'context', 'data': payload})
            changed = first.get(timeout=1)
            changed['data']['nested'].append('mutated')
            changed['live']['context']['nested'].append('also mutated')
            assert second.get(timeout=1)['data'] == {'nested': ['original']}
            assert ordinary.get(timeout=1)['data'] == {'nested': ['original']}
            assert feed.live('ling', 'session')['context'] == {'nested': ['original']}
            assert payload == {'nested': ['original']}
        feed.unsubscribe(ordinary)
        assert not manager.entries['alpha'].chat_subscriptions
        assert not manager.entries['beta'].chat_subscriptions
    finally:
        manager.close()


def test_close_wakes_all_readers_is_idempotent_and_unregisters(tmp_path):
    manager = make_manager(tmp_path)
    try:
        host = ready(manager)
        feed = runtime_updates(manager.state)
        subscription = host.chat_subscribe()
        barrier = threading.Barrier(5)
        results = []

        def read():
            barrier.wait()
            results.append(subscription.get())

        workers = [threading.Thread(target=read) for _ in range(4)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=2)
        subscription.close()
        subscription.close()
        for worker in workers:
            worker.join(timeout=2)
            assert not worker.is_alive()
        assert results == [None] * 4
        assert subscription.get() is None
        assert not manager.entries['alpha'].chat_subscriptions
        assert not feed._subscribers
        for _ in range(50):
            host.chat_subscribe().close()
        assert not manager.entries['alpha'].chat_subscriptions
        assert not feed._subscribers
        closed_by_feed = host.chat_subscribe()
        feed.close()
        assert closed_by_feed.get() is None
        assert not manager.entries['alpha'].chat_subscriptions
    finally:
        manager.close()


def test_unload_closes_subscription_before_waiting_for_inflight_send(tmp_path):
    app = StubApplication()
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    def send(body):
        entered.set()
        assert release.wait(3)
        return body

    app.send_hook = send
    manager = make_manager(tmp_path, app=app)
    try:
        host = ready(manager, instance=SimpleNamespace(close=closed.set))
        subscription = host.chat_subscribe()
        results = []
        worker = threading.Thread(target=lambda: results.append(host.chat_send('hello', 'req')))
        worker.start()
        assert entered.wait(2)
        assert manager.entries['alpha'].active_calls == 1
        manager.operate('alpha', 'unload')
        assert subscription.get(timeout=1) is None
        assert manager.entries['alpha'].status == 'draining'
        assert not closed.is_set()
        for action in (host.chat_snapshot, lambda: host.chat_send('later', 'later'), host.chat_subscribe):
            with pytest.raises(ValueError):
                action()
        release.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert results == [{'text': 'hello', 'request_id': 'req'}]
        assert closed.wait(2)
        assert manager.entries['alpha'].status == 'unloaded'
    finally:
        release.set()
        manager.close()


def test_subscription_registration_cannot_race_unload(tmp_path, monkeypatch):
    manager = make_manager(tmp_path)
    acquired, proceed = threading.Event(), threading.Event()
    try:
        host = ready(manager)
        original_lease = manager.lease

        @contextmanager
        def pause_after_lease(*args, **kwargs):
            with original_lease(*args, **kwargs) as item:
                acquired.set()
                assert proceed.wait(3)
                yield item

        monkeypatch.setattr(manager, 'lease', pause_after_lease)
        errors = []

        def subscribe():
            try:
                host.chat_subscribe()
            except ValueError as error:
                errors.append(error)

        worker = threading.Thread(target=subscribe)
        worker.start()
        assert acquired.wait(2)
        manager.operate('alpha', 'unload')
        proceed.set()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert len(errors) == 1
        assert manager.entries['alpha'].status == 'unloaded'
        assert not manager.entries['alpha'].chat_subscriptions
        assert not runtime_updates(manager.state)._subscribers
    finally:
        proceed.set()
        manager.close()


def test_start_can_use_chat_and_restart_closes_old_subscription(tmp_path, monkeypatch):
    backend = '''
class Plugin:
    def __init__(self, host):
        self.host = host
    def start(self):
        self.subscription = self.host.chat_subscribe()
        self.snapshot = self.host.chat_snapshot()
        self.sent = self.host.chat_send('startup', 'startup-id')
    def close(self):
        assert self.subscription.get() is None
def create_plugin(host):
    return Plugin(host)
'''
    monkeypatch.setattr('nexus.plugins.environment.prepare', lambda *args, **kwargs: None)
    manager = make_manager(tmp_path, backend=backend)
    try:
        manager.load('alpha')
        item = manager.entries['alpha']
        assert wait_until(lambda: item.status == 'ready'), item.error
        old = item.instance.subscription
        assert item.instance.snapshot['session_id'] == 'session'
        assert item.instance.sent == {'accepted': {'text': 'startup', 'request_id': 'startup-id'}}
        assert item.active_calls == 0
        manager.operate('alpha', 'restart')
        assert old.get(timeout=1) is None
        assert wait_until(lambda: item.status == 'ready' and item.instance.subscription is not old), item.error
        new = item.instance.subscription
        manager.close()
        assert new.get(timeout=1) is None
    finally:
        manager.close()


def test_loading_failure_closes_subscription_and_resources(tmp_path, monkeypatch):
    backend = '''
class Plugin:
    def __init__(self, host):
        self.host = host
    def start(self):
        self.subscription = self.host.chat_subscribe()
        self.host.chat_snapshot()
        raise RuntimeError('startup failed')
    def close(self):
        assert self.subscription.get() is None
        (self.host.data_dir / 'closed').write_text('yes')
def create_plugin(host):
    return Plugin(host)
'''
    monkeypatch.setattr('nexus.plugins.environment.prepare', lambda *args, **kwargs: None)
    app = StubApplication()
    manager = make_manager(tmp_path, backend=backend, app=app)
    captured = []
    app.snapshot_hook = lambda: captured.extend(manager.entries['alpha'].chat_subscriptions)
    try:
        manager.load('alpha')
        item = manager.entries['alpha']
        assert wait_until(lambda: item.status == 'error'), item.error
        assert 'startup failed' in item.error
        assert captured[0].get(timeout=1) is None
        assert not item.chat_subscriptions
        assert manager.data_dir('alpha').joinpath('closed').read_text() == 'yes'
    finally:
        manager.close()


def test_unload_during_start_defers_close_until_start_returns(tmp_path, monkeypatch):
    backend = '''
class Plugin:
    def __init__(self, host):
        self.host = host
        self.started = False
    def start(self):
        self.subscription = self.host.chat_subscribe()
        self.host.chat_snapshot()
        self.started = True
    def close(self):
        assert self.started
        assert self.subscription.get() is None
        (self.host.data_dir / 'closed').write_text('yes')
def create_plugin(host):
    return Plugin(host)
'''
    monkeypatch.setattr('nexus.plugins.environment.prepare', lambda *args, **kwargs: None)
    entered, release = threading.Event(), threading.Event()
    app = StubApplication()

    def snapshot():
        entered.set()
        assert release.wait(3)

    app.snapshot_hook = snapshot
    manager = make_manager(tmp_path, backend=backend, app=app)
    try:
        manager.load('alpha')
        assert entered.wait(2)
        item = manager.entries['alpha']
        subscription = item.instance.subscription
        assert item.status == 'loading' and item.active_calls == 1
        manager.operate('alpha', 'unload')
        assert subscription.get(timeout=1) is None
        assert item.status == 'draining'
        assert not manager.data_dir('alpha').joinpath('closed').exists()
        release.set()
        assert wait_until(lambda: item.status == 'unloaded'), item.error
        assert manager.data_dir('alpha').joinpath('closed').read_text() == 'yes'
    finally:
        release.set()
        manager.close()


def test_host_close_wakes_factory_before_instance_assignment(tmp_path, monkeypatch):
    backend = '''
class Plugin:
    def __init__(self, host):
        self.host = host
    def close(self):
        (self.host.data_dir / 'closed').write_text('yes')
def create_plugin(host):
    subscription = host.chat_subscribe()
    host.chat_snapshot()
    assert subscription.get() is None
    return Plugin(host)
'''
    monkeypatch.setattr('nexus.plugins.environment.prepare', lambda *args, **kwargs: None)
    app = StubApplication()
    entered = threading.Event()
    app.snapshot_hook = entered.set
    manager = make_manager(tmp_path, backend=backend, app=app)
    try:
        manager.load('alpha')
        assert entered.wait(2)
        item = manager.entries['alpha']
        assert item.status == 'loading' and item.instance is None
        assert len(item.chat_subscriptions) == 1
        manager.close()
        assert wait_until(lambda: item.status == 'unloaded'), item.error
        assert not item.chat_subscriptions
        assert manager.data_dir('alpha').joinpath('closed').read_text() == 'yes'
    finally:
        manager.close()


def test_host_close_wakes_event_worker_before_join(tmp_path):
    manager = make_manager(tmp_path)
    host = ready(manager)
    subscription = host.chat_subscribe()
    entered = threading.Event()
    result = []

    def event_worker():
        with manager.lease('alpha'):
            entered.set()
            result.append(subscription.get())

    worker = threading.Thread(target=event_worker)
    manager._events_thread = worker
    worker.start()
    try:
        assert entered.wait(2)
        manager.close()
        assert not worker.is_alive()
        assert result == [None]
        assert manager.entries['alpha'].status == 'unloaded'
    finally:
        subscription.close()
        worker.join(timeout=2)
        manager.close()


def test_chat_calls_report_missing_application(tmp_path):
    manager = make_manager(tmp_path)
    try:
        host = ready(manager)
        manager.state.pop('application')
        for action in (host.chat_snapshot, lambda: host.chat_send('hello', 'id'), host.chat_subscribe):
            with pytest.raises(RuntimeError, match='application is not available'):
                action()
    finally:
        manager.close()
