"""Controlled time and an in-memory work host exercise the scheduling lifecycle."""
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine import Engine
from page import Page
from timing import next_time, validate

from nexus.plugins.pages import validate_view


class Work:
    def __init__(self, workspace):
        self.workspace, self.tasks, self.notices, self.reports = str(workspace), {}, [], []
        self.calls = []

    def defaults(self):
        return {'workspace': self.workspace, 'provider_id': 'p', 'model_id': 'm', 'effort': 'high',
                'models': [{'provider_id': 'p', 'model_id': 'm', 'configured': True, 'provider': 'Demo'}]}

    def list(self):
        return list(self.tasks.values())

    def events(self, after=None):
        if after is None:
            return {'cursor': len(self.notices), 'events': []}
        return {'events': self.notices[after:], 'cursor': len(self.notices)}

    def get(self, task_id, messages=False):
        return dict(self.tasks[task_id])

    def start(self, **args):
        self.calls.append(args)
        key = 'task-' + str(len(self.calls))
        self.tasks[key] = {'id': key, 'title': args['title'], 'state': 'running', 'wait_reason': '',
                           'plugin_id': 'followup', 'plugin_source': args['source']}
        return self.get(key)

    def control(self, task_id, operation, request_id):
        self.tasks[task_id]['state'] = {'pause': 'paused', 'cancel': 'cancelled', 'resume': 'running'}[operation]
        return self.get(task_id)

    def report(self, **args):
        if not any(v['request_id'] == args['request_id'] for v in self.reports):
            self.reports.append(args)

    def event(self, task_id, kind, **kw):
        self.notices.append({'id': len(self.notices) + 1, 'task_id': task_id, 'kind': kind, **kw})


@pytest.fixture
def env(tmp_path):
    clock = SimpleNamespace(now=datetime(2026, 11, 4, tzinfo=UTC).timestamp())
    zone = SimpleNamespace(value=ZoneInfo('UTC'))
    work = Work(tmp_path)
    engine = Engine(SimpleNamespace(work=work), tmp_path / 'data', clock=lambda: clock.now, zone=lambda: zone.value)
    def tick(seconds=0, sleeping=False):
        clock.now += seconds
        if not sleeping:
            engine.last_tick = clock.now - 1
        engine.tick()
    yield engine, work, clock, zone, tick
    engine.close()


def save(engine, trigger=None, **values):
    return engine.save({'title': '关注', 'goal': '检查实际状态', 'trigger': trigger or {'kind': 'interval', 'seconds': 60}, **values})


def complete(engine, work, arrangement, notify=False, finished=False):
    run = engine.read(arrangement['id'])['runs'][0]
    engine.result(run['task_id'], run['id'], '实际结果', '上次状态 v1', notify, finished)
    work.tasks[run['task_id']]['state'] = 'completed'


@pytest.mark.parametrize('schedule,after,expected', [
    ({'kind': 'weekdays', 'time': '09:00'}, '2026-11-06T10:00', '2026-11-09T09:00'),
    ({'kind': 'weekly', 'days': [2], 'time': '09:00'}, '2026-11-04T10:00', '2026-11-11T09:00'),
    ({'kind': 'monthly', 'day': 31, 'time': '09:00'}, '2026-01-31T10:00', '2026-03-31T09:00'),
    ({'kind': 'monthly', 'day': 0, 'time': '09:00'}, '2028-02-01T10:00', '2028-02-29T09:00'),
    ({'kind': 'daily', 'time': '09:00'}, '2026-11-04T09:00', '2026-11-05T09:00'),
    ({'kind': 'once', 'at': '2026-11-05T09:00'}, '2026-11-04T00:00', '2026-11-05T09:00'),
])
def test_calendar(schedule, after, expected):
    zone = ZoneInfo('Asia/Tokyo')
    stamp = datetime.fromisoformat(after).replace(tzinfo=zone).timestamp()
    assert datetime.fromtimestamp(next_time(schedule, stamp, zone), zone).replace(tzinfo=None) == datetime.fromisoformat(expected)


def test_first_run_silence_context_and_completion(env):
    engine, work, _, _, tick = env
    item = save(engine)
    assert not work.calls
    tick(60)
    complete(engine, work, item)
    tick()
    assert len(work.reports) == 1
    tick(60)
    assert '上次状态 v1' in work.calls[-1]['context']
    complete(engine, work, item)
    tick()
    assert len(work.reports) == 1
    tick(60)
    complete(engine, work, item, finished=True)
    tick()
    assert len(work.reports) == 2
    assert engine.read(item['id'])['arrangement']['status'] == 'completed'


