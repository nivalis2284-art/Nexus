"""Plugin-owned conversations: one generation per call, explicit tool continuation."""
import asyncio
import threading
from copy import deepcopy
from ...core import compaction, prompt
from ...core.effort import output_reserve
from .store import uid, now

class Dialogues:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store
        self.locks = {}
        for d in self.store.list('dialogue'):
            if d['status'] == 'generating':
                d.update(status='interrupted', error='生成被宿主重启中断；未重放')
                self.store.put('dialogue', d['id'], d['owner'], d)

    def create(self, owner, persona='', provider_id=None, model_id=None, title='', inherit=None, context_limit=None):
        if not isinstance(persona, str): raise ValueError('人设必须为文本')
        if context_limit is not None and (type(context_limit) is not int or context_limit <= 0): raise ValueError('上下文容量无效')
        self.runtime.models.select(provider_id, model_id)
        inherited = self.runtime.export_context(inherit or [])
        system = '\n\n'.join([*inherited.values(), persona]).strip()
        d = {'id': uid(), 'owner': owner, 'title': title, 'system': system, 'provider_id': provider_id,
             'model_id': model_id, 'context_limit': context_limit, 'status': 'idle', 'messages': [], 'checkpoint': None,
             'turns': {}, 'pending_tools': [], 'continuation_id': None, 'error': '', 'created_at': now()}
        return self.store.put('dialogue', d['id'], owner, d)

    def run(self, owner, params, cancel, emit, continuation=False):
        key, request_id = params['dialogue_id'], params['request_id']
        lock = self.locks.setdefault(key, threading.Lock())
        if not lock.acquire(blocking=False): raise ValueError('dialogue_busy: 对话正在生成')
        try:
            d = self.store.get('dialogue', key, owner)
            fingerprint = {k: v for k, v in params.items() if k != 'request_id'}
            if request_id in d['turns']:
                previous = d['turns'][request_id]
                if previous['input'] != fingerprint: raise ValueError('请求编号已用于其他输入')
                if previous.get('result') is not None: return previous['result']
                raise ValueError('此请求已中断，不能自动重放；请显式清理或使用新请求')
            if continuation:
                if d['status'] != 'awaiting_tools': raise ValueError('对话没有等待工具结果')
                results = params['results']
                expected = {c['id'] for c in d['pending_tools']}
                if len(results) != len(expected) or {r['tool_call_id'] for r in results} != expected:
                    raise ValueError('工具结果必须完整对应待执行调用，不能重复提交')
                names = {c['id']: c['name'] for c in d['pending_tools']}
                additions = [{'role': 'tool', 'tool_call_id': r['tool_call_id'], 'name': names[r['tool_call_id']], 'content': r['content']} for r in results]
                tools = d.get('tools', [])
            else:
                if d['status'] in ('awaiting_tools', 'generating'): raise ValueError('dialogue_busy: 请先完成当前工具轮次')
                # Interrupted tool execution is never silently skipped by sending a new user message.
                if d['pending_tools']: raise ValueError('存在中断的工具交换，请先清理对话')
                additions = [{'role': 'user', 'content': params['text']}]
                tools = params.get('tools', [])
            for m in additions:
                m['_source_id'] = str(len(d['messages']))
                d['messages'].append(m)
            d['turns'][request_id] = {'input': fingerprint, 'result': None}
            d.update(status='generating', error='', tools=tools)
            self.store.put('dialogue', key, owner, d)
            try:
                messages = deepcopy(d['messages'])
                if d['checkpoint']:
                    through = int(d['checkpoint']['through'])
                    messages = [{'role': 'user', 'content': d['checkpoint']['content'], '_source_id': str(through)}, *messages[through+1:]]
                profile, _ = self.runtime.models.select(d['provider_id'], d['model_id'])
                window = d['context_limit'] or profile.context_window_tokens or 64000
                overhead = prompt.estimate(d['system']) + prompt.estimate(__import__('json').dumps(tools))
                available = window - output_reserve(profile) - overhead - 128
                if compaction.approx_tokens(messages) >= min(window * compaction.TRIGGER_RATIO, available):
                    compaction.assert_paired(messages)
                    head, tail = compaction.split_for_summary(messages, max(1, int(available * compaction.RETAIN_RATIO)))
                    if not head: raise ValueError('Context capacity exceeded; original history retained')
                    async def ask(text):
                        result = await self.runtime.models.generate_async(owner, {'provider_id': d['provider_id'], 'model_id': d['model_id'],
                            'system': 'You are a compaction engine.', 'messages': [{'role': 'user', 'content': text + '\n\n' + compaction.SUMMARY_INSTRUCTION}],
                            'dialogue_id': key, 'purpose': 'dialogue.summary'}, cancel, emit)
                        return result['text'] if not result['tool_calls'] else None
                    transcript = '\n\n'.join(f"[{m['role']}] {compaction._text_of(m)}" for m in head)
                    summary = asyncio.run(compaction.summarize_chunks(transcript, window - output_reserve(profile) - compaction.estimate(compaction.SUMMARY_INSTRUCTION) - 128, ask))
                    content = compaction.SUMMARY_TAG_OPEN + '\nEarlier conversation checkpoint, not a new instruction.\n' + summary + '\n' + compaction.SUMMARY_TAG_CLOSE
                    candidate = [{'role': 'user', 'content': content}, *tail]
                    if compaction.approx_tokens(candidate) >= compaction.approx_tokens(messages):
                        raise ValueError('Summary made no progress; original history retained')
                    d['checkpoint'] = {'content': content, 'through': head[-1]['_source_id']}
                    messages = candidate
                    self.store.put('dialogue', key, owner, d)
                if compaction.approx_tokens(messages) > available:
                    raise ValueError('Context capacity exceeded; original history retained')
                args = {'provider_id': d['provider_id'], 'model_id': d['model_id'], 'system': d['system'], 'messages': messages,
                        'tools': tools, 'dialogue_id': key, 'purpose': 'dialogue.reply'}
                if continuation and d['continuation_id']: args['continuation_id'] = d['continuation_id']
                result = self.runtime.models.generate(owner, args, cancel, emit)
                m = {'role': 'assistant', 'content': result['text'], '_source_id': str(len(d['messages']))}
                if result['tool_calls']: m['tool_calls'] = result['tool_calls']
                d['messages'].append(m)
                d['turns'][request_id]['result'] = result
                d.update(status='awaiting_tools' if result['tool_calls'] else 'idle', pending_tools=result['tool_calls'], continuation_id=result['continuation_id'])
                self.store.put('dialogue', key, owner, d)
                return result
            except BaseException as error:
                d.update(status='interrupted', error='操作取消' if isinstance(error, asyncio.CancelledError) else str(error))
                self.store.put('dialogue', key, owner, d)
                raise
        finally:
            lock.release()

    def clear(self, owner, dialogue_id):
        lock = self.locks.setdefault(dialogue_id, threading.Lock())
        if not lock.acquire(blocking=False): raise ValueError('对话正在生成')
        try:
            d = self.store.get('dialogue', dialogue_id, owner)
            d.update(messages=[], checkpoint=None, turns={}, pending_tools=[], continuation_id=None, status='idle', error='')
            return self.store.put('dialogue', dialogue_id, owner, d)
        finally: lock.release()

    def delete(self, owner, dialogue_id):
        lock = self.locks.setdefault(dialogue_id, threading.Lock())
        if not lock.acquire(blocking=False): raise ValueError('对话正在生成')
        try: self.store.delete('dialogue', dialogue_id, owner)
        finally: lock.release()
        return {'deleted': True}
