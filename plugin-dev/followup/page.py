"""Host UI v1 descriptions; layout, themes and motion remain host-owned."""
import math
from datetime import datetime

from timing import describe

KINDS = [('once', '单次'), ('interval', '固定间隔'), ('daily', '每天'), ('weekdays', '工作日'),
         ('weekly', '每周'), ('monthly', '每月'), ('event', '工作事件')]
EVENT_LABELS = {'completed': '完成', 'paused': '挂起', 'failed': '失败', 'cancelled': '取消',
                'waiting_user': '等待决定', 'blocked': '遇到阻碍'}
STATES = {'active': '启用', 'paused': '已暂停', 'completed': '已完成', 'missed': '已错过', 'deleted': '已删除',
          'queued': '排队', 'running': '执行中', 'waiting_user': '等待决定', 'failed': '失败',
          'interrupted': '已中断', 'cancelled': '已取消'}


def button(key, label, action, arguments=None, **kw):
    return {'id': key, 'type': 'button', 'label': label, 'action': action, 'arguments': arguments or {}, **kw}


def field(name, label, value, control='text', **kw):
    return {'id': 'field-' + name, 'type': 'field', 'name': name, 'label': label, 'value': value, 'control': control, **kw}


def options(values):
    return [{'value': value, 'label': label} for value, label in values]