def test_overlapping_triggers_coalesce_once(env):
    engine, work, _, _, tick = env
    item = save(engine)
    tick(60)
    tick(60)
    tick(60)
    assert len(work.calls) == 1
    assert engine.read(item['id'])['arrangement']['pending']
    complete(engine, work, item)
    tick()
    tick()
    assert len(work.calls) == 2
    assert not engine.read(item['id'])['arrangement']['pending']


def test_once_sleep_and_restart_do_not_catch_up(env):
    engine, work, clock, zone, tick = env
    item = save(engine, {'kind': 'once', 'delay_seconds': 60})
    tick(3600, sleeping=True)
    assert not work.calls
    assert engine.read(item['id'])['arrangement']['status'] == 'missed'
    recurring = save(engine)
    engine.store.db.close()
    clock.now += 3600
    engine.store = __import__('engine').Store(Path(work.workspace) / 'data')
    recovered = Engine(engine.host, Path(work.workspace) / 'data', lambda: clock.now, lambda: zone.value)
    try:
        assert recovered.read(recurring['id'])['arrangement']['next_at'] == clock.now + 60
        assert not work.calls
    finally:
        recovered.close()


def test_event_source_history_once_and_repeat(env):
    engine, work, _, _, tick = env
    work.tasks['source'] = {'id': 'source', 'title': '源工作', 'state': 'completed'}
    work.event('source', 'completed')
    item = save(engine, {'kind': 'event', 'task_id': 'source', 'events': ['completed'], 'repeat': False})
    tick()
    assert not work.calls
    work.event('other', 'completed')
    tick()
    assert not work.calls
    work.event('source', 'completed')
    tick()
    assert len(work.calls) == 1
    assert 'source' in work.calls[0]['context']
    complete(engine, work, item)
    tick()
    work.event('source', 'completed')
    tick()
    assert len(work.calls) == 1
    repeat = save(engine, {'kind': 'event', 'task_id': 'source', 'events': ['waiting_user'], 'repeat': True})
    work.event('source', 'interaction')
    tick()
    complete(engine, work, repeat)
    tick()
    work.event('source', 'interaction')
    tick()
    assert len(work.calls) == 3


def test_pause_delete_failure_and_explicit_resume(env):
    engine, work, _, _, tick = env
    item = save(engine)
    tick(60)
    run = engine.read(item['id'])['runs'][0]
    engine.control(item['id'], 'pause')
    assert work.tasks[run['task_id']]['state'] == 'paused'
    tick()
    assert engine.read(item['id'])['arrangement']['status'] == 'paused'
    work.tasks[run['task_id']]['active'] = True
    with pytest.raises(ValueError, match='收尾'):
        engine.control(item['id'], 'resume')
    work.tasks[run['task_id']]['active'] = False
    engine.control(item['id'], 'resume')
    assert work.tasks[run['task_id']]['state'] == 'running'
    complete(engine, work, item)
    tick()
    tick(60)
    current = engine.read(item['id'])['runs'][0]
    work.tasks[current['task_id']]['state'] = 'waiting_user'
    tick()
    assert engine.read(item['id'])['arrangement']['status'] == 'paused'
    assert not engine.read(item['id'])['arrangement']['pending']
    with pytest.raises(ValueError, match='未完成'):
        engine.control(item['id'], 'run')
    engine.control(item['id'], 'delete')
    tick()
    assert engine.list() == []
    assert len(engine.read(item['id'])['runs']) == 2


def test_timezone_change_keeps_interval_and_moves_calendar(env):
    engine, _, _, zone, tick = env
    daily = save(engine, {'kind': 'daily', 'time': '09:00'})
    interval = save(engine)
    zone.value = ZoneInfo('Asia/Tokyo')
    tick()
    a = engine.read(daily['id'])['arrangement']
    assert datetime.fromtimestamp(a['next_at'], zone.value).hour == 9
    assert engine.read(interval['id'])['arrangement']['next_at'] == interval['next_at']


