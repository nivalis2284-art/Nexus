"""Versioned host services. Business plugins never receive mutable Application state."""
import asyncio
import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from ..hooks import HookRegistry
from ...file_io import replace_text
from .store import Store, uid, now
from .models import Models
from .history import History
from .jobs import Jobs
from .dialogues import Dialogues
from .bridge import Bridge

CAPABILITIES = {name: 1 for name in ('models', 'dialogues', 'history', 'jobs', 'storage', 'services', 'context', 'events', 'hooks', 'chat', 'mcp', 'ui')}
LONG = {'models.generate', 'models.embed', 'dialogues.send', 'dialogues.continue', 'dialogues.continue_turn', 'services.call'}

class Runtime:
    def __init__(self, manager):
        self.manager = manager
        self.store = Store(manager.home)
        self.local = threading.local()
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='plugin-operation')
        self.cancel_events = {}
        self.services = {}
        self.service_chain = threading.local()
        self.models = Models(self)
        self.history = History(self)
        self.jobs = Jobs(self)
        self.dialogues = Dialogues(self)
        self.bridge = Bridge(self)
        self.closed = False
        from ...runtime.stream import runtime_updates
        from collections import deque
        self.chat_buffer = deque(maxlen=1000)
        self.feed = runtime_updates(manager.state)
        self.feed.subscribe(self)
        for op in self.store.list('operation'):
            if op['status'] in ('pending', 'running'):
                op.update(status='interrupted', error='宿主已重启，操作未重放')
                self.store.put('operation', op['id'], op['owner'], op)

    def current_operation(self):
        return getattr(self.local, 'operation', None)

    def check(self, owner, method):
        item = self.manager.entries.get(owner)
        if item is None: raise ValueError('插件不存在')
        registering = method in ('capabilities.describe', 'capabilities.require', 'models.list', 'context.export', 'services.register', 'services.list', 'jobs.register', 'events.subscribe', 'hooks.define', 'hooks.subscribe', 'hooks.unsubscribe') or method.startswith('storage.')
        if not item.enabled or item.status not in (('ready', 'loading') if registering else ('ready',)) or self.closed:
            raise ValueError('插件已停用或尚未就绪')

    def start(self, owner, method, params):
        if method not in LONG: raise ValueError('此方法不是长操作')
        self.check(owner, method)
        params = dict(params)
        job_context = getattr(self.local, 'job', None)
        if job_context and job_context[0] == owner:
            params.update(job_id=job_context[1], job_attempt=job_context[2])
        if params.get('job_id'):
            job = self.store.get('job', params['job_id'], owner)
            if job['attempt'] != params.get('job_attempt') or job['status'] not in ('running', 'recovering'):
                raise ValueError('作业尝试已失效，不能发起新操作')
        request_id = params.get('request_id')
        with self.store.lock:
            if request_id:
                for old in self.store.list('operation', owner):
                    if old.get('request_id') == request_id:
                        if old['method'] != method or old['input'] != params: raise ValueError('请求编号已用于其他操作')
                        return {'operation_id': old['id']}
            op = {'id': uid(), 'owner': owner, 'method': method, 'input': params, 'request_id': request_id,
                  'status': 'pending', 'result': None, 'error': '', 'created_at': now()}
            self.store.put('operation', op['id'], owner, op)
            cancel = self.cancel_events[op['id']] = threading.Event()
            self.pool.submit(self.run_operation, op, cancel)
            return {'operation_id': op['id']}

    def run_operation(self, op, cancel):
        owner, key = op['owner'], op['id']
        self.local.operation = key
        emit = lambda event: self.store.emit(owner, key, 'model.event', event)
        try:
            with self.manager.lease(owner):
                if cancel.is_set(): raise asyncio.CancelledError()
                op['status'] = 'running'; self.store.put('operation', key, owner, op)
                method, p = op['method'], op['input']
                if method == 'models.generate': result = self.models.generate(owner, p, cancel, emit)
                elif method == 'models.embed': result = self.models.embed(owner, p, cancel, emit)
                elif method.startswith('dialogues.'):
                    result = self.dialogues.run(owner, p, cancel, emit, method in ('dialogues.continue', 'dialogues.continue_turn'))
                else: result = self.call_service(owner, **p)
                if cancel.is_set(): raise asyncio.CancelledError()
                op.update(status='completed', result=result)
        except BaseException as error:
            op.update(status='cancelled' if isinstance(error, asyncio.CancelledError) else 'failed', error='操作取消' if isinstance(error, asyncio.CancelledError) else str(error))
        finally:
            self.store.put('operation', key, owner, op)
            self.store.emit(owner, key, 'operation.done', {'status': op['status'], 'error': op['error']})
            self.local.operation = None

    def wait(self, owner, operation_id):
        with self.store.changed:
            while True:
                op = self.store.get('operation', operation_id, owner)
                if op['status'] == 'completed': return op['result']
                if op['status'] not in ('pending', 'running'): raise ValueError(op['error'] or op['status'])
                self.store.changed.wait(.1)

    def cancel(self, owner, operation_id):
        op = self.store.get('operation', operation_id, owner)
        self.cancel_events.setdefault(operation_id, threading.Event()).set()
        return {'operation_id': operation_id, 'status': op['status'], 'cancellation_requested': True}

    def export_context(self, sections):
        allowed = {'owner_instructions', 'main_role'}
        if not isinstance(sections, list) or set(sections) - allowed:
            raise ValueError('只支持显式导出 owner_instructions / main_role')
        from ...instructions import load_instructions
        from ...role_instructions import load_role
        return {key: load_instructions(self.manager.home) if key == 'owner_instructions' else load_role(self.manager.home, 'main') for key in sections}

    def remote(self, owner, target, values):
        if not isinstance(target, dict) or set(target) != {'service', 'tool'}:
            raise ValueError('入口须指定本插件 service 和 tool')
        return self.manager.call_service(owner, target['service'], target['tool'], values)

    def register_service(self, owner, name, version=1, handler=None, target=None, schema=None):
        if not isinstance(name, str) or not name or type(version) is not int or version < 1:
            raise ValueError('服务名称或版本无效')
        if not callable(handler) and target is None: raise ValueError('需要本地处理器或 MCP 入口')
        full = owner + '.' + name
        self.services[full] = {'name': full, 'owner': owner, 'version': version, 'schema': schema or {}, 'handler': handler, 'target': target}
        return {k: v for k, v in self.services[full].items() if k not in ('handler', 'target')}

    def call_service(self, owner, name, values=None, version=1, request_id=None, context_id=None, job_id=None, job_attempt=None):
        service = self.services.get(name)
        if not service or service['version'] != version: raise ValueError('服务未加载或版本不匹配')
        chain = self.store.get('service_context', context_id, owner)['chain'] if context_id else getattr(self.service_chain, 'chain', [])
        if name in chain: raise ValueError('检测到循环服务调用链')
        self.service_chain.chain = [*chain, name]
        try:
            with self.manager.lease(service['owner']):
                if service['handler']:
                    return service['handler'](values or {}, {'caller_plugin_id': owner})
                context_key = uid()
                self.store.put('service_context', context_key, service['owner'], {'chain': [*chain, name]})
                try:
                    return self.remote(service['owner'], service['target'], {'values': values or {}, 'caller_plugin_id': owner, '_host_context': context_key})
                finally:
                    self.store.delete('service_context', context_key, service['owner'])
        finally: self.service_chain.chain = chain

    def storage(self, owner, method, p):
        root = self.manager.data_dir(owner)
        if method == 'data_dir': return str(root)
        if method == 'settings.get':
            from copy import deepcopy
            return deepcopy(self.manager.entries[owner].settings)
        if method == 'settings.save': return self.manager.configure(owner, p['values'])
        if method.startswith('secrets.'):
            path = root / 'private-credentials.json'
            with self.store.lock:
                values = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
                name = p['name']
                if not isinstance(name, str) or not name: raise ValueError('凭据名称无效')
                if method == 'secrets.get': return values.get(name)
                if method == 'secrets.set': values[name] = p['value']
                elif method == 'secrets.delete': values.pop(name, None)
                else: raise ValueError('未知凭据操作')
                replace_text(path, json.dumps(values, ensure_ascii=False))
                path.chmod(0o600)
                return {'saved': True}
        if method == 'attachments.read':
            from ...attachments import AttachmentStore
            session_id = self.history.session_id()
            attachment = AttachmentStore(self.manager.home)
            item = attachment.get('sessions', session_id, p['attachment_id'])
            folder = attachment.directory('sessions', session_id, p['attachment_id'])
            text_path = folder / 'text.txt'
            return {'metadata': {k: v for k, v in item.items() if k != 'original_path'}, 'text': text_path.read_text(encoding='utf-8')[max(0,int(p.get('offset',0))):max(0,int(p.get('offset',0)))+min(20000,int(p.get('limit',10000)))] if text_path.exists() else '', 'original_base64': base64.b64encode(Path(item['original_path']).read_bytes()).decode() if p.get('original') else None}
        raise ValueError('未知资源操作')

    def invoke(self, owner, method, p=None):
        p = p or {}
        self.check(owner, method)
        if method == 'services.call' and getattr(self.service_chain, 'chain', []):
            return self.call_service(owner, **p)
        if method in LONG:
            return self.wait(owner, self.start(owner, method, p)['operation_id'])
        if method == 'operations.start': return self.start(owner, p['method'], p.get('params', {}))
        if method == 'operations.get': return self.store.get('operation', p['operation_id'], owner)
        if method == 'operations.events':
            self.store.get('operation', p['operation_id'], owner)
            events = self.store.events(owner, p.get('after', 0), p['operation_id'])
            return {'events': events, 'cursor': events[-1]['seq'] if events else p.get('after', 0)}
        if method in ('operations.cancel', 'models.cancel'): return self.cancel(owner, p['operation_id'])
        if method == 'capabilities.describe': return {'sdk_version': '1.1.0', 'capabilities': CAPABILITIES}
        if method == 'capabilities.require':
            required = p['requires']
            if any(CAPABILITIES.get(k) != v for k,v in required.items()): raise ValueError('宿主能力版本不匹配')
            return {'satisfied': True}
        if method == 'models.list': return self.models.list()
        if method == 'context.export': return self.export_context(p.get('sections', []))
        if method.startswith('storage.'): return self.storage(owner, method[8:], p)
        if method.startswith('history.'):
            action = method[8:]
            if action == 'subscribe': action = 'changes'
            if action not in ('snapshot', 'changes', 'get'): raise ValueError('历史仅提供读取接口')
            return getattr(self.history, action)(**p)
        if method == 'dialogues.create': return self.dialogues.create(owner, **p)
        if method == 'dialogues.list': return [self.dialogue_summary(d) for d in self.store.list('dialogue', owner)]
        if method in ('dialogues.get', 'dialogues.history'):
            d = self.store.get('dialogue', p['dialogue_id'], owner)
            return {'messages': d['messages'], 'checkpoint': d['checkpoint']} if method.endswith('history') else self.dialogue_summary(d)
        if method == 'dialogues.clear': return self.dialogues.clear(owner, **p)
        if method == 'dialogues.delete': return self.dialogues.delete(owner, **p)
        if method == 'dialogues.cancel':
            for op in self.store.list('operation', owner):
                if op['input'].get('dialogue_id') == p['dialogue_id'] and op['status'] in ('pending','running'): self.cancel(owner, op['id'])
            return {'cancellation_requested': True}
        if method == 'jobs.register': return self.jobs.register(owner, **p)
        if method == 'jobs.submit': return self.jobs.submit(owner, **p)
        if method == 'jobs.get': return self.store.get('job', p['job_id'], owner)
        if method == 'jobs.list': return self.store.list('job', owner)
        if method == 'jobs.cancel': return self.jobs.cancel(owner, **p)
        if method == 'jobs.retry': return self.jobs.retry(owner, **p)
        if method in ('jobs.report', 'jobs.progress', 'jobs.checkpoint'):
            return self.jobs.report(owner, **p)
        if method == 'services.register': return self.register_service(owner, **p)
        if method == 'services.list': return [{k:v for k,v in s.items() if k not in ('handler','target')} for s in self.services.values()]
        if method == 'events.subscribe':
            self.manager.events.subscribe(owner, p['types']); return {'subscribed': True}
        if method == 'events.read': return self.store.events(owner, p.get('after', 0))
        if method == 'hooks.define': return self.manager.hooks.define(owner, p['name'], p.get('description',''))
        if method == 'hooks.subscribe':
            callback = p.get('handler') or (lambda payload, scope: self.remote(owner, p['target'], {'payload':payload,'scope':scope}))
            self.manager.hooks.subscribe(owner, p['name'], callback, p.get('key','default')); return {'subscribed': True}
        if method == 'hooks.unsubscribe': self.manager.hooks.unsubscribe(owner,p['name'],p.get('key','default')); return {'unsubscribed':True}
        if method == 'hooks.emit':
            if self.manager.hooks.definitions.get(p['name'],{}).get('owner') != owner: raise ValueError('只能触发自己的钩子')
            return self.manager.hooks.emit(p['name'],p.get('payload',{}))
        if method == 'chat.snapshot': return self.manager.state['application'].chat_snapshot()
        if method == 'chat.send': return self.manager.state['application'].submit_input({'text':p['text'],'request_id':p['request_id']})
        if method == 'chat.events':
            with self.store.lock:
                events = [e for e in self.chat_buffer if e.get('revision',0)>p.get('after',0)]
                return {'events':events, 'cursor':events[-1]['revision'] if events else p.get('after',0), 'reset_required':bool(self.chat_buffer and p.get('after',0)<self.chat_buffer[0]['revision']-1)}
        if method == 'mcp.connect': return self.manager.connect_service(owner,p['name'],p['config'])
        if method == 'mcp.call': return self.manager.call_service(owner,p['service'],p['tool'],p.get('arguments',{}))
        raise ValueError('未知宿主能力方法：' + method)

    @staticmethod
    def dialogue_summary(d):
        return {k:v for k,v in d.items() if k not in ('messages','turns','continuation_id')}

    def put(self, event):
        if event is not None:
            with self.store.lock:
                self.chat_buffer.append(event)

    def ready(self, owner):
        self.jobs.recover_owner(owner)

    def suspend(self, owner):
        self.jobs.suspend(owner)
        for op in self.store.list('operation', owner):
            if op['status'] in ('pending','running'): self.cancel(owner, op['id'])
        self.services = {k:v for k,v in self.services.items() if v['owner'] != owner}

    def release(self, owner):
        self.bridge.revoke(owner)

    def snapshot(self, owner):
        return {'capabilities': CAPABILITIES, 'requires_host': self.manager.entries[owner].manifest.get('requires_host', {}),
                'services': [{k:v for k,v in s.items() if k not in ('handler','target')} for s in self.services.values() if s['owner']==owner],
                'jobs': self.store.list('job',owner), 'dialogues': [self.dialogue_summary(d) for d in self.store.list('dialogue',owner)],
                'operations': [{k:v for k,v in op.items() if k not in ('input','result')} for op in self.store.list('operation',owner)][-100:]}

    def close(self):
        for owner in list(self.manager.entries): self.suspend(owner)
        self.closed = True
        self.jobs.close()
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.bridge.close()
        self.feed.unsubscribe(self)
        if self.history.sessions: self.history.sessions.on_change = None
