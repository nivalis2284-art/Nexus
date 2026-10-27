"""Plugin lifecycle checks through actual HTTP."""
import io
import json
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

from nexus.core.config import ConfigStore
from nexus.model.request_history import RequestHistory
from nexus.plugins.manager import PluginManager
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
