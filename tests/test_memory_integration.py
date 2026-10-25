"""Actual stdio MCP + adapter + local scripted model; never uses daily data or credentials."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from nexus.mcp import MCPRegistry, ServerConfig
from plugins.memory.backend import MemoryPlugin


def test_static_memory_roundtrip_through_real_mcp_and_adapter(tmp_path):
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            value = json.loads(body['messages'][-1]['content'])
            if 'records' in value:
                refs = [record['source_ref'] for record in value['records']]
                answer = {'events': [{'title': '便条与回访', 'summary': '约定三十轮后回访便条。',
                                      'source_refs': refs, 'before': '未回访', 'after': '等待回访',
                                      'uncertainty': '尚未发生'}]}
            elif 'purpose' in value:
                answer = {'event_ids': [event['id'] for event in value['events']][:1],
                          'group_ids': [group['id'] for group in value['groups']][:1]}
            else:
                ids = [event['id'] for event in value['events']]
                answer = {'groups': [{'title': '便条记录', 'dimension': 'topic',
                                      'summary': '曾约定在三十轮后回访便条。', 'event_ids': ids}],
                          'associations': []}
            response = json.dumps({'choices': [{'message': {'content': json.dumps(answer)}}],
                                   'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    http = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    registry = MCPRegistry()

    class Host:
        data_dir = tmp_path
        settings: ClassVar[dict] = {'takeover': False, 'session_id': 's1', 'budget_tokens': 3000}
        records: ClassVar[list] = [{'kind': 'user', 'text': '便条应该什么时候回访？', 'at': 't1'},
                   {'kind': 'assistant', 'text': '三十轮后回访便条。', 'at': 't2'}]

        def connect_service(self, name, config):
            package = Path(__file__).resolve().parents[1] / 'services/persistent_memory/src'
            registry.add(ServerConfig(
                name='memory', command=sys.executable,
                args=['-m', 'persistent_memory', '--data', str(tmp_path / 'engine')],
                env={'PYTHONPATH': str(package)},
            ))

        def call_service(self, name, tool, **values):
            result = registry.servers['memory'].call_tool(tool, values)
            if not result.ok:
                raise ValueError(result.text)
            return (result.structured_content if result.structured_content is not None
                    else json.loads(result.text))

        def query(self, topic, **args):
            return self.records if topic == 'sessions.records' else {'session_id': 's1'}

        def save_settings(self, values):
            self.settings = values

        def background(self, callback):
            worker = threading.Thread(target=callback, daemon=True)
            worker.start()
            return worker

    host = Host()
    plugin = MemoryPlugin(host)
    try:
        plugin.start()
        plugin.call('configure', {'base_url': f'http://127.0.0.1:{http.server_port}/v1',
                                  'model': 'scripted'})
        plugin.call('import', {})
        assert plugin.call('build', {})['running']
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = plugin.call('status', {})
            if not status['job']['running']:
                break
            time.sleep(.02)
        assert not status['job']['running']
        assert not status['job']['error'], status['job']['error']
        version = status['job']['result']['id']
        plugin.call('activate', {'version_id': version})
        plugin.call('configure', {'takeover': True})
        host.records.append({'kind': 'user', 'text': '便条约定是什么？', 'at': 't3'})
        messages = [{'role': record['kind'], 'content': record['text'],
                     '_source': {'session_id': 's1', 'index': index}}
                    for index, record in enumerate(host.records)]
        prepared = plugin.context({'kind': 'ling', 'session_id': 's1', 'input_id': 1,
                                   'messages': messages})
        assert prepared['materials'][0]['status'] == 'memory', prepared
        assert prepared['messages'] == messages[2:]
        assert '三十轮' in prepared['materials'][0]['content']
        found = plugin.call('search', {'query': '便条'})
        event = found['events'][0]
        expanded = plugin.call('expand', {'kind': 'event', 'id': event['id']})
        source = plugin.call('expand', {'kind': 'source', 'id': expanded['source_ids'][0]})
        assert '便条' in str(source)
        assert plugin.call('runs', {})['runs'][0]['status'] == 'memory'
        assert 'api_key' not in plugin.call('export', {})['memory']['config']
    finally:
        registry.close()
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)
