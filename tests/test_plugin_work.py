"""Public work capability and packaged followup integration use temporary homes."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from test_task_runtime import call_frames, configure, text_frames

from nexus import application
from nexus.application import Application
from nexus.core import agent as loop
from nexus.plugins.api import PluginHost
from nexus.plugins.manager import Entry, PluginManager

ROOT = Path(__file__).resolve().parents[1]


def wait_until(test, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = test()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('condition timed out')


@pytest.fixture
def app(tmp_path, monkeypatch):
    config, _ = configure(tmp_path)
    monkeypatch.setattr(application, 'PluginManager', lambda state: PluginManager(state, root=tmp_path / 'plugins'))
    value = Application().start(home=config.home)
    yield value
    value.close()


def test_work_ownership_deduplication_silence_and_report(app, monkeypatch, tmp_path):
    calls = []
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        task = any(t['function']['name'] == 'task_finish' for t in body.get('tools', []))
        frames = call_frames('finish', 'task_finish', {'summary': 'observed v1'}) if task else text_frames('owner update')
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=frames)
    real = httpx.AsyncClient
    monkeypatch.setattr(loop, '_client', lambda: real(transport=httpx.MockTransport(respond)))
    manager = app.state['plugins']
    for key in ('test', 'other'):
        manager.entries[key] = Entry(key, tmp_path, {'id': key}, enabled=True, status='ready')
    host = PluginHost(manager, 'test')
    defaults = host.work.defaults()
    item = host.work.start(request_id='one', goal='observe', report=False, source={'arrangement_id': 'a'})
    wait_until(lambda: host.work.get(task_id=item['id'])['state'] == 'completed')
    app.inbox().collect_reports()
    assert len(calls) == 1
    assert host.work.start(request_id='one', goal='observe', report=False, source={'arrangement_id': 'a'})['id'] == item['id']
    with pytest.raises(ValueError, match='参数'):
        host.work.start(request_id='one', goal='different', report=False)
    with pytest.raises(ValueError, match='不属于'):
        PluginHost(manager, 'other').work.control(task_id=item['id'], operation='cancel', request_id='bad')
    host.work.report(task_id=item['id'], text='observed v1', request_id='report-one')
    host.work.report(task_id=item['id'], text='observed v1', request_id='report-one')
    wait_until(lambda: len(calls) == 2)
    wait_until(lambda: not app.inbox().running)
    session = app.state['sessions'].load(app.inbox().session_id)
    assert not any(m.kind == 'user' and 'observed v1' in m.text for m in session.items)
    reports = [m for m in session.items if m.kind == 'work_report']
    assert len(reports) == 1 and reports[0].data['report']['plugin_id'] == 'test'
    task = app.state['tasks'].get(item['id'])
    assert task.workspace == defaults['workspace'] and task.model_id == defaults['model_id']


def test_plugin_work_keeps_normal_approval_and_unload_stops_it(app, monkeypatch, tmp_path):
    app.state['store'].save_preferences(permission='ask')
    real = httpx.AsyncClient
    def respond(request):
        body = json.loads(request.content)
        task = any(t['function']['name'] == 'task_finish' for t in body.get('tools', []))
        frames = call_frames('write', 'write_file', {'path': 'needs-owner.txt', 'content': 'hello'}) if task else text_frames('approval needed')
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=frames)
    monkeypatch.setattr(loop, '_client', lambda: real(transport=httpx.MockTransport(respond)))
    manager = app.state['plugins']
    manager.entries['test'] = Entry('test', tmp_path, {'id': 'test'}, enabled=True, status='ready')
    host = PluginHost(manager, 'test')
    item = host.work.start(request_id='approval', goal='write a file', report=False)
    wait_until(lambda: app.state['tasks'].pending_interactions(item['id']))
    assert not (tmp_path / 'needs-owner.txt').exists()
    assert host.work.get(task_id=item['id'])['pending_interactions']
    manager.runtime.suspend('test')
    wait_until(lambda: app.state['tasks'].get(item['id']).state == 'paused')


def test_work_control_is_plugin_scoped_and_each_unload_pauses_again(app, monkeypatch, tmp_path):
    manager = app.state['plugins']
    store = app.state['tasks']
    # Keep work queued so lifecycle commands can be checked without a model or scheduler race.
    monkeypatch.setattr(app.state['task_controller'], 'submit',
                        lambda task_id, **kw: store.command(task_id, 'queued', **kw))
    for owner in ('first', 'second'):
        manager.entries[owner] = Entry(owner, tmp_path, {'id': owner}, enabled=True, status='ready')
        host = PluginHost(manager, owner)
        item = host.work.start(goal='observe', request_id='same-request', report=False)
        assert host.work.control(task_id=item['id'], operation='pause', request_id='same-control')['state'] == 'paused'
        for cycle in range(2):
            host.work.control(task_id=item['id'], operation='resume', request_id=f'resume-{cycle}')
            manager.runtime.suspend(owner)
            assert host.work.get(task_id=item['id'])['state'] == 'paused'


@pytest.mark.parametrize('language', ['python', 'node'])
def test_independent_sdk_clients_use_standard_work(app, monkeypatch, tmp_path, language):
    if language == 'node' and not shutil.which('node'):
        pytest.skip('Node.js unavailable')
    real = httpx.AsyncClient
    monkeypatch.setattr(loop, '_client', lambda: real(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                       text=call_frames('done', 'task_finish', {'summary': 'client result'})))))
    manager = app.state['plugins']
    owner = 'sdk-' + language
    manager.entries[owner] = Entry(owner, tmp_path, {'id': owner}, enabled=True, status='ready')
    env = {**os.environ, **manager.runtime.bridge.environment(owner)}
    if language == 'python':
        script = tmp_path / 'client.py'
        script.write_text('''import json
from nexus_host import HostClient, SDK_VERSION
host = HostClient()
host.capabilities.require(requires={'work': 1})
cursor = host.work.events()['cursor']
item = host.work.start(goal='Check the project', request_id='client', report=False, source={'kind': 'sdk-example'})
print(json.dumps({'version': SDK_VERSION, 'id': item['id'], 'cursor': cursor}))
''', encoding='utf-8')
        shutil.copyfile(ROOT / 'nexus-plugin-sdk/nexus_host.py', tmp_path / 'nexus_host.py')
        command = [sys.executable, str(script)]
    else:
        script = tmp_path / 'client.mjs'
        script.write_text('''import {HostClient, SDK_VERSION} from './nexus-host.mjs';
const host = new HostClient();
await host.capabilities.require({requires: {work: 1}});
const {cursor} = await host.work.events();
const item = await host.work.start({goal: 'Check the project', request_id: 'client', report: false, source: {kind: 'sdk-example'}});
console.log(JSON.stringify({version: SDK_VERSION, id: item.id, cursor}));
''', encoding='utf-8')
        shutil.copyfile(ROOT / 'nexus-plugin-sdk/nexus-host.mjs', tmp_path / 'nexus-host.mjs')
        command = ['node', str(script)]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20, check=True)
    data = json.loads(result.stdout)
    assert data['version'] == '1.2.0'
    host = PluginHost(manager, owner)
    wait_until(lambda: host.work.get(task_id=data['id'])['state'] == 'completed')
    item = host.work.get(task_id=data['id'], messages=True)
    assert item['plugin_id'] == owner and item['plugin_source'] == {'kind': 'sdk-example'}
    assert item['messages']
    assert any(e['task_id'] == item['id'] for e in host.work.events(after=data['cursor'])['events'])
    assert any(t['id'] == item['id'] for t in host.work.list())


def test_packaged_followup_stdio_background_and_host_page(app, monkeypatch, tmp_path):
    from nexus.plugins import environment
    from nexus.plugins.pages import validate_view
    monkeypatch.setattr(environment, 'prepare', lambda *a, **kw: Path(sys.executable))
    spec = importlib.util.spec_from_file_location('followup_packager', ROOT / 'nexus-plugin-sdk/build.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    source = tmp_path / 'followup-source'
    shutil.copytree(ROOT / 'plugin-dev/followup', source, ignore=shutil.ignore_patterns('__pycache__', '*.zip'))
    archive = builder.build(source)
    manager = app.state['plugins']
    manager.import_zip(archive.read_bytes())
    calls = []
    real = httpx.AsyncClient
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        task = any(t['function']['name'] == 'followup_finish' for t in body.get('tools', []))
        frames = call_frames('result', 'followup_finish', {'summary': 'stdio result', 'context': 'version 1',
                            'notify': False, 'finished': False}) if task else text_frames('baseline delivered')
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=frames)
    monkeypatch.setattr(loop, '_client', lambda: real(transport=httpx.MockTransport(respond)))
    manager.load('followup')
    wait_until(lambda: manager.entries['followup'].status != 'loading', 15)
    assert manager.entries['followup'].status == 'ready', manager.entries['followup'].error
    view = manager.page('followup', 'main', {'event': 'open'})
    validate_view(view)
    service = lambda action, **kw: manager.call_service('followup', 'worker', 'manage', {'action': action, 'arguments': kw})
    item = service('save', values={'title': 'stdio check', 'goal': 'Check version', 'trigger': {'kind': 'interval', 'seconds': 3600}})
    assert not calls
    service('control', arrangement_id=item['id'], operation='run')
    wait_until(lambda: any(r['status'] == 'completed' for r in service('read', arrangement_id=item['id'])['runs']), 15)
    wait_until(lambda: any('baseline delivered' == m.text for m in app.state['sessions'].load(app.inbox().session_id).items))
    assert len(calls) == 2
    result = service('read', arrangement_id=item['id'])
    assert result['arrangement']['last_result'] == 'version 1'
    service('control', arrangement_id=item['id'], operation='run')
    wait_until(lambda: len([r for r in service('read', arrangement_id=item['id'])['runs'] if r['status'] == 'completed']) == 2, 15)
    assert len(calls) == 3  # The second unchanged check does not wake the main conversation.
    view = manager.page('followup', 'main', {'event': 'action', 'action': 'history', 'arguments': {'id': item['id']}})
    validate_view(view)
    manager.operate('followup', 'unload')
    wait_until(lambda: manager.entries['followup'].status == 'unloaded')
