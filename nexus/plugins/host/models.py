"""Stateless model calls through the host gateway; no Agent or main prompt assembly."""
import asyncio
import json
from dataclasses import asdict
from decimal import Decimal
import httpx
from ...core.config import build_profile
from ...core.protocol import request_body
from ...core.replay import capture
from ...model import ModelGateway, RuntimeCredentials
from .store import uid


def serial(value):
    return json.loads(json.dumps(value, default=lambda v: str(v) if isinstance(v, Decimal) else asdict(v)))


class Models:
    def __init__(self, runtime):
        self.runtime = runtime
        self.config = runtime.manager.state['store']

    def list(self):
        result = []
        for p in self.config.providers():
            for m in p.models:
                result.append({'id': p.id + ':' + m['id'], 'provider_id': p.id, 'provider': p.name, 'model_id': m['id'],
                               'kind': 'generation', 'protocol': p.family, 'context_window_tokens': m.get('context_window_tokens'),
                               'capabilities': m.get('capabilities', {}), 'configured': bool(p.base_url and self.config.has_key(p.id))})
        return result

    def select(self, provider_id, model_id):
        provider = next((p for p in self.config.providers() if p.id == provider_id), None)
        model = next((m for m in provider.models if m['id'] == model_id), None) if provider else None
        if model is None:
            raise ValueError('模型不存在')
        key = self.config.read_credentials().get(provider.id, '')
        if not key:
            raise ValueError('模型提供商尚未配置凭据')
        return build_profile(provider, model), key

    async def generate_async(self, owner, params, cancel, emit):
        profile, key = self.select(params['provider_id'], params['model_id'])
        messages = json.loads(json.dumps(params.get('messages', [])))
        continuation = params.get('continuation_id')
        if continuation:
            native = self.runtime.store.get('continuation', continuation, owner)
            if native['route'] != [profile.api_family.value, profile.model, profile.base_url]:
                raise ValueError('续接不能跨模型或提供商')
            # Attach opaque continuation only to its exact saved assistant response.
            match = next((m for m in reversed(messages) if m.get('role') == 'assistant'), None)
            if not match or {'content': match.get('content', ''), 'tool_calls': match.get('tool_calls') or []} != native['assistant']:
                raise ValueError('续接与助手消息不匹配')
            match['native'] = native['native']
        effort = params.get('effort')
        body = request_body(profile, params.get('system', ''), messages, params.get('tools', []), effort)
        scope = {'kind': 'plugin', 'plugin_id': owner, 'owner_id': params.get('dialogue_id') or owner,
                 'dialogue_id': params.get('dialogue_id'), 'job_id': params.get('job_id'),
                 'operation_id': self.runtime.current_operation(), 'purpose': params.get('purpose', 'generate')}
        parts, calls, usage, finish, native = [], [], {}, '', None
        async with httpx.AsyncClient(follow_redirects=False) as client:
            gateway = ModelGateway(client, request_history=self.runtime.manager.state.get('request_history'), request_scope=scope)
            stream = gateway.stream(profile, body, credentials=RuntimeCredentials(key), effort=effort)
            pending = None
            try:
                while True:
                    pending = asyncio.create_task(anext(stream))
                    while not pending.done() and not cancel.is_set():
                        await asyncio.wait({pending}, timeout=.05)
                    if cancel.is_set():
                        raise asyncio.CancelledError()
                    try:
                        event = pending.result()
                    except StopAsyncIteration:
                        break
                    pending = None
                    if event.type == 'error':
                        raise ValueError(event.error.message if event.error else '模型调用失败')
                    if event.type == 'text_delta': parts.append(event.text or '')
                    if event.type == 'tool_call' and event.tool_call:
                        calls.append({'id': event.tool_call.id, 'name': event.tool_call.name, 'arguments': event.tool_call.arguments_json})
                    if event.type == 'usage' and event.usage: usage = serial(event.usage)
                    if event.type == 'complete':
                        finish = event.finish_reason or ''
                        native = capture(profile, event.continuation)
                        if event.usage: usage = serial(event.usage)
                    if event.type != 'complete':
                        value = {'type': event.type}
                        if event.text is not None: value['text'] = event.text
                        if event.tool_call: value['tool_call'] = serial(event.tool_call)
                        emit(value)
            finally:
                if pending is not None:
                    pending.cancel()
                    try: await pending
                    except (asyncio.CancelledError, StopAsyncIteration): pass
                await stream.aclose()
        if finish not in ('stop', 'end_turn', 'stop_sequence', 'completed', 'tool_calls', 'tool_use', 'function_call'):
            raise ValueError('模型输出未完整结束：' + (finish or 'missing completion'))
        if finish in ('tool_calls', 'tool_use', 'function_call') and not calls:
            raise ValueError('模型声明工具调用但没有返回完整调用')
        text = ''.join(parts)
        result = {'text': text, 'tool_calls': calls, 'usage': usage, 'finish_reason': finish, 'continuation_id': None}
        if native:
            token = uid()
            self.runtime.store.put('continuation', token, owner, {'route': [profile.api_family.value, profile.model, profile.base_url],
                'native': native, 'assistant': {'content': text, 'tool_calls': calls}})
            result['continuation_id'] = token
        return result

    def generate(self, owner, params, cancel, emit):
        return asyncio.run(self.generate_async(owner, params, cancel, emit))
