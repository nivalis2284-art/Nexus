// This controller produces data only. No HTML, CSS, DOM or animation code.
import {readFileSync} from 'node:fs';

export async function renderPage(host, call, request) {
  const view = JSON.parse(readFileSync(new URL('./view.json', import.meta.url), 'utf8'));
  const context = {...request.context}, values = request.values || {}, args = request.arguments || {};
  const action = request.event === 'action' ? request.action : null;
  let editing = action === 'edit-note';
  const models = await host.models.list();
  const generation = models.filter(m => m.kind === 'generation' && m.configured);
  context.model = Object.hasOwn(values, 'model') ? values.model : context.model ?? generation[0]?.id ?? null;
  context.message = values.message ?? context.message ?? '请简短打招呼。';
  const settings = await host.storage.settings.get();
  let note = settings.example_note ?? '这是可以编辑的示例备注。';
  if (action === 'edit-note') { /* Opening a dialog is a view change, not durable state. */ }
  else if (action === 'save-note') {
    note = values.note || '';
    if (!note.trim()) { view.errors = {note: '请填写备注。'}; editing = true; }
    else {
      await host.storage.settings.save({values: {...settings, example_note: note}});
      view.notice = {text: '示例备注已保存。', tone: 'success'};
    }
  } else if (action === 'models-page') context.page = args.page;
  else if (action) {
    const params = {request_id: request.request_id};
    if (['generate', 'stream', 'dialogue'].includes(action)) {
      const model = generation.find(m => m.id === context.model);
      if (!model) throw Error('请先在设置中配置可用模型。');
      Object.assign(params, {provider_id: model.provider_id, model_id: model.model_id});
    }
    if (['generate', 'stream'].includes(action)) params.messages = [{role: 'user', content: context.message}];
    if (action === 'dialogue') delete params.request_id;
    if (action === 'send') {
      if (!context.dialogue_id) throw Error('请先创建独立对话。');
      Object.assign(params, {dialogue_id: context.dialogue_id, text: context.message});
    }
    const result = await call(action, params);
    if (action === 'dialogue') context.dialogue_id = result.id;
    context.result = result;
  }
  function* walk(nodes) { for (const n of nodes) { yield n; yield* walk(n.children || []); } }
  const nodes = Object.fromEntries([...walk(view.components)].map(n => [n.id, n]));
  nodes.model.options = generation.map(m => ({label: `${m.provider} · ${m.model_id}`, value: m.id}));
  nodes.model.value = context.model; nodes.message.value = context.message;
  nodes['dialogue-state'].text = context.dialogue_id || '尚未创建独立对话。';
  nodes.result.text = JSON.stringify(context.result ?? {}, null, 2);
  nodes.notes.items[0].text = action === 'save-note' && !view.errors ? note : settings.example_note ?? '这是可以编辑的示例备注。';
  const pages = Math.max(1, Math.ceil(models.length / 5));
  const page = Math.min(pages, Math.max(1, Number(context.page || 1)));
  Object.assign(nodes['model-pages'], {page, total_pages: pages});
  nodes['model-table'].rows = models.flatMap((m, i) => (page - 1) * 5 <= i && i < page * 5 ? [{id: `model-row-${i}`, cells: {
    provider: m.provider ?? m.name ?? '', model: m.model_id, kind: m.kind, status: m.configured ? '已配置' : '未配置',
  }}] : []);
  if (editing) view.dialog = {id: 'note-editor', title: '编辑示例备注', description: '内容由插件保存，弹窗与动效由 Nexus 提供。', components: [
    {id: 'note', type: 'field', control: 'textarea', name: 'note', label: '备注', value: values.note ?? note},
    {id: 'note-save', type: 'button', label: '保存', action: 'save-note', variant: 'primary'},
  ]};
  view.context = context;
  return view;
}
