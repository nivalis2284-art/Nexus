"""Example UI controller. Returns data only; Nexus owns DOM, styles and motion."""
import json
from pathlib import Path


def render_page(host, call, request):
    view = json.loads(Path(__file__).with_name('view.json').read_text(encoding='utf-8'))
    context = dict(request.get('context', {}))
    values, args = request.get('values', {}), request.get('arguments', {})
    action = request.get('action') if request['event'] == 'action' else None
    editing = action == 'edit-note'
    models = host.models.list()
    generation = [m for m in models if m['kind'] == 'generation' and m.get('configured')]
    context['model'] = values.get('model', context.get('model', generation[0]['id'] if generation else None))
    context['message'] = values.get('message', context.get('message', '请简短打招呼。'))
    settings = host.storage.settings.get()
    note = settings.get('example_note', '这是可以编辑的示例备注。')
    if action == 'edit-note':
        pass
    elif action == 'save-note':
        note = values.get('note', '')
        if not note.strip():
            view['errors'] = {'note': '请填写备注。'}
            editing = True
        else:
            host.storage.settings.save(values={**settings, 'example_note': note})
            view['notice'] = {'text': '示例备注已保存。', 'tone': 'success'}
    elif action == 'models-page':
        context['page'] = args['page']
    elif action:
        params = {'request_id': request['request_id']}
        if action in ('generate', 'stream', 'dialogue'):
            model = next((m for m in generation if m['id'] == context['model']), None)
            if not model: raise ValueError('请先在设置中配置可用模型。')
            params.update(provider_id=model['provider_id'], model_id=model['model_id'])
        if action in ('generate', 'stream'):
            params['messages'] = [{'role': 'user', 'content': context['message']}]
        if action == 'dialogue': params.pop('request_id')
        if action == 'send':
            if not context.get('dialogue_id'): raise ValueError('请先创建独立对话。')
            params.update(dialogue_id=context['dialogue_id'], text=context['message'])
        result = call(action, params)
        if action == 'dialogue': context['dialogue_id'] = result['id']
        context['result'] = result

    def walk(nodes):
        for node in nodes:
            yield node
            yield from walk(node.get('children', []))

    nodes = {n['id']: n for n in walk(view['components'])}
    nodes['model']['options'] = [{'label': m['provider'] + ' · ' + m['model_id'], 'value': m['id']} for m in generation]
    nodes['model']['value'] = context['model']
    nodes['message']['value'] = context['message']
    nodes['dialogue-state']['text'] = context.get('dialogue_id', '尚未创建独立对话。')
    nodes['result']['text'] = json.dumps(context.get('result', {}), ensure_ascii=False, indent=2)
    nodes['notes']['items'][0]['text'] = settings.get('example_note', '这是可以编辑的示例备注。')
    if action == 'save-note' and 'errors' not in view: nodes['notes']['items'][0]['text'] = note
    pages = max(1, (len(models) + 4) // 5)
    page = min(pages, max(1, int(context.get('page', 1))))
    nodes['model-pages'].update(page=page, total_pages=pages)
    nodes['model-table']['rows'] = [{'id': 'model-row-' + str(i), 'cells': {
        'provider': m.get('provider', m.get('name', '')), 'model': m['model_id'], 'kind': m['kind'],
        'status': '已配置' if m.get('configured') else '未配置',
    }} for i, m in enumerate(models) if (page - 1) * 5 <= i < page * 5]
    if editing:
        view['dialog'] = {'id': 'note-editor', 'title': '编辑示例备注', 'description': '内容由插件保存，弹窗与动效由 Nexus 提供。', 'components': [
            {'id': 'note', 'type': 'field', 'control': 'textarea', 'name': 'note', 'label': '备注', 'value': values.get('note', note)},
            {'id': 'note-save', 'type': 'button', 'label': '保存', 'action': 'save-note', 'variant': 'primary'},
        ]}
    view['context'] = context
    return view
