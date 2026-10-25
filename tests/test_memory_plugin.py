"""Integration boundaries for static memory; no external model calls."""
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from nexus.core.protocol import request_body
from nexus.model import APIFamily, ModelCapabilities, ModelProfile
from nexus.session import Message, Session
from nexus.web.server import _internal
from plugins.memory.backend import MemoryPlugin


class Host:
    def __init__(self, tmp_path):
        self.data_dir = tmp_path
        self.settings = {'takeover': True, 'session_id': 's1'}
        self.records = [{'kind': 'user', 'text': '旧提问', 'at': 't1'},
                        {'kind': 'assistant', 'text': '旧回答', 'at': 't2'}]
        self.prepares = 0
        self.error = None
        self.snapshot = {'version_id': 'v1', 'session_id': 's1',
                         'raw_count': 2, 'records': deepcopy(self.records)}

    def query(self, topic, **args):
        if topic == 'sessions.records':
            return self.records
        return {'session_id': 's1'}

    def call_service(self, service, tool, **values):
        if self.error:
            raise ValueError(self.error)
        if tool == 'memory_snapshot':
            return deepcopy(self.snapshot)
        if tool == 'memory_prepare':
            self.prepares += 1
            return {'content': '曾讨论记录可靠性', 'run_id': 'run-1', 'version_id': 'v1'}
        return {'ok': True}

    def save_settings(self, values):
        self.settings = values


def messages():
    return [
        {'role': 'user', 'content': '旧提问', '_source': {'session_id': 's1', 'index': 0}},
        {'role': 'assistant', 'content': '旧回答', '_source': {'session_id': 's1', 'index': 1}},
        {'role': 'user', 'content': '我们以前讨论过什么？',
         '_source': {'session_id': 's1', 'index': 2}},
    ]


def test_takeover_keeps_new_input_and_live_tools_and_caches_per_input(tmp_path):
    host = Host(tmp_path)
    plugin = MemoryPlugin(host)
    incoming = messages()
    original = deepcopy(incoming)
    scope = {'kind': 'ling', 'session_id': 's1', 'input_id': 10, 'messages': incoming}
    result = plugin.context(scope)
    assert result['messages'] == incoming[2:]
    assert incoming == original
    assert result['materials'][0]['status'] == 'memory'
    scope['messages'] += [
        {'role': 'assistant', 'tool_calls': [{'id': 'c1', 'name': 'memory_search'}]},
        {'role': 'tool', 'tool_call_id': 'c1', 'content': '结果'},
    ]
    result = plugin.context(scope)
    assert len(result['messages']) == 3
    assert host.prepares == 1


@pytest.mark.parametrize('reason', ['service', 'changed', 'unpaired'])
def test_failure_restores_original_history_with_explicit_status(tmp_path, reason):
    host = Host(tmp_path)
    incoming = messages()
    if reason == 'service':
        host.error = '服务不可用'
    elif reason == 'changed':
        host.records[0]['text'] = '修改过的原文'
    else:
        incoming.append({'role': 'tool', 'tool_call_id': 'missing', 'content': '孤立结果'})
    result = MemoryPlugin(host).context({'kind': 'ling', 'session_id': 's1', 'messages': incoming})
    assert result['messages'] == incoming
    assert result['materials'][0]['status'] == 'fallback'
    assert '恢复' in result['materials'][0]['content']


def test_empty_recall_still_takes_over_and_other_sessions_are_not_affected(tmp_path):
    host = Host(tmp_path)
    original = host.call_service
    host.call_service = lambda service, tool, **kw: (
        {'content': '', 'run_id': 'r0', 'version_id': 'v1'} if tool == 'memory_prepare'
        else original(service, tool, **kw))
    plugin = MemoryPlugin(host)
    assert plugin.context({'kind': 'chat', 'session_id': 's1'}) == []
    assert plugin.context({'kind': 'ling', 'session_id': 'other'}) == []
    result = plugin.context({'kind': 'ling', 'session_id': 's1', 'messages': messages()})
    assert result['messages'] == messages()[2:]
    assert result['materials'][0]['status'] == 'memory'


def test_source_markers_cover_internal_experience_and_never_reach_provider():
    session = Session('s1', items=[Message('notice', 'skip'), Message('user', 'question'),
                                  Message('experience', '内部经历')])
    internal = _internal(session)
    assert [m['_source']['index'] for m in internal] == [1, 2]
    for family in APIFamily:
        profile = ModelProfile(api_family=family, model='test', base_url='https://example.test/v1',
                               capabilities=ModelCapabilities(tools=True))
        body = request_body(profile, 'system', internal, [])
        assert '_source' not in str(body)


def test_projection_happens_before_compaction_and_is_never_written_back(tmp_path, monkeypatch):
    from test_agent_loop import build_agent, run, text_stream
    agent = build_agent(tmp_path)
    incoming = [{'role': 'user', 'content': 'huge-old-history'},
                {'role': 'user', 'content': 'current question'}]
    agent.plugins = SimpleNamespace(prepare_agent=lambda _agent, history: history[1:])
    agent.profile = agent.profile.__class__(**{
        **agent.profile.__dict__, 'context_window_tokens': 128})
    agent.context_limit = 128
    folds = []

    async def fold(original, trimmed, window):
        from nexus.core.compaction import CompactionReport
        folds.append(deepcopy(original))
        return [{'role': 'user', 'content': 'recent-only-summary'}], CompactionReport(summarised=True)

    monkeypatch.setattr(agent, '_fold', fold)
    seen = []
    incoming[1]['content'] *= 40
    run(agent, incoming, [text_stream('ok')], seen)
    assert folds and all('huge-old-history' not in str(value) for value in folds)
    assert incoming[0]['content'] == 'huge-old-history'
    assert 'recent-only-summary' in str(seen[0])
    assert 'huge-old-history' not in str(seen[0])


def test_real_agent_plugin_preparation_exposes_remaining_material_capacity(tmp_path):
    from test_agent_loop import build_agent
    from test_plugin_manager import make_manager

    agent = build_agent(tmp_path)
    manager = make_manager(tmp_path)
    try:
        incoming = [{'role': 'user', 'content': 'current question'}]
        assert manager.prepare_agent(agent, incoming) == incoming
        assert agent.request_materials == []
    finally:
        manager.close()


def test_background_build_keeps_plugin_draining_until_work_finishes(tmp_path):
    from test_plugin_manager import load_sync, make_manager, plugin_zip

    from nexus.plugins.api import PluginHost

    manager = make_manager(tmp_path)
    release = threading.Event()
    try:
        manager.import_zip(plugin_zip())
        item = load_sync(manager, 'demo-plugin')
        worker = PluginHost(manager, item.id).background(lambda: release.wait(3))
        manager.operate(item.id, 'unload')
        assert item.status == 'draining'
        assert item.instance is not None
        with pytest.raises(ValueError, match='停用'), manager.lease(item.id):
            pass
        release.set()
        worker.join(timeout=3)
        assert item.status == 'unloaded'
    finally:
        release.set()
        manager.close()
