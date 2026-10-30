"""Focused checks using temporary data; never installs into Nexus."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'memory'
sys.path.insert(0, str(ROOT))
from engine import Engine
from store import Store
from models import now
from provider import Provider


def message(index, text, kind='user', **extra):
    key = {'user': 'ling_input_id', 'assistant': 'message_id', 'tool': 'tool_call_id'}[kind]
    return {'kind': kind, 'text': text, 'at': '2026-10-29T12:00:00Z', key: index, **extra}


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.fail = False

    def chat(self, settings, secrets, instruction, material):
        self.calls.append(material)
        if self.fail:
            raise ValueError('model unavailable')
        if 'records' in material:
            return {'units': [{'primary_segments': [material['records'][0]['segment_id']], 'context_segments': [r['segment_id'] for r in material['records'][1:]]}], 'no_memory': False}
        return {'particles': [{'unit_index': 0, 'content': '用户确定只保留主题级结论，原文可按需展开。', 'event_time': {'start': '2026-10-01T00:00', 'end': '2026-10-31T23:59'}}], 'needs_context': False}

    def embed(self, settings, secrets, texts):
        return [[1., 0., 0.] if '主题' in text else [0., 1., 0.] for text in texts]


def prepared(tmp_path):
    provider = FakeProvider()
    engine = Engine(tmp_path, provider)
    engine.save_settings({'llm_url': 'https://example.test/v1', 'llm_model': 'test', 'embedding_url': 'https://example.test/v1', 'embedding_model': 'test', 'process_mode': 'off'})
    engine.collect('s1', [message(1, 'existing')])
    engine.collect('s1', [message(1, 'existing'), message(2, '只保留主题级结论'), message(3, '原文仍然需要展开')])
    return engine, provider


def test_baseline_and_atomic_dedup_and_restart(tmp_path):
    engine, provider = prepared(tmp_path)
    assert engine.store.status()['particles'] == 0
    assert json.loads(engine.store.batch()['records'])[0]['text'] == '只保留主题级结论'
    assert engine.collect('s1', [message(2, '只保留主题级结论')])['captured'] == 0
    engine.process(engine.store.batch())
    assert engine.store.batch() is None
    row = engine.store.db.execute('SELECT records,selection FROM batches').fetchone()
    assert tuple(row) == (None, None)
    restored = Store(tmp_path)
    assert restored.ingest('s1', [message(2, '只保留主题级结论')])['captured'] == 0
    assert restored.status()['particles'] == 1
    assert len(provider.calls) == 2


def test_originals_survive_context_deletion_and_edit_and_vector_lifecycle(tmp_path):
    engine, provider = prepared(tmp_path)
    engine.process(engine.store.batch())
    p = engine.store.list()['particles'][0]
    original = engine.store.expand(p['id'])
    assert [r['text'] for r in original['segments']] == ['只保留主题级结论', '原文仍然需要展开']
    engine.collect('s1', [])
    engine.embed_pending()
    assert engine.search('主题')['particles'][0]['id'] == p['id']
    edited = engine.store.edit(p['id'], '新主题：保留原文，不自动删除。', None)
    assert edited['manually_edited'] and edited['id'] == p['id']
    assert engine.search('主题')['particles'] == []
    assert engine.store.list(query='新主题')['total'] == 1
    assert engine.store.expand(p['id']) == original
    engine.embed_pending()
    assert engine.search('主题')['particles'][0]['content'].startswith('新主题')
    engine.store.delete(p['id'])
    assert engine.store.cleanup()['unreferenced_originals'] == 1
    assert engine.store.cleanup(True)['cleaned']
    assert engine.store.db.execute('SELECT count(*) FROM segments').fetchone()[0] == 0


def test_retry_reuses_selection_and_successful_batch_is_idempotent(tmp_path):
    engine, provider = prepared(tmp_path)
    batch = engine.store.batch()
    provider.fail = True
    engine.start_job()
    engine.worker.join(3)
    failed = engine.store.status()['batches'][0]
    assert failed['status'] == 'failed'
    assert engine.store.db.execute('SELECT records FROM batches').fetchone()[0]
    provider.fail = False
    engine.retry(failed['id'])
    engine.worker.join(3)
    assert engine.store.status()['particles'] == 1
    engine.store.commit(batch['id'], [], [])
    assert engine.store.status()['particles'] == 1


def test_incomplete_tool_exchange_is_not_captured_until_result_saved(tmp_path):
    store = Store(tmp_path)
    store.ingest('s', [])
    assistant = message('a', '', 'assistant', tool_calls=[{'id': 'call1', 'name': 'read', 'arguments': '{}'}])
    assert store.ingest('s', [assistant])['captured'] == 0
    assert store.ingest('s', [assistant, message('call1', 'output', 'tool')])['captured'] == 2


def test_batch_splitting_preserves_exchanges(tmp_path):
    engine = Engine(tmp_path, FakeProvider())
    engine.save_settings({'batch_chars': 900, 'process_mode': 'off'})
    engine.collect('s', [])
    records = [message(1, 'a'*300), message('a', '', 'assistant', tool_calls=[{'id': 'c', 'name': 'read'}]), message('c', 'b'*100, 'tool')]
    engine.collect('s', records)
    engine.process(engine.store.batch())
    batches = engine.store.db.execute('SELECT records FROM batches').fetchall()
    assert len(batches) == 2
    assert [r['kind'] for r in json.loads(batches[1][0])] == ['assistant', 'tool']


def test_bad_selection_and_time_never_commit(tmp_path):
    engine, provider = prepared(tmp_path)
    provider.chat = lambda *args: {'units': [{'primary_segments': ['not-real'], 'context_segments': []}], 'no_memory': False}
    with pytest.raises(ValueError, match='引用不存在'):
        engine.process(engine.store.batch())
    assert engine.store.status()['particles'] == 0


def test_credentials_and_embedding_configuration_change(tmp_path):
    engine, provider = prepared(tmp_path)
    settings = engine.save_settings({'llm_key': 'secret-value', 'embedding_key': 'other-secret'})
    assert settings['has_llm_key'] and 'llm_key' not in settings
    assert 'secret-value' not in json.dumps(engine.store.status())
    engine.process(engine.store.batch())
    engine.embed_pending()
    assert engine.store.getmeta('index')
    engine.save_settings({'embedding_model': 'new-model'})
    assert engine.store.getmeta('index') is None
    assert engine.store.list()['particles'][0]['embedding_status'] == 'pending'


def test_real_http_provider_protocol(tmp_path):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, body))
            result = {'choices': [{'message': {'content': '{"ok":true}'}}]} if self.path.endswith('completions') else {'data': [{'index': 0, 'embedding': [3, 4]}]}
            data = json.dumps(result).encode()
            self.send_response(200); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self, *args): pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True);thread.start()
    try:
        s = {'llm_url': f'http://127.0.0.1:{http.server_port}/v1', 'embedding_url': f'http://127.0.0.1:{http.server_port}/v1', 'llm_model': 'test', 'embedding_model': 'embed'}
        p = Provider()
        assert p.chat(s, {}, 'instruction', {}) == {'ok': True}
        assert p.embed(s, {}, ['x']) == [[.6, .8]]
        assert [r[0] for r in received] == ['/v1/chat/completions', '/v1/embeddings']
    finally:
        http.shutdown();http.server_close();thread.join(2)


def test_real_mcp_stdio(tmp_path):
    import asyncio
    from mcp import ClientSession, StdioServerParameters, stdio_client
    async def run():
        async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(ROOT / 'service.py'), '--data', str(tmp_path)])) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert {'memory_search', 'memory_get', 'memory_expand'} <= {t.name for t in tools.tools}
                result = await client.call_tool('memory_admin', {'action': 'status'})
                assert not result.is_error
                assert json.loads(result.content[0].text)['particles'] == 0
    asyncio.run(run())


def test_bridge_passive_cache_budget_and_readonly_tools(tmp_path):
    spec = importlib.util.spec_from_file_location('memory_bridge_test', ROOT / 'backend.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine, provider = prepared(tmp_path)
    engine.process(engine.store.batch());engine.embed_pending()
    class Host:
        def __init__(self): self.searches = 0
        def query(self, topic, **args):
            return {'session_id': 's1'} if topic == 'ling.identity' else [message(20, '主题')]
        def call_service(self, name, tool, **args):
            if args['action'] == 'search': self.searches += 1
            return engine.dispatch(args['action'], args.get('values', {}))
    host = Host(); host.chat_api_version = 1; host.subscribe = lambda names: None
    plugin = module.MemoryPlugin(host)
    first = plugin.context({'kind': 'ling'})
    assert first and plugin.context({'kind': 'ling'}) == first and host.searches == 1
    assert sum(len(m['content']) for m in first) <= 3000
    engine.save_settings({'passive_chars': 1})
    assert plugin.context({'kind': 'ling'}) == []
    tools = plugin.tools({'kind': 'ling'})
    assert len(tools) == 3 and all(t.read_only for t in tools)
    plugin.start()
    plugin.close()
    assert not plugin.thread.is_alive()
