"""Standard-library bridge using only documented PluginHost methods."""
import json
import threading
import time
from nexus.plugins.api import ToolSpec, BaseModel, Field

class SearchArgs(BaseModel):
    query: str
    mode: str = 'semantic'
    limit: int = Field(default=5, ge=1, le=100)

class IdArgs(BaseModel):
    particle_id: str

class MemoryPlugin:
    def __init__(self, host):
        self.host = host
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.capture_lock = threading.Lock()
        self.cache = None
        self.last_error = ''
        self.thread = None
        self.session_id = None

    def rpc(self, action, values=None):
        return self.host.call_service('memory', 'memory_admin', action=action, values=values or {})

    def records(self):
        session_id = self.host.query('ling.identity')['session_id']
        return session_id, self.host.query('sessions.records', id=session_id)

    def collect(self, through=None):
        with self.capture_lock:
            session_id, records = self.records()
            if through is not None:
                records = records[:int(through)+1]
            return self.rpc('collect', {'session_id': session_id, 'records': records})

    def start(self):
        if getattr(self.host, 'chat_api_version', None) != 1:
            raise RuntimeError('需要主交流公共接口 v1')
        # Initialize baseline synchronously so no incoming message is mistaken for pre-install history.
        self.collect()
        self.host.subscribe(['message.saved'])
        def loop():
            startup = True
            while not self.stop_event.is_set():
                try:
                    if startup or self.rpc('schedule', {'kind': 'capture'})['due']:
                        self.collect()
                    s = self.rpc('settings.get')
                    if s['process_mode'] == 'arrival' or self.rpc('schedule', {'kind': 'process'})['due']:
                        self.rpc('process')
                    self.last_error = ''
                except Exception as error:
                    self.last_error = str(error)
                startup = False
                self.stop_event.wait(15)
        self.thread = threading.Thread(target=loop, name='memory-scheduler', daemon=True)
        self.thread.start()

    def on_event(self, event):
        data = event.get('data', {})
        if data.get('kind') != 'checkpoint':
            return
        if not self.rpc('settings.get')['capture_compression']:
            return
        session_id = self.host.query('ling.identity')['session_id']
        if data.get('session_id') == session_id:
            # Re-read the durable checkpoint rather than trusting an index changed by history edits.
            _, records = self.records()
            checkpoint = next((r for r in reversed(records) if r.get('kind') == 'checkpoint'), None)
            if checkpoint:
                self.collect(checkpoint.get('through'))

    def context(self, scope):
        if scope.get('kind') != 'ling':
            return []
        s = self.rpc('settings.get')
        if not s['passive_enabled'] or not s['embedding_url'] or not s['embedding_model']:
            return []
        _, records = self.records()
        users = [r for r in records if r.get('kind') == 'user']
        if not users:
            return []
        last = users[-1]
        status = self.rpc('status')
        identity = (last.get('ling_input_id'), last.get('text'), status['revision'], json.dumps(s, sort_keys=True))
        with self.lock:
            if self.cache and self.cache[0] == identity:
                if self.cache[2]:
                    raise ValueError(self.cache[2])
                return self.cache[1]
            query = last.get('text', '')
            if not query.strip():
                self.cache = (identity, [], '')
                return []
            if len(query) < 30 and len(users) > 1:
                query = users[-2].get('text', '')[-500:] + '\n当前问题：' + query
            try:
                rows = self.rpc('search', {'query': query, 'limit': s['passive_limit']})['particles']
                materials, remaining = [], s['passive_chars']
                for p in rows:
                    content = f"记忆 {p['id']}（来源：{p['source']['label']}；事件时间：{p['event_time']}）\n{p['content']}"
                    if len(content) <= remaining:
                        remaining -= len(content)
                        materials.append({'purpose': 'reference', 'source': 'memory:' + p['id'], 'content': content})
                self.cache = (identity, materials, '')
                return materials
            except Exception as error:
                self.cache = (identity, [], str(error))
                raise

    def tools(self, scope):
        if scope.get('kind') != 'ling':
            return []
        # Read-only public adapters avoid classifying memory reads as write operations.
        return [ToolSpec('memory_search', '检索长期记忆粒子；semantic语义、exact词句匹配。先检索粒子，必要时展开；保留来源、时间和不确定性。', SearchArgs,
                         lambda ctx, args: self.tool('search', args), read_only=True),
                ToolSpec('memory_get', '按ID读取记忆粒子。', IdArgs, lambda ctx, args: self.tool('get', args), read_only=True),
                ToolSpec('memory_expand', '粒子不足时展开绑定原文，返回内容会进入主交流。', IdArgs, lambda ctx, args: self.tool('expand', args), read_only=True)]

    def tool(self, action, values):
        result = self.rpc(action, values)
        return {'content': json.dumps(result, ensure_ascii=False)}

    def call(self, action, values):
        if action == 'collect':
            return self.collect()
        result = self.rpc(action, values)
        if action == 'status':
            result['bridge_error'] = self.last_error
        return result

    def close(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=20)


def create_plugin(host):
    return MemoryPlugin(host)