class Page:
    def __init__(self, engine):
        self.engine = engine

    def stamp(self, value):
        return datetime.fromtimestamp(value, self.engine.zone()).strftime('%Y-%m-%d %H:%M:%S') if value is not None else '—'

    def render(self, request):
        with self.engine.lock:
            return self._render(request)

    def _render(self, request):
        action = request.get('action', '')
        args = request.get('arguments') or {}
        ctx = dict(request.get('context') or {})
        values = request.get('values') or {}
        dialog = None
        notice = None
        error = None
        key = args.get('id') or ctx.get('editing')
        if action in ('pause', 'resume', 'delete', 'run'):
            self.engine.control(args['id'], action, request.get('request_id'))
            notice = '操作已保存。'
        elif action in ('new', 'edit', 'kind'):
            if action == 'new':
                ctx.pop('editing', None)
                draft = {}
            elif action == 'edit':
                ctx['editing'] = key
                draft = self.engine.read(key)['arrangement']
            else:
                draft = self._draft(values)
            dialog = self.editor(draft)
        elif action == 'save':
            try:
                self.engine.save(self._draft(values), ctx.get('editing'), request.get('request_id'))
                ctx.pop('editing', None)
                notice = '安排已保存，等待下一次触发。'
            except (TypeError, ValueError) as exc:
                dialog = self.editor(self._draft(values, strict=False))
                error = str(exc)
        elif action in ('history', 'run-detail'):
            ctx.pop('editing', None)
            if action == 'history':
                ctx['history'] = key
                runs = self.engine.read(key)['runs']
                history_page = max(1, int(args.get('page', 1)))
                total = max(1, math.ceil(len(runs) / 20))
                history_page = min(history_page, total)
                dialog = {'id': 'history-dialog', 'title': '运行记录', 'components': [
                    {'id': 'run-list', 'type': 'list', 'empty': '尚无运行记录。', 'items': [
                        {'id': 'run-' + r['id'], 'title': STATES.get(r['status'], r['status']),
                         'text': (r.get('result') or {}).get('summary') or r.get('error') or '',
                         'meta': self.stamp(r['created_at']) + ' · ' + r['trigger']['kind'],
                         'actions': [button('detail-' + r['id'], '详情', 'run-detail', {'id': key, 'run_id': r['id']})]}
                        for r in runs[(history_page - 1) * 20:history_page * 20]]},
                    {'id': 'history-pages', 'type': 'pagination', 'page': history_page, 'total_pages': total,
                     'action': 'history', 'arguments': {'id': key}}]}
            else:
                run = next(r for r in self.engine.read(key)['runs'] if r['id'] == args['run_id'])
                import json
                components = [{'id': 'run-data', 'type': 'code', 'text': json.dumps(run, ensure_ascii=False, indent=2)}]
                if run.get('task_id'):
                    work = self.engine.host.work.get(task_id=run['task_id'], messages=True)
                    components.append({'id': 'work-data', 'type': 'code', 'text': json.dumps(work, ensure_ascii=False, indent=2)})
                dialog = {'id': 'detail-dialog', 'title': '本轮详情与工作记录', 'components': components}
        if action == 'page':
            ctx['page'] = args.get('page', 1)
        all_items = self.engine.list(include_deleted=True)
        rows = [v for v in all_items if v['status'] != 'deleted']
        page = min(max(1, int(ctx.get('page', 1))), max(1, math.ceil(len(rows) / 20)))
        ctx['page'] = page
        components = [{'id': 'intro', 'type': 'text', 'text': '约定到时执行，关注有事再说。系统时区：' + str(self.engine.zone())},
                      {'id': 'toolbar', 'type': 'row', 'children': [button('new', '新建安排', 'new', variant='primary'),
                       button('refresh', '刷新', 'refresh'), button('archive', '已删除记录', 'archive')]}]
        if self.engine.last_error:
            components.append({'id': 'scheduler-error', 'type': 'text', 'tone': 'error', 'text': self.engine.last_error})
        components.append({'id': 'arrangements', 'type': 'list', 'empty': '还没有安排。可以在这里新建，也可以直接交代给灵。',
                           'items': [self.item(v) for v in rows[(page - 1) * 20:page * 20]]})
        components.append({'id': 'pages', 'type': 'pagination', 'page': page, 'total_pages': max(1, math.ceil(len(rows) / 20)), 'action': 'page'})
        if action == 'archive':
            dialog = {'id': 'archive-dialog', 'title': '已删除安排的历史', 'components': [
                {'id': 'archived-list', 'type': 'list', 'empty': '没有已删除安排。', 'items': [
                    {'id': 'deleted-' + v['id'], 'title': v['title'], 'text': v['goal'],
                     'actions': [button('deleted-history-' + v['id'], '运行记录', 'history', {'id': v['id']})]}
                    for v in all_items if v['status'] == 'deleted']} ]}
        view = {'ui_version': 1, 'context': ctx, 'components': components}
        if dialog:
            if error:
                dialog['components'].insert(0, {'id': 'editor-error', 'type': 'text', 'tone': 'error', 'text': error})
            view['dialog'] = dialog
        if notice or error:
            view['notice'] = {'text': error or notice, 'tone': 'error' if error else 'success'}
        return view

    def item(self, item):
        key = item['id']
        state = STATES[item['status']]
        if item.get('active_run'):
            run = self.engine.store.get('runs', item['active_run'])
            state += ' · ' + STATES.get(run['status'], run['status'])
        if item.get('pending'):
            state += ' · 待执行一次'
        actions = [button('edit-' + key, '编辑', 'edit', {'id': key}),
                   button('history-' + key, '运行记录', 'history', {'id': key})]
        if item['status'] not in ('completed', 'missed'):
            operation = 'pause' if item['status'] == 'active' else 'resume'
            actions.append(button(operation + '-' + key, '暂停' if operation == 'pause' else '恢复', operation, {'id': key}))
        if item['status'] != 'completed':
            actions.append(button('run-' + key, '立即运行', 'run', {'id': key},
                                  confirm={'title': '立即执行一次？', 'description': '将执行安排中的实际工作。'}))
        actions.append(button('delete-' + key, '删除', 'delete', {'id': key}, variant='danger',
                              confirm={'title': '删除安排并停止当前执行？', 'description': '保留已执行的操作和历史记录。'}))
        return {'id': 'arrangement-' + key, 'title': item['title'],
                'text': item['goal'] + '\n' + (item['last_result'] or '尚无结果'),
                'meta': state + ' · ' + describe(item['trigger']) + ' · 下次：' + self.stamp(item.get('next_at')),
                'actions': actions}

    def editor(self, item):
        defaults = self.engine.host.work.defaults()
        trigger = item.get('trigger') or {'kind': 'interval', 'seconds': 3600}
        kind = trigger.get('kind', 'interval')
        models = [m for m in defaults['models'] if m['configured']]
        selected_model = json_model(item.get('provider_id', defaults['provider_id']), item.get('model_id', defaults['model_id']))
        children = [field('title', '名称', item.get('title', '')),
                    field('goal', '要做的事', item.get('goal', ''), 'textarea'),
                    field('context', '必要背景', item.get('context', ''), 'textarea'),
                    field('kind', '触发方式', kind, 'select', options=options(KINDS)),
                    button('kind-update', '更新触发字段', 'kind')]
        if kind == 'once':
            children += [field('at', '执行日期时间', trigger.get('at', ''), help='系统本地时间，例如 2026-11-05T09:00；或填写下面的延迟。'),
                         field('delay_seconds', '延迟秒数（可选）', trigger.get('delay_seconds'), 'number', min=1)]
        elif kind == 'interval':
            children.append(field('seconds', '间隔秒数', trigger.get('seconds', 3600), 'number', min=1, required=True))
        elif kind == 'event':
            works = self.engine.host.work.list()
            children += [field('task_id', '指定后台工作', trigger.get('task_id', ''), 'select',
                               options=options([('', '请选择')] + [(w['id'], w['title'] + ' · ' + w['id']) for w in works]))]
            children += [field('event-' + k, label, k in trigger.get('events', []), 'switch') for k, label in EVENT_LABELS.items()]
            children.append(field('repeat', '持续监听', trigger.get('repeat', False), 'switch'))
        else:
            children.append(field('time', '当地时间 HH:MM', trigger.get('time', '09:00'), required=True))
            if kind == 'weekly':
                children += [field('weekday-' + str(d), '周' + '一二三四五六日'[d], d in trigger.get('days', [0]), 'switch') for d in range(7)]
            if kind == 'monthly':
                children.append(field('day', '每月日期（0 表示最后一天）', trigger.get('day', 1), 'number', min=0, max=31, step=1, required=True))
        children += [field('report_mode', '汇报方式', item.get('report_mode', 'changes'), 'select',
                           options=options([('changes', '首轮基线，之后有事再汇报'), ('always', '每次汇报')])),
                     field('report_when', '哪些情况值得告诉我', item.get('report_when', ''), 'textarea'),
                     field('end_when', '何时结束关注', item.get('end_when', ''), 'textarea'),
                     field('workspace', '项目目录', item.get('workspace', defaults['workspace']), required=True),
                     field('model', '模型', selected_model, 'select',
                           options=[{'value': json_model(m['provider_id'], m['model_id']), 'label': m['provider'] + ' / ' + m['model_id']} for m in models], required=True),
                     field('effort', '思考强度', item.get('effort', defaults['effort']) or '', 'select',
                           options=options([('', '提供商默认'), ('low', '低'), ('medium', '中'), ('high', '高'), ('xhigh', '很高'), ('max', '最高')])),
                     button('save-arrangement', '保存安排', 'save', variant='primary')]
        return {'id': 'editor-dialog', 'title': '编辑安排' if item.get('id') else '新建安排', 'components': children}

    def _draft(self, values, strict=True):
        import json
        kind = values.get('kind', 'interval')
        trigger = {'kind': kind}
        if kind == 'once':
            if values.get('delay_seconds'):
                trigger['delay_seconds'] = values['delay_seconds']
            else:
                trigger['at'] = values.get('at', '')
        elif kind == 'interval':
            trigger['seconds'] = values.get('seconds')
        elif kind == 'event':
            trigger.update(task_id=values.get('task_id', ''), events=[k for k in EVENT_LABELS if values.get('event-' + k)],
                           repeat=values.get('repeat', False))
        else:
            trigger['time'] = values.get('time', '09:00')
            if kind == 'weekly':
                trigger['days'] = [d for d in range(7) if values.get('weekday-' + str(d))]
            elif kind == 'monthly':
                day = values.get('day', 1)
                if strict and (type(day) not in (int, float) or int(day) != day):
                    raise ValueError('每月日期须为整数')
                trigger['day'] = int(day) if type(day) in (int, float) else day
        provider, model = json.loads(values.get('model') or '["", ""]')
        return {**{k: values.get(k, '') for k in ('title', 'goal', 'context', 'report_when', 'end_when', 'workspace')},
                'report_mode': values.get('report_mode', 'changes'), 'trigger': trigger,
                'provider_id': provider, 'model_id': model, 'effort': values.get('effort') or None}


def json_model(provider, model):
    import json
    return json.dumps([provider, model], ensure_ascii=False)
