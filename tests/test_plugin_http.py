"""First-stage checks through actual HTTP and the common activity queue."""
import io
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from nexus.core.config import ConfigStore
from nexus.model.request_history import RequestHistory
from nexus.plugins.activity import ActivityRuntime
from nexus.plugins.manager import PluginManager
from nexus.session import SessionStore
from nexus.web import server as web


def bundle():
    content = io.BytesIO()
    with zipfile.ZipFile(content, 'w') as archive:
        archive.writestr('plugin.json', json.dumps({'id': 'check', 'name': 'Check', 'api_version': 1,
            'version': '1', 'entry': 'backend.py:create_plugin',
            'pages': [{'id': 'home', 'path': 'web/index.html'}]}))
        archive.writestr('backend.py', '''
class Plugin:
    def __init__(self,host): self.host=host
    def call(self,action,values): return {'action':action,'values':values}
def create_plugin(host): return Plugin(host)
''')
        archive.writestr('web/index.html', '<p>plugin page</p>')
    return content.getvalue()


def request(base, path, method='GET', body=None, raw=False):
    data = body if raw else json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
        headers={'Content-Type': 'application/zip' if raw else 'application/json'})
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        content = response.read()
        return response.status, (json.loads(content) if response.headers.get_content_type() == 'application/json' else content)


def test_import_load_configure_page_events_requests_uninstall_over_http(tmp_path, monkeypatch):
    config = ConfigStore(tmp_path / 'home')
    config.ensure()
    state = {'store': config, 'mcp': SimpleNamespace(tools={}, servers={})}
    manager = PluginManager(state, root=tmp_path / 'plugins')
    history = RequestHistory(config.home / 'requests.sqlite3')
    state.update(plugins=manager, request_history=history)
    monkeypatch.setattr(web, 'STATE', state)
    http = ThreadingHTTPServer(('127.0.0.1', 0), web.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{http.server_port}'
    try:
        status, snapshot = request(base, '/api/plugins/import', 'POST', bundle(), raw=True)
        assert status == 200 and not snapshot['plugins'][0]['enabled']
        assert request(base, '/api/plugins/import', 'POST', bundle(), raw=True)[0] == 409
        assert request(base, '/api/plugins/check/settings', 'PUT', {'choice': 'kept'})[0] == 200
        assert request(base, '/api/plugins/check/commands', 'POST', {'operation': 'load'})[0] == 200
        deadline = time.monotonic() + 3
        while manager.entries['check'].status == 'loading' and time.monotonic() < deadline:
            time.sleep(.02)
        assert manager.entries['check'].status == 'ready', manager.entries['check'].error
        status, value = request(base, '/api/plugins/check/call', 'POST', {'action': 'echo', 'values': {'x': 1}})
        assert status == 200 and value == {'action': 'echo', 'values': {'x': 1}}
        status, value = request(base, '/api/plugins/check/commands', 'POST', {'operation': 'stop'})
        assert status == 400 and '没有可停止' in value['error']
        assert request(base, '/plugins/check/web/index.html') == (200, b'<p>plugin page</p>')
        status, event = request(base, '/api/plugin-events/check', 'POST', {'id': 'e1', 'text': 'hello'})
        assert status == 202 and event['accepted'] and event['status'] == 'pending'
        assert request(base, '/api/plugin-events/' + event['id'])[1]['text'] == 'hello'
        record = history.record({'messages': [{'role': 'user', 'content': 'real body'}]},
            {'kind': 'chat', 'owner_id': 'owner', 'materials': [{'source': 'check'}]}, 'model', 'openai_chat_completions')
        assert request(base, '/api/model-requests?owner_id=owner')[1]['requests'][0]['id'] == record
        assert request(base, f'/api/model-requests/{record}')[1]['materials'] == [{'source': 'check'}]
        assert request(base, '/api/plugins/check/commands', 'POST', {'operation': 'uninstall'})[0] == 200
        assert not manager.root.joinpath('check').exists()
        assert manager.events.get(event['id'])['status'] == 'paused'
        assert request(base, '/api/plugins/import', 'POST', bundle(), raw=True)[1]['plugins'][0]['settings'] == {'choice': 'kept'}
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)
        manager.close()