def test_always_report_and_missing_result_is_failure(env):
    engine, work, _, _, tick = env
    item = save(engine, report_mode='always')
    for _ in range(2):
        tick(60)
        complete(engine, work, item)
        tick()
    assert len(work.reports) == 2
    tick(60)
    run = engine.read(item['id'])['runs'][0]
    work.tasks[run['task_id']]['state'] = 'completed'
    tick()
    assert engine.read(item['id'])['arrangement']['status'] == 'paused'
    assert '未提交' in work.reports[-1]['text']


def test_host_page_forms_errors_history_and_archive(env):
    engine, _, _, _, tick = env
    ui = Page(engine)
    view = ui.render({'event': 'open'})
    validate_view(view)
    view = ui.render({'action': 'new'})
    validate_view(view)
    values = {v['name']: v['value'] for v in view['dialog']['components'] if v['type'] == 'field'}
    values.update(title='报告', goal='输出结果')
    view = ui.render({'action': 'save', 'values': values, 'context': view['context'], 'request_id': 'save-1'})
    validate_view(view)
    assert view['notice']['tone'] == 'success'
    item = engine.list()[0]
    view = ui.render({'action': 'edit', 'arguments': {'id': item['id']}})
    validate_view(view)
    values['goal'] = ''
    invalid = ui.render({'action': 'save', 'values': values, 'context': view['context']})
    validate_view(invalid)
    assert invalid['notice']['tone'] == 'error' and invalid['dialog']
    for kind in ('once', 'daily', 'weekdays', 'weekly', 'monthly', 'event'):
        validate_view({'ui_version': 1, 'components': [], 'dialog': ui.editor({'trigger': {'kind': kind}})})
    engine.control(item['id'], 'run', 'manual-1')
    tick()
    validate_view(ui.render({'action': 'history', 'arguments': {'id': item['id']}}))
    engine.control(item['id'], 'delete')
    validate_view(ui.render({'action': 'archive'}))


def test_validation_and_save_idempotency(env):
    engine, _, clock, zone, _ = env
    for trigger in ({'kind': 'interval', 'seconds': 0}, {'kind': 'interval', 'seconds': float('nan')},
                    {'kind': 'weekly', 'days': [], 'time': '09:00'}, {'kind': 'monthly', 'day': 32, 'time': '09:00'},
                    {'kind': 'once', 'at': 'yesterday'}):
        with pytest.raises(ValueError):
            validate(trigger, clock.now, zone.value)
    values = {'title': '任务', 'goal': '检查', 'trigger': {'kind': 'interval', 'seconds': 60}}
    first = engine.save(values, request_id='same')
    assert engine.save(values, request_id='same')['id'] == first['id']
    assert len(engine.list()) == 1


def test_pause_before_dispatch_and_edit_after_stop(env):
    engine, work, _, _, tick = env
    item = save(engine)
    engine.control(item['id'], 'run')
    engine.control(item['id'], 'pause')
    tick()
    assert not work.calls
    engine.control(item['id'], 'resume')
    tick(60)
    engine.control(item['id'], 'pause')
    tick()
    updated = engine.save({'goal': '修改后的目标'}, item['id'])
    assert updated['goal'] == '修改后的目标' and not updated['active_run']


def test_missed_once_can_be_run_explicitly(env):
    engine, work, _, _, tick = env
    item = save(engine, {'kind': 'once', 'delay_seconds': 60})
    tick(3600, sleeping=True)
    engine.control(item['id'], 'run')
    tick()
    assert len(work.calls) == 1
    complete(engine, work, item)
    tick()
    assert engine.read(item['id'])['arrangement']['status'] == 'completed'


def test_restart_preserves_current_once_work_for_explicit_resume(env):
    engine, work, clock, zone, tick = env
    item = save(engine, {'kind': 'once', 'delay_seconds': 60})
    tick(60)
    original = engine.read(item['id'])['runs'][0]
    work.tasks[original['task_id']]['state'] = 'paused'
    recovered = Engine(engine.host, Path(work.workspace) / 'data', lambda: clock.now, lambda: zone.value)
    try:
        restored = recovered.read(item['id'])['arrangement']
        assert restored['active_run'] == original['id'] and restored['status'] == 'paused'
        assert len(work.calls) == 1
        recovered.control(item['id'], 'resume')
        assert work.tasks[original['task_id']]['state'] == 'running'
        complete(recovered, work, item)
        recovered.tick()
        assert recovered.read(item['id'])['arrangement']['status'] == 'completed'
        assert len(work.calls) == 1
    finally:
        recovered.close()
