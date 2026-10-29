"""Temporary persona: public Nexus interfaces, local assets, daily reflection."""
from __future__ import annotations

import os
import re
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .reflection import Client
from .store import Store

DEFAULTS = {'auto_evolve': True, 'schedule_time': '04:00', 'reflection_url': '',
            'reflection_model': '', 'reflection_key_env': ''}


def settings(values):
    result = {key: values.get(key, default) for key, default in DEFAULTS.items()}
    if type(result['auto_evolve']) is not bool:
        raise ValueError('自动演化设置必须为布尔值')
    for key in ('schedule_time', 'reflection_url', 'reflection_model', 'reflection_key_env'):
        if not isinstance(result[key], str):
            raise ValueError('设置必须为文本')  # noqa: TRY004 -- host maps validation errors to HTTP 400.
        result[key] = result[key].strip()
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', result['schedule_time']):
        raise ValueError('时间应为 00:00–23:59')
    if result['reflection_key_env'] and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', result['reflection_key_env']):
        raise ValueError('密钥环境变量名无效')
    if result['reflection_url']:
        Client(result)._endpoint()
    return result


def next_run(now, clock):
    """Next future wall-clock occurrence in the computer's local timezone."""
    hour, minute = map(int, clock.split(':'))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


class Persona:
    def __init__(self, host):
        self.host = host
        self.store = Store(host.data_dir)
        self.key_path = Path(host.data_dir) / 'reflection-credentials.txt'
        self.lock = threading.RLock()
        self.collect_lock = threading.Lock()
        self._stop = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.reflection_thread = None
        self.generation = 0
        self.running = False
        self.scheduled = False
        self.error = ''
        self.state = 'ready'
        self.main_id = ''
        self.due = None

    def config(self):
        return settings(self.host.settings)

    def _key(self, config):
        if config['reflection_key_env']:
            return os.environ.get(config['reflection_key_env'], '')
        return self.key_path.read_text(encoding='utf-8') if self.key_path.exists() else ''

    def _save_key(self, value):
        fd, path = tempfile.mkstemp(prefix='.credential-', dir=self.key_path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                out.write(value)
                out.flush()
                os.fsync(out.fileno())
            os.replace(path, self.key_path)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def collect(self):
        with self.collect_lock:
            identity = self.host.query('ling.identity')
            session = identity['session_id']
            records = self.host.query('sessions.records', id=session)
            first = not self.store.metadata('activated')
            self.store.baseline(session, records if first else [])
            if first:
                self.store.metadata('activated', True)
            self.store.ingest(session, records)
            self.main_id = session

    def start(self):
        self.host.subscribe(['message.saved'])
        self.collect()
        self.due = next_run(datetime.now(), self.config()['schedule_time'])
        self.thread = threading.Thread(target=self._loop, name='persona-scheduler', daemon=True)
        self.thread.start()

    def close(self):
        with self.lock:
            self.generation += 1
            self._stop.set()
            self.wake.set()
        for worker in (self.thread, self.reflection_thread):
            if worker and worker is not threading.current_thread():
                worker.join(timeout=23)

    def on_event(self, event):
        if (event.get('data') or {}).get('session_id'):
            identity = self.host.query('ling.identity')
            if event['data']['session_id'] == identity['session_id']:
                self.collect()

    def context(self, scope):
        if scope.get('kind') != 'ling':
            return []
        self.collect()
        current = self.store.current()
        content = ('临时人设记录；以下分别标注所有者设定和可修订的自我描述。'
                   '它们影响交流倾向，不改变宿主角色、权限或所有者当前要求。'
                   '可修订描述来自历史反思，不是已证实的客观事实，不需刻意表演固定性格。\n\n'
                   f"[所有者设定]\n{current['owner_text']}\n\n"
                   f"[可修订描述；版本 {current['revision']}]\n"
                   + (current['persona_text'] or '尚未形成；不补写虚构的兴趣、经历或关系。'))
        estimated = len(content.encode('utf-8')) // 3 + 1
        budget = scope.get('material_budget_tokens', estimated)
        if isinstance(budget, (int, float)) and budget < estimated:
            raise ValueError('本次上下文空间不足，临时人设未注入')
        return [{'purpose': 'reference', 'source': f"temp-persona:revision:{current['revision']}", 'content': content}]

    def tick(self, now=None):
        """One non-blocking scheduler step, also used by deterministic tests."""
        now = now or datetime.now()
        with self.lock:
            config = self.config()
            if self.due is None:
                self.due = next_run(now, config['schedule_time'])
            if not config['auto_evolve']:
                self.scheduled = False
                self.due = next_run(now, config['schedule_time'])
                return
            if now >= self.due:
                slot = self.due.isoformat(timespec='minutes')
                # Jump across missed days rather than running a backlog of jobs.
                if now.date() == self.due.date() and self.store.metadata('last_slot') != slot:
                    self.store.metadata('last_slot', slot)
                    self.scheduled = True
                self.due = next_run(now, config['schedule_time'])
            pending = self.scheduled and not self.running
        if pending:
            if self.host.chat_snapshot().get('running'):
                with self.lock:
                    self.state = 'pending'
                return
            self.launch(scheduled=True)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001 -- surface scheduler failures without terminating it.
                with self.lock:
                    self.error = str(error)
                    self.state = 'error'
                    self.scheduled = False
            self.wake.wait(1)
            self.wake.clear()

    def launch(self, scheduled=False):
        with self.lock:
            if self._stop.is_set():
                raise ValueError('插件正在停止')
            if self.running:
                return {'started': False, 'reason': '反思正在运行'}
            if self.host.chat_snapshot().get('running'):
                if scheduled:
                    self.scheduled = True
                    self.state = 'pending'
                    return {'started': False, 'reason': '等待主对话完成'}
                raise ValueError('请等待当前主对话完成后再反思')
            self.scheduled = False
            config = self.config()
            client = Client(config, self._key(config))
            if not client.configured:
                self.state = 'unconfigured'
                self.error = '请先配置反思模型地址和模型名'
                return {'started': False, 'reason': self.error}
            self.collect()
            pending = self.store.pending()
            if not pending['items']:
                self.state = 'idle'
                self.error = ''
                return {'started': False, 'reason': '没有待反思的新交流'}
            current = self.store.current()
            generation = self.generation
            self.running = True
            self.state = 'running'
            self.error = ''
            self.reflection_thread = threading.Thread(target=self._reflect,
                args=(client, current, pending, generation, config), name='persona-reflection', daemon=True)
            self.reflection_thread.start()
            return {'started': True}

    def _reflect(self, client, current, pending, generation, config):
        payload = {'current': current, 'items': pending['items'],
                   'model': config['reflection_model'], 'endpoint': config['reflection_url']}
        try:
            self.store.archive_attempt(payload, None, '请求准备', record=False)
            response = client.reflect(current, pending['items'])
            if response['persona_text'] == current['persona_text']:
                response['changed'] = False
            payload.update(provider_input=client.last_input, provider_output=client.last_output)
            with self.lock:
                allowed = (not self._stop.is_set() and generation == self.generation
                           and self.config()['auto_evolve'])
                result = self.store.finish_reflection(payload, response, pending['cursor'],
                                                     current['revision'], apply=allowed)
                self.state = 'updated' if result['applied'] else 'unchanged' if result['consumed'] else 'candidate'
                self.store.metadata('last_run', datetime.now().isoformat(timespec='seconds'))
        except Exception as error:  # noqa: BLE001 -- preserve old persona and cursor on provider/backup failure.
            try:
                payload.update(provider_input=client.last_input, provider_output=client.last_output)
                self.store.archive_attempt(payload, client.last_output, str(error))
            except Exception as backup_error:  # noqa: BLE001 -- archive failure must also be visible.
                error = RuntimeError(f'{error}；反思备份失败：{backup_error}')
            with self.lock:
                self.error = str(error)
                self.state = 'error'
        finally:
            with self.lock:
                self.running = False

    def snapshot(self, config=None):
        with self.lock:
            config = config or self.config()
            state = self.state
            if not self.running and not self.scheduled:
                if not config['auto_evolve']:
                    state = 'frozen'
                elif not Client(config).configured:
                    state = 'unconfigured'
            status = {**self.store.status(), 'state': state, 'error': self.error,
                      'next_run': self.due.isoformat(timespec='minutes') if self.due else '',
                      'last_run': self.store.metadata('last_run') or '',
                      'key_present': bool(self._key(config)), 'frozen': not config['auto_evolve']}
            return {'current': self.store.current(), 'settings': config, 'status': status,
                    'history': self.store.history(), 'reflections': self.store.reflection_history()}

    def call(self, action, values):
        if action == 'snapshot':
            return self.snapshot()
        if action == 'reflect':
            return self.launch()
        if action == 'export':
            return self.store.export_zip(self.config())
        if action == 'reflection':
            if type(values.get('id')) is not int:
                raise ValueError('反思记录编号无效')
            return self.store.reflection_detail(values['id'])
        with self.lock:
            if action == 'configure':
                candidate = settings({**self.config(), **values})
                key = values.get('reflection_key', '')
                if not isinstance(key, str):
                    raise ValueError('密钥必须为文本')
                if key:
                    self._save_key(key)
                self.host.save_settings(candidate)
                self.generation += 1
                self.scheduled = False
                self.due = next_run(datetime.now(), candidate['schedule_time'])
                self.wake.set()
                return self.snapshot(candidate)
            if action == 'clear_key':
                self.key_path.unlink(missing_ok=True)
                self.generation += 1
                return self.snapshot()
            if action in ('save', 'rollback'):
                expected = values.get('expected_revision')
                if type(expected) is not int:
                    raise ValueError('缺少当前版本编号，请刷新页面')
                if action == 'save':
                    owner, persona = values.get('owner_text'), values.get('persona_text')
                    if not isinstance(owner, str) or not isinstance(persona, str):
                        raise ValueError('人设必须为文本')
                    if not owner.strip() or len(owner) > 4000 or len(persona) > 4000:
                        raise ValueError('所有者设定不可为空，每部分最多 4000 字符')
                    self.store.save(owner, persona, expected)
                else:
                    revision = values.get('revision')
                    if type(revision) is not int:
                        raise ValueError('回退版本编号无效')
                    self.store.rollback(revision, expected)
                self.generation += 1
                return self.snapshot()
        raise ValueError('未知插件操作：' + str(action))


def create_plugin(host):
    return Persona(host)
