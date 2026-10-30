"""Version-one host-rendered pages. Content is data; presentation belongs to Nexus."""
from __future__ import annotations

import json


def declarations(pages):
    if not isinstance(pages, list):
        raise ValueError('插件 pages 必须是数组')
    seen = set()
    for page in pages:
        if not isinstance(page, dict):
            raise ValueError('插件页面声明必须是对象')
        renderer = page.get('renderer', 'iframe')
        if renderer == 'host':
            if not isinstance(page.get('id'), str) or not page['id']:
                raise ValueError('宿主渲染页面需要非空 id')
            if page['id'] in seen:
                raise ValueError('插件页面 id 重复')
            seen.add(page['id'])
            if type(page.get('ui_version')) is not int or page['ui_version'] != 1:
                raise ValueError('不支持此插件页面的 UI 协议版本')
            if not isinstance(page.get('action'), str) or not page['action']:
                raise ValueError('宿主渲染页面需要 action')
        elif renderer != 'iframe' or not isinstance(page.get('path'), str) or not page['path']:
            raise ValueError('自定义插件页面需要 path，renderer 仅支持 host 或 iframe')
    host_ids = [p['id'] for p in pages if p.get('renderer') == 'host']
    if any(sum(p.get('id') == key for p in pages) != 1 for key in host_ids):
        raise ValueError('插件页面 id 重复')


COMMON = {'id', 'type'}
PROPS = {
    'section': {'title', 'description', 'children'},
    'row': {'children'}, 'column': {'children'},
    'text': {'text', 'tone'}, 'badge': {'text', 'tone'}, 'code': {'text'},
    'field': {'name', 'label', 'control', 'value', 'placeholder', 'help', 'required',
              'readonly', 'disabled', 'min', 'max', 'step', 'options'},
    'button': {'label', 'action', 'arguments', 'variant', 'disabled', 'confirm'},
    'list': {'items', 'empty'}, 'table': {'columns', 'rows', 'empty'},
    'pagination': {'page', 'total_pages', 'action', 'arguments'},
}