def test_activity_finishes_through_common_runtime_and_survives_reopen(tmp_path):
    home = tmp_path / 'home'
    config = ConfigStore(home)
    config.ensure()
    sessions = SessionStore(config.sessions_dir)
    session = sessions.create('model')
    database = home / 'tasks.sqlite3'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE ling_inbox(status TEXT)')
    class Tasks:
        def _connect(self):
            return sqlite3.connect(database)
    state = {'store': config, 'sessions': sessions}
    manager = PluginManager(state, root=tmp_path / 'plugins')
    manager.import_zip(bundle())
    entry = manager.entries['check']
    entry.enabled, entry.status = True, 'loading'
    manager._load(entry)
    manager._save()
    inbox = SimpleNamespace(store=Tasks(), running=False, session_id=session.id,
                            interaction=None, live={}, emit=lambda value: None)
    def run(session, emit, inbox, event):
        runtime.finish('check', summary='one saved experience')
        emit({'type': 'done', 'data': {'stopped': 'cancelled'}})
    runtime = ActivityRuntime(manager, threading.Lock(), lambda: inbox, run)
    manager.runtime = runtime
    event = manager.receive('check', {'id': 'stable', 'text': 'new event'})
    runtime.tick()
    assert manager.events.get(event['id'])['status'] == 'done'
    assert sessions.load(session.id).items[-1].text == 'one saved experience'
    assert manager.receive('check', {'id': 'stable', 'text': 'duplicate'})['status'] == 'done'
    manager.close()
    reopened = PluginManager(state, root=manager.root)
    assert reopened.entries['check'].enabled
    assert reopened.events.get(event['id'])['status'] == 'done'


def test_existing_activity_migration_preserves_results_and_does_not_replay(tmp_path):
    config = ConfigStore(tmp_path / 'home')
    config.ensure()
    source = config.home / 'automation.sqlite3'
    with sqlite3.connect(source) as db:
        db.execute('''CREATE TABLE runs (id TEXT,status TEXT,reasons TEXT,prompt TEXT,
            notify INTEGER,kind TEXT,summary TEXT,reply TEXT,error TEXT,created_at TEXT,read_at TEXT)''')
        for event_id, status, reply, error in [('finished', 'done', 'old reply', ''),
                                                ('waiting', 'pending', '', None),
                                                ('in-progress', 'running', '', '')]:
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (event_id, status, '[{"type":"task"}]', 'original instruction', 1, 'task',
                        '', reply, error, '2026-10-23T01:00:00+00:00', '2026-10-23T02:00:00+00:00'))
    manager = PluginManager({'store': config}, root=tmp_path / 'plugins')
    manager._migrate_events()
    manager._migrate_events()
    values = {event['external_id']: event for event in manager.events.list('autonomy')}
    assert len(values) == 3
    assert values['finished']['status'] == 'done'
    assert values['finished']['result']['reply'] == 'old reply'
    assert values['finished']['data']['read_at'] == '2026-10-23T02:00:00+00:00'
    assert values['waiting']['status'] == 'pending' and values['waiting']['reply_required']
    assert values['waiting']['error'] == ''
    assert values['in-progress']['status'] == 'interrupted'
    assert (config.home / 'plugins-migration-backup' / 'automation.sqlite3').exists()
    with sqlite3.connect(source) as db:
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 3


def test_uninstall_does_not_release_a_paused_task_without_owner_control(tmp_path):
    config = ConfigStore(tmp_path / 'home')
    config.ensure()
    manager = PluginManager({'store': config}, root=tmp_path / 'plugins')
    manager.import_zip(bundle())
    manager.command('check', 'task.hold', id='task-a', held=True)
    manager.operate('check', 'uninstall')
    assert not manager.can_schedule('task-a')
    manager.release_task('task-a')
    assert manager.can_schedule('task-a')


def test_manager_restores_saved_pause_hold_before_scheduler_runs(tmp_path):
    home = tmp_path / 'home'
    config = ConfigStore(home)
    config.ensure()
    with sqlite3.connect(home / 'tasks.sqlite3') as db:
        db.executescript('''
            CREATE TABLE responsibilities (id TEXT PRIMARY KEY, state TEXT);
            CREATE TABLE work_metadata (task_id TEXT PRIMARY KEY, responsibility_id TEXT);
            INSERT INTO responsibilities VALUES ('old-role', 'paused');
            INSERT INTO work_metadata VALUES ('old-task', 'old-role');
        ''')
    state = {'store': config, 'mcp': SimpleNamespace(tools={}, servers={})}
    manager = PluginManager(state, root=tmp_path / 'plugins')
    try:
        assert not manager.can_schedule('old-task')
    finally:
        manager.close()


def test_manager_restores_existing_plugin_pause_hold_before_scheduler_runs(tmp_path):
    config = ConfigStore(tmp_path / 'home')
    config.ensure()
    plugin_data = config.home / 'plugins' / 'autonomy'
    plugin_data.mkdir(parents=True)
    with sqlite3.connect(plugin_data / 'autonomy.sqlite3') as db:
        db.executescript('''
            CREATE TABLE responsibilities (id TEXT PRIMARY KEY, state TEXT);
            CREATE TABLE work_metadata (task_id TEXT PRIMARY KEY, responsibility_id TEXT);
            INSERT INTO responsibilities VALUES ('old-role', 'paused');
            INSERT INTO work_metadata VALUES ('existing-task', 'old-role');
        ''')
    manager = PluginManager({'store': config}, root=tmp_path / 'plugins')
    try:
        assert not manager.can_schedule('existing-task')
    finally:
        manager.close()
