/* 演示适配层：只改变内存中的样本，不包含 HTTP、SSE 或真实业务执行。 */
'use strict';
(function (P, N) {
  const s = N.state;
  P.later = (fn, ms) => { const id = setTimeout(() => { P.timers.delete(id); fn(); }, ms); P.timers.add(id); return id; };
  P.cancel = () => { P.timers.forEach(clearTimeout); P.timers.clear(); };
  const clone = value => JSON.parse(JSON.stringify(value));
  const delay = () => new Promise(resolve => setTimeout(resolve, 140));
  const repaint = () => { N.composeItems(); N.renderChat?.(); N.renderAll?.(); };
  N.composeItems = () => { s.items = [...s.baseItems, ...s.pending, ...s.liveItems]; };
  N.mapTask = x => x; N.clock = x => x || '10:20';
  N.sync = {
    settings(value) { if (value) for (const key of ['providers', 'current', 'permission', 'workspace', 'projects', 'project', 'defaultProject', 'effort']) if (value[key] !== undefined) s[key] = value[key]; N.renderProjects?.(); N.renderProjectButton?.(); },
    chat() { N.composeItems(); }, modelRequests(value) { s.modelRequests = value.requests || []; },
  };
  for (const key of ['all', 'chatOnly', 'settingsOnly', 'contextOnly', 'worksOnly', 'skillsOnly', 'mcpOnly', 'pluginsOnly', 'instructionsOnly', 'modelRequestsOnly']) N.sync[key] = async () => { N.composeItems(); return s; };
  N.sync.taskDetail = async id => s.works.find(w => w.id === id);
  const remember = () => { P.history = clone({ baseItems: s.baseItems, contextData: s.contextData }); };
  const finish = stopped => {
    s.replying = s.running = false;
    s.baseItems = s.baseItems.map(item => item.live ? { ...item, live: false, elapsed: stopped ? 1.2 : 4.8 } : item);
    repaint(); N.updateSend?.();
  };
  function reply() {
    s.replying = s.running = true;
    const key = N.uid('preview-answer');
    s.baseItems.push({ key: 'thinking-' + key, role: 'thinking', thinking: '演示：检查设计场景，整理可复用的界面细节。', elapsed: 1.2 });
    repaint(); N.updateSend?.();
    P.later(() => {
      s.baseItems.push({ key: 'tool-' + key, role: 'tool', name: 'read', callId: N.uid('call'), arguments: { path: '示例项目/设计.md' }, text: '这是工具过程的静态样本，没有读取实际文件。', ok: true });
      const item = { key, index: s.baseItems.length, role: 'assistant', at: N.nowHM(), text: '', live: true };
      s.baseItems.push(item);
      const chars = Array.from(P.markdown); let i = 0;
      const tick = () => {
        i = Math.min(chars.length, i + 12);
        s.baseItems = s.baseItems.map(x => x.key === key ? { ...x, text: chars.slice(0, i).join('') } : x);
        N.composeItems(); N.renderChat(); N.refreshIsland?.();
        if (i < chars.length) P.later(tick, 65); else finish(false);
      };
      tick();
    }, 600);
  }
  const mutations = {
    input(text, options = {}) {
      const pending = s.pending.find(x => x.requestId === options.requestId);
      pending?.attachments?.forEach(file => { if (file.url?.startsWith('blob:')) P.files.set(file.url, file.url); });
      s.pending = s.pending.filter(x => x !== pending);
      s.baseItems.push({ ...(pending || {}), key: pending?.key || N.uid('user'), index: s.baseItems.length, requestId: options.requestId, role: 'user', text, at: N.nowHM() });
      if (!s.replying) reply(); else s.queue.push({ id: Date.now(), text, status: 'queued', kind: 'followup' });
      N.composeItems(); return { accepted: true };
    },
    stop() { P.cancel(); finish(true); return {}; },
    dismissInput(id) { s.queue = s.queue.filter(x => x.id !== id); return {}; },
    resume() { s.held = false; s.queue = []; return {}; },
    pauseAll() { s.works.forEach(w => { if (['running', 'queued', 'waiting_user'].includes(w.state)) w.state = 'paused'; }); return {}; },
    approval(token) { s.requests = s.requests.filter(x => (x.token || x.id) !== token); return {}; },
    answer(token) { return mutations.approval(token); }, reviewUnknown(token) { return mutations.approval(token); },
    taskAnswer(id, interactionId) { const w = s.works.find(x => x.id === id); if (w) { w.pending_interactions = w.pending_interactions.filter(x => x.id !== interactionId); w.state = 'running'; } return {}; },
    taskCommand(id, body) { const w = s.works.find(x => x.id === id); if (w) { const action = body.action || body.operation || ''; if (action === 'archive' || action === 'unarchive') w.archived = action === 'archive'; else w.state = /pause/.test(action) ? 'paused' : 'running'; } return {}; },
    taskLifecycle(id, body) { const w = s.works.find(x => x.id === id); if (w) w.archived = body.operation === 'archive'; return {}; },
    history(body) {
      if (body.operation === 'undo') { if (P.history) Object.assign(s, clone(P.history)); P.history = null; }
      else { remember(); s.baseItems = s.baseItems.filter(x => !(body.indices || []).includes(x.index)); s.contextData.turns = s.contextData.turns.filter(t => !t.indices.some(i => (body.indices || []).includes(i))); if (body.operation === 'resend') { s.baseItems.push({ key: N.uid('edited'), role: 'user', text: body.text, index: s.baseItems.length, at: N.nowHM() }); reply(); } }
      N.composeItems(); return {};
    },
    saveContext(section, body) { if (section === 'owners') s.contextData.owner_instructions = body.items; if (section === 'roles') { const role = s.contextData.roles.find(r => r.id === body.id); if (role) role.text = body.text; } if (section === 'summary') { remember(); s.contextData.summary.text = body.text; } return {}; },
    saveInstructions(text) { s.instructions = text; return {}; },
    select(body) { Object.assign(s.current, body); return {}; },
    settings(body) { Object.assign(s, body); return {}; },
    addProvider(body) { s.providers.push({ ...body, id: N.uid('provider'), has_key: false }); return {}; },
    updateProvider(id, body) { Object.assign(s.providers.find(x => x.id === id), body); return {}; },
    deleteProvider(id) { s.providers = s.providers.filter(x => x.id !== id); return {}; },
    projectCommand(body) {
      if (body.action === 'delete') { s.projects = s.projects.filter(x => x.path !== body.path); if (s.workspace === body.path) { s.project = s.defaultProject; s.workspace = s.defaultProject.path; } }
      else if (body.action === 'rename') { const p = s.projects.find(x => x.path === body.path); if (p) p.name = body.name; }
      else { let p = [s.defaultProject, ...s.projects].find(x => x.path === body.path); if (!p) { p = { name: body.name || '设计项目', path: body.path }; s.projects.push(p); } s.project = p; s.workspace = p.path; }
      return s;
    },
    chooseProjectFolder() { N.notify?.('演示：使用虚构文件夹，不会打开系统目录选择器。', 'jade'); return { path: '示例项目/新建项目', name: '新建项目' }; },
    saveSkill(body) { const i = s.skills.findIndex(x => x.path === body.path && x.source === body.source); const v = { ...body, path: body.path || N.uid('skill'), effective: true, read_only: false }; if (i >= 0) s.skills[i] = v; else s.skills.push(v); return {}; },
    deleteSkill(body) { s.skills = s.skills.filter(x => x.path !== body.path); return {}; },
    saveMcp(body) { const i = s.mcp.findIndex(x => x.name === (body.original_name || body.name)); if (i >= 0) Object.assign(s.mcp[i], body); else s.mcp.push({ ...body, connected: true, tool_count: 2 }); return {}; },
    mcpCommand(name, action) { if (action === 'delete') s.mcp = s.mcp.filter(x => x.name !== name); else { const m = s.mcp.find(x => x.name === name); if (m) { m.connected = true; m.error = ''; } } return {}; },
    pluginScan() { return {}; },
    pluginCommand(id, action) { const p = s.plugins.find(x => x.id === id); if (action === 'uninstall') s.plugins = s.plugins.filter(x => x.id !== id); else if (p) { p.enabled = action !== 'unload'; p.status = p.enabled ? 'ready' : 'unloaded'; p.error = ''; } return {}; },
    pluginSettings(id, body) { s.plugins.find(x => x.id === id).settings = body; return {}; },
    importPlugin() { N.notify?.('演示：仅展示导入成功反馈，不解压或执行 ZIP。'); return { id: s.plugins[0]?.id }; },
    hookSettings(body) { const h = P.hooks.find(x => x.name === body.name); const t = body.plugin_id ? h.subscribers.find(x => x.plugin_id === body.plugin_id) : h; t.enabled = body.enabled; return { hooks: P.hooks }; },
  };
  N.api = {};
  for (const [name, action] of Object.entries(mutations)) N.api[name] = async (...args) => { await delay(); if (P.failNext) { P.failNext = false; throw new Error('演示失败：输入保留，可再次提交。'); } return action(...args); };
  Object.assign(N.api, {
    ling: async () => ({}), hooks: async () => ({ hooks: P.hooks }),
    modelRequest: async id => s.modelRequests.find(x => String(x.id) === String(id)),
    modelRequests: async () => ({ requests: s.modelRequests, next: null }),
    upload: async (name, blob) => { const id = N.uid('attachment'); const url = URL.createObjectURL(blob); P.files.set(id, url); return { id, name, url }; },
    get: async path => path.startsWith('/api/workspace-file') ? { text: '# 设计示例\n\n这里只展示文件预览，不读取磁盘。', path: '示例项目/说明.md' } : {},
    del: async path => { const id = path.split('/').pop(); s.works = s.works.filter(w => w.id !== id); return {}; },
  });
  P.hostView = action => ({
    ui_version: 1, title: '插件宿主设计组件', context: {}, errors: action === 'validate' ? { name: '演示字段错误：请检查输入。' } : {},
    notice: action === 'save' ? { text: '演示保存完成，仅保存在本页。', tone: 'success' } : null,
    dialog: action === 'dialog' ? { id: 'sample-editor', title: '插件编辑弹窗', description: '宿主生成的中档弹窗。', components: [{ id: 'dialog-text', type: 'text', text: '这段内容用于展示弹窗内部布局。' }, { id: 'dialog-save', type: 'button', label: '完成', action: 'save' }] } : null,
    components: [
      { id: 'intro', type: 'section', title: '宿主组件', description: '同一套配色、圆角、字体与动效参数。', children: [{ id: 'row', type: 'row', children: [{ id: 'badge', type: 'badge', text: '状态标记' }, { id: 'text', type: 'text', text: '正文、提示与长内容。' }] }, { id: 'column', type: 'column', children: [{ id: 'code', type: 'code', text: '{ "preview": true }' }] }] },
      { id: 'fields', type: 'section', title: '字段与动作', children: [{ id: 'name', type: 'field', name: 'name', label: '名称', value: '设计预览', control: 'text', help: '字段说明与错误消息。' }, { id: 'body', type: 'field', name: 'body', label: '说明', value: '多行文字', control: 'textarea' }, { id: 'number', type: 'field', name: 'number', label: '数量', value: 3, control: 'number' }, { id: 'choice', type: 'field', name: 'choice', label: '选择', value: 'a', control: 'select', options: [{ label: '选项 A', value: 'a' }, { label: '选项 B', value: 'b' }] }, { id: 'bool', type: 'field', name: 'bool', label: '启用', value: true, control: 'switch' }, { id: 'actions', type: 'row', children: [{ id: 'save', type: 'button', label: '保存', action: 'save' }, { id: 'validate', type: 'button', label: '字段错误', action: 'validate' }, { id: 'dialog', type: 'button', label: '打开弹窗', action: 'dialog' }] }] },
      { id: 'list', type: 'list', items: [{ id: 'one', title: '列表项', text: '列表正文', meta: '辅助信息', actions: [{ id: 'list-action', type: 'button', label: '查看', action: 'dialog' }] }] },
      { id: 'table', type: 'table', columns: [{ key: 'name', label: '名称' }, { key: 'state', label: '状态' }], rows: [{ id: 'r1', cells: { name: '设计组件', state: '就绪' }, actions: [{ id: 'table-action', type: 'button', label: '详情', action: 'dialog' }] }] },
      { id: 'pages', type: 'pagination', page: action === 'page' ? 2 : 1, total_pages: 3, action: 'page' },
    ],
  });
  N.previewFetch = async (path, options = {}) => {
    await delay(); const body = options.body ? JSON.parse(options.body) : {};
    let value;
    if (path.includes('/readme')) value = { content: '# 设计示例插件\n\n用于展示介绍、代码和引用。\n\n> 不加载第三方插件。\n\n```js\nconst preview = true;\n```' };
    else if (path.includes('/runtime')) value = body.method === 'dialogues.history' ? { messages: s.baseItems } : { capabilities: { pages: 1, jobs: 1, dialogues: 1 }, requires_host: { pages: 1 }, services: [{ name: 'preview', version: 1 }], jobs: [{ id: 'job-1', name: '示例作业', status: body.method === 'jobs.cancel' ? 'cancelled' : 'running', progress: { completed: 2, total: 5 } }], dialogues: [{ id: 'dialogue-1', title: '示例独立对话', status: 'idle' }], operations: [{ name: '本地演示', status: 'complete' }] };
    else if (path.includes('/pages/')) value = P.hostView(body.action);
    else throw new Error('未定义的设计演示请求：' + path);
    return { ok: true, json: async () => value, text: async () => JSON.stringify(value) };
  };
})(window.Preview, window.N);