def validate_view(view):
    """Check the wire contract before a response can reach the renderer."""
    def fail(message):
        raise ValueError('插件页面格式错误：' + message)

    def object_keys(value, allowed, label):
        if not isinstance(value, dict) or set(value) - allowed:
            fail(label + ' 包含不支持的属性或不是对象')

    def string(value, label):
        if not isinstance(value, str): fail(label + ' 必须是文本')

    def scalar(value):
        return value is None or type(value) in (str, bool, int, float)

    ids, fields = set(), set()

    def identity(value):
        string(value, 'id')
        if not value or value in ids: fail('组件 id 为空或重复：' + value)
        ids.add(value)

    def action(value):
        string(value.get('action'), 'action')
        if not value['action']: fail('action 不能为空')
        if not isinstance(value.get('arguments', {}), dict): fail('arguments 必须是对象')

    def nodes(values):
        if not isinstance(values, list): fail('组件集合必须是数组')
        for node in values: component(node)

    def component(node):
        if not isinstance(node, dict) or node.get('type') not in PROPS:
            fail('未知组件类型')
        kind = node['type']
        object_keys(node, COMMON | PROPS[kind], kind)
        identity(node.get('id'))
        for key in ('title', 'description', 'text', 'label', 'placeholder', 'help', 'empty'):
            if key in node: string(node[key], key)
        if 'tone' in node and node['tone'] not in ('default', 'muted', 'success', 'error'):
            fail('未知 tone')
        for key in ('disabled', 'readonly', 'required'):
            if key in node and type(node[key]) is not bool: fail(key + ' 必须是布尔值')
        if kind in ('section', 'row', 'column'):
            nodes(node.get('children', []))
        elif kind in ('text', 'badge', 'code'):
            string(node.get('text'), 'text')
        elif kind == 'button':
            string(node.get('label'), 'label'); action(node)
            if node['action'].startswith('$') and node['action'] != '$close': fail('未知宿主本地操作')
            if node.get('variant', 'secondary') not in ('primary', 'secondary', 'danger'):
                fail('未知按钮 variant')
            if 'confirm' in node:
                object_keys(node['confirm'], {'title', 'description', 'submitLabel'}, 'confirm')
                for k, v in node['confirm'].items(): string(v, k)
                string(node['confirm'].get('title'), 'confirm.title')
        elif kind == 'field':
            name = node.get('name')
            string(name, 'name'); string(node.get('label'), 'label')
            if not name or name in fields: fail('字段 name 为空或重复')
            fields.add(name)
            control = node.get('control', 'text')
            if control not in ('text', 'textarea', 'number', 'select', 'switch'):
                fail('未知表单控件')
            if 'value' in node:
                value = node['value']
                if not scalar(value): fail('字段值必须是标量')
                if control == 'switch' and type(value) is not bool: fail('开关值必须是布尔值')
                if control in ('text', 'textarea') and not isinstance(value, str): fail('文本字段值必须是文本')
                if control == 'number' and value is not None and type(value) not in (int, float): fail('数字字段值必须是数字或 null')
            for k in ('min', 'max', 'step'):
                if k in node and type(node[k]) not in (int, float): fail(k + ' 必须是数字')
            options = node.get('options', [])
            if not isinstance(options, list): fail('options 必须是数组')
            for option in options:
                object_keys(option, {'label', 'value'}, 'option')
                string(option.get('label'), 'option.label')
                if 'value' not in option or not scalar(option['value']): fail('选项需要标量 value')
        elif kind == 'list':
            items = node.get('items', [])
            if not isinstance(items, list): fail('items 必须是数组')
            for item in items:
                object_keys(item, {'id', 'title', 'text', 'meta', 'actions'}, 'item')
                identity(item.get('id'))
                string(item.get('title'), 'item.title')
                for k in ('text', 'meta'):
                    if k in item: string(item[k], k)
                buttons(item.get('actions', []))
        elif kind == 'table':
            columns, rows = node.get('columns'), node.get('rows', [])
            if not isinstance(columns, list) or not isinstance(rows, list): fail('table 需要 columns 和 rows 数组')
            keys = set()
            for col in columns:
                object_keys(col, {'key', 'label'}, 'column')
                string(col.get('key'), 'column.key'); string(col.get('label'), 'column.label')
                if not col['key'] or col['key'] in keys: fail('表格列 key 为空或重复')
                keys.add(col['key'])
            for row in rows:
                object_keys(row, {'id', 'cells', 'actions'}, 'row')
                identity(row.get('id'))
                if not isinstance(row.get('cells'), dict) or set(row['cells']) - keys or not all(scalar(v) for v in row['cells'].values()):
                    fail('表格 cells 必须是已声明列的标量映射')
                buttons(row.get('actions', []))
        elif kind == 'pagination':
            action(node)
            if any(type(node.get(k)) is not int or node[k] < 1 for k in ('page', 'total_pages')) or node['page'] > node['total_pages']:
                fail('分页需要有效的 page 和 total_pages')

    def buttons(values):
        if not isinstance(values, list) or any(not isinstance(b, dict) or b.get('type') != 'button' for b in values):
            fail('actions 只能包含按钮')
        nodes(values)

    object_keys(view, {'ui_version', 'context', 'components', 'dialog', 'notice', 'errors'}, '页面')
    if type(view.get('ui_version')) is not int or view['ui_version'] != 1: fail('不支持的 ui_version')
    if not isinstance(view.get('context', {}), dict): fail('context 必须是对象')
    nodes(view.get('components'))
    dialog = view.get('dialog')
    if dialog is not None:
        object_keys(dialog, {'id', 'title', 'description', 'components'}, 'dialog')
        identity(dialog.get('id')); string(dialog.get('title'), 'dialog.title')
        if 'description' in dialog: string(dialog['description'], 'dialog.description')
        nodes(dialog.get('components', []))
    if 'notice' in view:
        object_keys(view['notice'], {'text', 'tone'}, 'notice')
        string(view['notice'].get('text'), 'notice.text')
        if view['notice'].get('tone', 'success') not in ('success', 'error'): fail('未知 notice.tone')
    if not isinstance(view.get('errors', {}), dict): fail('errors 必须是对象')
    for k, v in view.get('errors', {}).items():
        if k not in fields: fail('errors 引用了不存在的字段')
        string(v, 'errors 值')
    try: json.dumps(view, allow_nan=False)
    except (ValueError, TypeError) as error: fail('必须是有效 JSON：' + str(error))
    return view
