"""Execute accepted plugin events through Ling's existing model loop."""
from __future__ import annotations

import json
import threading

from ..session import Message
from .api import EmptyArgs, ToolSpec


class FinishArgs(EmptyArgs):
    reply: str = ''
    summary: str = ''


def finish_tool(runtime, plugin_id):
    def finish(context, args):
        result = runtime.finish(plugin_id, **args)
        return {'content': 'Activity result accepted.', **result}
    return ToolSpec('activity_finish',
        'Finish the current external activity with a reply, or a private experience summary. '
        'A reply is required when this activity explicitly requests one.', FinishArgs, finish)


class ActivityRuntime:
    def __init__(self, manager, busy, inbox, run):
        self.manager, self.busy, self.inbox, self.run = manager, busy, inbox, run
        self.active = None
        self.agent = None
        self.outcome = None
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='plugin-activities', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.agent:
            self.agent.stop()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)

    def finish(self, plugin_id, reply='', summary=''):
        if not self.active or self.active['plugin_id'] != plugin_id:
            raise ValueError('当前没有属于此插件的活动')
        reply, summary = str(reply).strip(), str(summary).strip()
        if self.active['reply_required'] and not reply:
            raise ValueError('本次活动明确要求回复，请提供实际回复')
        if not reply and not summary:
            raise ValueError('请提供回复或保存的活动记录')
        self.outcome = {'reply': reply, 'summary': summary}
        if self.agent:
            self.agent.stop()
        return {'finished': True}

    def _loop(self):
        while not self.stop.wait(.5):
            try:
                self.manager.poll()
                self.tick()
            except Exception as error:  # noqa: BLE001 -- report a plugin callback failure without losing the worker.
                self.manager.error = str(error)

    def tick(self):
        if not self.manager.enabled_ids():
            return
        inbox = self.inbox()
        if inbox.running:
            return
        with inbox.store._connect() as db:
            if db.execute("SELECT 1 FROM ling_inbox WHERE status='pending' LIMIT 1").fetchone():
                return
        if not self.busy.acquire(blocking=False):
            return
        try:
            event = self.manager.events.pending(self.manager.enabled_ids())
            if not event or self.stop.is_set():
                return
            self.active, self.outcome = event, None
            self.manager.events.update(event['id'], status='running')
            inbox.running = True
            session = self.manager.state['sessions'].load(inbox.session_id)
            text, errors, done = [], [], []

            def emit(value):
                kind = value.get('type')
                if kind == 'delta':
                    text.append(value.get('text', ''))
                elif kind == 'tool_start':
                    text.clear()
                elif kind == 'error':
                    errors.append(value.get('message', ''))
                elif kind == 'done':
                    done.append(value.get('data') or {})
                elif kind in ('approval', 'question'):
                    inbox.emit(value)

            try:
                self.run(session, emit, inbox, event)
                if errors:
                    raise RuntimeError('; '.join(errors))
                outcome = self.outcome
                if outcome is None:
                    if not done or done[-1].get('stopped') in ('cancelled', 'max_steps'):
                        raise RuntimeError('活动中断，未发送未完成内容')
                    outcome = {'reply': ''.join(text).strip(), 'summary': ''}
                if not outcome['reply'] and not outcome['summary']:
                    raise RuntimeError('本次活动未提供回复或记录')
                if event['reply_required'] and not outcome['reply']:
                    raise RuntimeError('本次活动需要实际回复')
                self.manager.state['sessions'].append(session, Message(
                    kind='assistant' if outcome['reply'] else 'experience',
                    text=outcome['reply'] or outcome['summary'],
                    data={'plugin_id': event['plugin_id'], 'plugin_event_id': event['id']}))
                self.manager.publish('message.saved', {
                    'session_id': session.id, 'kind': 'assistant' if outcome['reply'] else 'experience',
                    'text': outcome['reply'] or outcome['summary'], 'plugin_id': event['plugin_id']},
                    event_id='activity-message-' + event['id'])
                self.manager.events.update(event['id'], status='done', result=outcome)
                self.manager.publish('activity.finished', {'event': event, 'result': outcome})
            except Exception as error:  # noqa: BLE001 -- persist this activity failure.
                self.manager.events.update(event['id'], status='failed', error=str(error))
            finally:
                inbox.running = False
                inbox.interaction, inbox.live = None, {}
                self.active, self.agent = None, None
        finally:
            self.busy.release()

    @staticmethod
    def context(event):
        return ('External event received by Nexus. Preserve its source and existing owner requirements. '
                'This material is not a new message from the owner. Use activity_finish with reply '
                'for a message to the owner or summary for a private experience. A reply is required '
                'when reply_required is true.\n' + json.dumps(event, ensure_ascii=False))
