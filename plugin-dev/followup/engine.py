"""Durable arrangements and runs; the host owns Agent execution."""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from timing import describe, local_zone, next_time, validate

TERMINAL = {'completed', 'cancelled', 'paused', 'interrupted', 'missed', 'failed'}
EVENTS = {'completed', 'paused', 'failed', 'cancelled', 'waiting_user', 'blocked'}
FIELDS = {'title', 'goal', 'context', 'trigger', 'report_mode', 'report_when', 'end_when',
          'workspace', 'provider_id', 'model_id', 'effort'}


class Store:
    def __init__(self, root):
        Path(root).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(Path(root) / 'followup.sqlite3', check_same_thread=False)
        self.db.execute('CREATE TABLE IF NOT EXISTS schedules(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, arrangement_id TEXT NOT NULL, body TEXT NOT NULL)')
        self.db.commit()

    def get(self, table, key):
        row = self.db.execute(f'SELECT body FROM {table} WHERE id=?', (key,)).fetchone()
        if row is None:
            raise ValueError('记录不存在')
        return json.loads(row[0])

    def list(self, table, arrangement_id=None):
        query = f'SELECT body FROM {table}'
        args = ()
        if arrangement_id is not None:
            query += ' WHERE arrangement_id=?'
            args = (arrangement_id,)
        return [json.loads(r[0]) for r in self.db.execute(query + ' ORDER BY rowid DESC', args)]

    def put(self, table, value):
        with self.db:
            if table == 'schedules':
                self.db.execute('INSERT INTO schedules VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                                (value['id'], json.dumps(value, ensure_ascii=False)))
            else:
                self.db.execute('INSERT INTO runs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                                (value['id'], value['arrangement_id'], json.dumps(value, ensure_ascii=False)))
        return value

    def pair(self, item, run):
        with self.db:
            self.db.execute('INSERT INTO schedules VALUES(?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                            (item['id'], json.dumps(item, ensure_ascii=False)))
            self.db.execute('INSERT INTO runs VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body',
                            (run['id'], run['arrangement_id'], json.dumps(run, ensure_ascii=False)))


class Engine:
    def __init__(self, host, root, clock=time.time, zone=local_zone):
        self.host, self.clock, self.zone = host, clock, zone
        self.store = Store(root)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.last_error = ''
        self.last_tick = clock()
        self.zone_name = str(zone())
        self.cursor = host.work.events()['cursor']
        with self.lock:
            for run in self.store.list('runs'):
                if run['status'] not in TERMINAL:
                    run.update(status='interrupted', error='宿主或插件重启；未自动重做', finished_at=clock())
                    if not run.get('task_id'):
                        self._queue_report(run, '执行被重启中断，安排已暂停，请检查已有操作后决定是否继续。')
                    self.store.put('runs', run)
            for item in self.store.list('schedules'):
                item['pending'] = None
                run_id = item.get('active_run')
                if run_id:
                    run = self.store.get('runs', run_id)
                    item['status'] = 'paused'
                self._skip_past(item, clock())
                self.store.put('schedules', item)

    def start(self):
        self.thread = threading.Thread(target=self._loop, name='followup-scheduler', daemon=True)
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.wake.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(35)
        if self.thread is None or not self.thread.is_alive():
            self.store.db.close()

    def _loop(self):
        while not self.stopped.is_set():
            try:
                self.tick()
                self.last_error = ''
            except Exception as error:  # noqa: BLE001 -- keep scheduler failures visible without losing saved work.
                self.last_error = str(error)
            self.wake.wait(1)
            self.wake.clear()

    def list(self, include_deleted=False):
        with self.lock:
            return [self._display(v) for v in self.store.list('schedules')
                    if include_deleted or v['status'] != 'deleted']

    def read(self, arrangement_id):
        with self.lock:
            return {'arrangement': self._display(self.store.get('schedules', arrangement_id)),
                    'runs': self.store.list('runs', arrangement_id)}

    def _display(self, item):
        return {**item, 'trigger_text': describe(item['trigger']), 'timezone': str(self.zone())}

    def save(self, values, arrangement_id=None, request_id=None):
        if not isinstance(values, dict) or set(values) - FIELDS:
            raise ValueError('包含不支持的安排字段')
        with self.lock:
            now = self.clock()
            if not arrangement_id and request_id:
                old = next((v for v in self.store.list('schedules') if v.get('request_id') == request_id), None)
                if old:
                    if old['creation_values'] != values:
                        raise ValueError('请求标识已用于另一项安排')
                    return self._display(old)
            item = self.store.get('schedules', arrangement_id) if arrangement_id else {
                'id': uuid.uuid4().hex, 'status': 'active', 'created_at': now, 'last_result': '',
                'success_count': 0, 'active_run': None, 'pending': None, 'revision': 0,
                'context': '', 'report_mode': 'changes', 'report_when': '', 'end_when': '',
                'request_id': request_id, 'creation_values': values}
            if item['status'] == 'deleted':
                raise ValueError('已删除的安排不能修改')
            if item.get('active_run'):
                previous = self.store.get('runs', item['active_run'])
                work = self.host.work.get(task_id=previous['task_id']) if previous.get('task_id') else None
                if item['status'] != 'paused' or work and (work.get('active') or work['state'] not in ('paused', 'cancelled', 'completed')):
                    raise ValueError('请先暂停安排并等待本轮停止，再修改执行内容')
                item['active_run'] = None
            defaults = self.host.work.defaults()
            merged = {**item, **{k: defaults[k] for k in ('workspace', 'provider_id', 'model_id', 'effort') if k not in item}, **values}
            for key in ('title', 'goal', 'workspace', 'provider_id', 'model_id'):
                if not isinstance(merged.get(key), str) or not merged[key].strip():
                    raise ValueError('请填写' + {'title': '名称', 'goal': '任务说明', 'workspace': '项目目录',
                                              'provider_id': '模型提供商', 'model_id': '模型'}[key])
            if len(merged['title']) > 160 or len(merged['goal']) > 20000:
                raise ValueError('名称或任务说明超过后台工作允许的长度')
            for key in ('context', 'report_when', 'end_when'):
                if not isinstance(merged[key], str):
                    raise TypeError(key + ' 必须为文本')
            if merged['report_mode'] not in ('always', 'changes'):
                raise ValueError('汇报方式须为 always / changes')
            if merged.get('effort') not in (None, 'low', 'medium', 'high', 'xhigh', 'max'):
                raise ValueError('未知思考强度')
            if not Path(merged['workspace']).is_dir():
                raise ValueError('项目目录不存在')
            models = defaults['models']
            if not any(m['provider_id'] == merged['provider_id'] and m['model_id'] == merged['model_id'] and m['configured'] for m in models):
                raise ValueError('请选择已配置的模型')
            trigger = merged.get('trigger')
            if isinstance(trigger, dict) and trigger.get('kind') == 'event':
                events = trigger.get('events')
                if not isinstance(events, list) or not events or any(v not in EVENTS for v in events):
                    raise ValueError('请选择有效的工作事件')
                if type(trigger.get('repeat', False)) is not bool:
                    raise ValueError('repeat 必须为布尔值')
                self.host.work.get(task_id=trigger.get('task_id', ''))
                merged['trigger'] = {'kind': 'event', 'task_id': trigger['task_id'], 'events': list(dict.fromkeys(events)),
                                     'repeat': trigger.get('repeat', False)}
                merged['next_at'] = None
            else:
                merged['trigger'] = validate(trigger, now, self.zone())
                merged['next_at'] = next_time(merged['trigger'], now, self.zone())
            merged.update(updated_at=now, revision=item['revision'] + 1, pending=None,
                          event_after=self.host.work.events()['cursor'])
            self.store.put('schedules', merged)
            self.wake.set()
            return self._display(merged)

    def control(self, arrangement_id, operation, request_id=None):
        with self.lock:
            item = self.store.get('schedules', arrangement_id)
            if item['status'] == 'deleted':
                raise ValueError('安排已删除')
            if operation in ('pause', 'delete'):
                item.update(status='deleted' if operation == 'delete' else 'paused', pending=None,
                            revision=item['revision'] + 1)
                self.store.put('schedules', item)
                if item.get('active_run'):
                    run = self.store.get('runs', item['active_run'])
                    if run.get('task_id') and run['status'] not in TERMINAL:
                        self.host.work.control(task_id=run['task_id'], operation='pause', request_id='followup-stop-' + run['id'])
                    elif not run.get('task_id'):
                        run.update(status='cancelled', finished_at=self.clock())
                        self.store.put('runs', run)
                        item['active_run'] = None
                        self.store.put('schedules', item)
            elif operation == 'resume':
                resuming_current = False
                if item.get('active_run'):
                    run = self.store.get('runs', item['active_run'])
                    work = self.host.work.get(task_id=run['task_id']) if run.get('task_id') else None
                    if work and (work.get('active') or work['state'] not in ('completed', 'cancelled', 'paused')):
                        raise ValueError('当前工作仍在收尾或等待决定，请先处理本轮')
                    if work and work['state'] == 'paused':
                        self.host.work.control(task_id=run['task_id'], operation='resume',
                                               request_id='followup-resume-' + (request_id or uuid.uuid4().hex))
                        run.update(status='running', error='')
                        self.store.put('runs', run)
                        resuming_current = True
                    else:
                        item['active_run'] = None
                item.update(status='active', pending=None, revision=item['revision'] + 1,
                            event_after=self.host.work.events()['cursor'])
                item['next_at'] = None if item['trigger']['kind'] == 'event' else next_time(item['trigger'], self.clock(), self.zone())
                if item['trigger']['kind'] == 'once' and item['next_at'] is None and not resuming_current:
                    raise ValueError('单次时间已过，请编辑为未来时间或立即运行')
                self.store.put('schedules', item)
            elif operation == 'run':
                if item['status'] == 'completed':
                    raise ValueError('安排已完成；如需重新关注请创建新安排')
                if item.get('active_run'):
                    active = self.store.get('runs', item['active_run'])
                    if active['status'] in ('paused', 'waiting_user', 'interrupted', 'failed'):
                        raise ValueError('已有未完成的关联工作，请先处理待决定事项并恢复本轮，或取消后再运行')
                self._trigger(item, {'kind': 'manual', 'at': self.clock()}, 'manual-' + (request_id or uuid.uuid4().hex))
            else:
                raise ValueError('未知操作')
            self.wake.set()
            return self._display(self.store.get('schedules', arrangement_id))

    def result(self, task_id, run_id, summary, context, notify, finished, report_text=''):
        with self.lock:
            run = self.store.get('runs', run_id)
            item = self.store.get('schedules', run['arrangement_id'])
            if item['active_run'] != run_id or item['status'] == 'deleted':
                raise ValueError('安排已停止或本轮已不再是当前执行')
            work = self.host.work.get(task_id=task_id)
            if work.get('plugin_id') != 'followup' or work.get('plugin_source', {}).get('run_id') != run_id:
                raise ValueError('只能提交当前安排本轮的结果')
            if run.get('task_id') not in (None, task_id) or work['state'] != 'running':
                raise ValueError('本轮已结束或工作标识不匹配')
            if not summary.strip():
                raise ValueError('结果摘要不能为空')
            run.update(task_id=task_id, result={'summary': summary, 'context': context, 'notify': notify,
                                              'finished': finished, 'report_text': report_text})
            self.store.put('runs', run)
            return {'saved': True}

    def _trigger(self, item, trigger, key):
        run_id = uuid.uuid5(uuid.NAMESPACE_URL, item['id'] + ':' + key).hex
        if any(r['id'] == run_id for r in self.store.list('runs', item['id'])):
            return
        if item.get('active_run'):
            item['pending'] = {'trigger': trigger, 'key': key}
            self.store.put('schedules', item)
            return
        run = {'id': run_id, 'arrangement_id': item['id'], 'revision': item['revision'],
               'status': 'queued', 'trigger': trigger, 'created_at': self.clock(), 'task_id': None,
               'result': None, 'report_text': '', 'reported': False, 'error': ''}
        item['active_run'] = run_id
        self.store.pair(item, run)

    def _skip_past(self, item, now):
        if item['status'] != 'active' or item['trigger']['kind'] == 'event':
            return
        if item.get('next_at') is not None and item['next_at'] <= now:
            run = {'id': uuid.uuid4().hex, 'arrangement_id': item['id'], 'status': 'missed',
                   'trigger': {'kind': 'missed', 'at': item['next_at']}, 'created_at': now,
                   'finished_at': now, 'task_id': None, 'result': None, 'error': '服务未运行期间错过，未补执行'}
            self.store.put('runs', run)
            item['next_at'] = next_time(item['trigger'], now, self.zone())
            if item['trigger']['kind'] == 'once':
                item['status'] = 'missed'

    def _queue_report(self, run, text):
        run.update(report_text=text, reported=False)

    def _finish_run(self, item, run, work):
        state = work['state']
        result = run.get('result')
        if state == 'completed' and result:
            run.update(status='completed', finished_at=self.clock())
            if item['status'] != 'deleted':
                notify = item['success_count'] == 0 or item['report_mode'] == 'always' or result['notify'] or result['finished']
                if notify:
                    self._queue_report(run, result['report_text'] or result['summary'])
                item.update(last_result=result['context'] or result['summary'], success_count=item['success_count'] + 1)
                once = item['trigger']['kind'] == 'once' or item['trigger']['kind'] == 'event' and not item['trigger']['repeat']
                if result['finished'] or once:
                    item.update(status='completed', pending=None)
        else:
            run.update(status='failed' if state == 'completed' else state, finished_at=self.clock(),
                       error='本轮未提交安排结果' if state == 'completed' else work.get('wait_reason') or '工作未正常完成')
            if item['status'] not in ('deleted', 'paused'):
                item.update(status='paused', pending=None)
                if state == 'completed':
                    self._queue_report(run, run['error'] + '，安排已暂停。')
        item['active_run'] = None if state in ('completed', 'cancelled') else run['id']
        self.store.pair(item, run)
        if item['status'] == 'active' and item.get('pending'):
            pending = item.pop('pending')
            item['pending'] = None
            self.store.put('schedules', item)
            self._trigger(item, pending['trigger'], pending['key'])

    def _dispatch(self, item, run):
        one_shot = item['trigger']['kind'] == 'once' or item['trigger']['kind'] == 'event' and not item['trigger']['repeat']
        context = '\n\n'.join(v for v in [item['context'], '上次有效结果：\n' + (item['last_result'] or '尚无，首次检查请汇报基线。'),
                  '安排的触发规则：' + describe(item['trigger']),
                  '本次触发：\n' + json.dumps(run['trigger'], ensure_ascii=False),
                  '汇报要求：' + item['report_mode'] + '\n' + item['report_when'],
                  '结束条件：' + (item['end_when'] or ('一次性安排，成功后即结束，不会自动创建后续关注。' if one_shot else '持续保留，除非目标已完成或所有者要求停止。')),
                  ('完成后调用 followup_finish，提交实际结果、后续需要的摘要、是否汇报和是否结束关注。不要用 task_finish 替代。'
                   '执行中自行处理普通问题；需要所有者决定时使用现有询问/审批。')] if v)
        try:
            work = self.host.work.start(request_id='followup-' + run['id'], title=item['title'],
                goal=item['goal'], context=context, workspace=item['workspace'], provider_id=item['provider_id'],
                model_id=item['model_id'], effort=item['effort'], report=False,
                source={'arrangement_id': item['id'], 'run_id': run['id']})
            run.update(task_id=work['id'], status='running')
            self.store.put('runs', run)
        except Exception as error:  # noqa: BLE001 -- a failed dispatch must pause the arrangement and retain its error.
            run.update(status='failed', error=str(error), finished_at=self.clock())
            self._queue_report(run, f"安排「{item['title']}」未能启动：{error}。已暂停。")
            item.update(status='paused', pending=None, active_run=None)
            self.store.put('runs', run)
            self.store.put('schedules', item)

    def tick(self):
        with self.lock:
            now = self.clock()
            zone = self.zone()
            resumed = now - self.last_tick > 30
            if resumed:
                self.cursor = self.host.work.events()['cursor']
            page = self.host.work.events(after=self.cursor)
            self.cursor = page['cursor']
            for item in self.store.list('schedules'):
                if item['status'] == 'deleted' and not item.get('active_run'):
                    continue
                schedule = item['trigger']
                if resumed:
                    item['pending'] = None
                    self._skip_past(item, now)
                if str(zone) != self.zone_name and schedule['kind'] not in ('event', 'interval') and item['status'] == 'active':
                    item['next_at'] = next_time(schedule, now, zone)
                    if schedule['kind'] == 'once' and item['next_at'] is None:
                        item['status'] = 'missed'
                self.store.put('schedules', item)
                if item['status'] == 'active':
                    if schedule['kind'] == 'event':
                        for event in page['events']:
                            kind = 'waiting_user' if event['kind'] == 'interaction' else event['kind']
                            if kind == 'interrupted' or kind == 'paused' and event.get('source') == 'error':
                                kind = 'failed'
                            if event['id'] > item['event_after'] and event['task_id'] == schedule['task_id'] and kind in schedule['events']:
                                self._trigger(item, {'kind': 'event', 'event': event}, 'event-' + str(event['id']))
                    elif item.get('next_at') is not None and item['next_at'] <= now:
                        due = item['next_at']
                        if schedule['kind'] == 'interval':
                            item['next_at'] = due + (int((now - due) // schedule['seconds']) + 1) * schedule['seconds']
                        else:
                            item['next_at'] = next_time(schedule, now, zone)
                        self.store.put('schedules', item)
                        self._trigger(item, {'kind': 'time', 'at': due}, 'time-' + str(due))
                item = self.store.get('schedules', item['id'])
                if item.get('active_run'):
                    run = self.store.get('runs', item['active_run'])
                    if run['status'] == 'queued' and not self.stopped.is_set():
                        self._dispatch(item, run)
                    elif run.get('task_id'):
                        work = self.host.work.get(task_id=run['task_id'])
                        if work['state'] in ('completed', 'cancelled', 'paused'):
                            if run['status'] not in TERMINAL or work['state'] in ('completed', 'cancelled'):
                                self._finish_run(item, run, work)
                        elif work['state'] == 'waiting_user':
                            run['status'] = 'waiting_user'
                            item.update(status='paused', pending=None)
                            self.store.put('runs', run)
                            self.store.put('schedules', item)
            for run in self.store.list('runs'):
                if run.get('report_text') and not run.get('reported'):
                    self.host.work.report(task_id=run.get('task_id'), text=run['report_text'],
                                          request_id='followup-result-' + run['id'],
                                          source={'arrangement_id': run['arrangement_id'], 'run_id': run['id']})
                    run['reported'] = True
                    self.store.put('runs', run)
            self.zone_name = str(zone)
            self.last_tick = now
