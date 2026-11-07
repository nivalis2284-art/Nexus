'use strict';
(function (P, N) {
  const { $, $$, state: s } = N;
  const pages = { chat: '对话', work: '工作', context: '上下文', skills: '技能', plugins: '插件', settings: '设置' };
  const scenes = {
    chat: [['conversation', '已有会话'], ['empty', '空态'], ['stream', '思考→工具→流式回复'], ['attention', '审批与提问'], ['details', '关联工作／附件／压缩'], ['error', '错误与排队'], ['long', '长历史／阅读锚点']],
    work: [['overview', '全部工作状态'], ['empty', '空态'], ['progress', '工作推进与状态变化']],
    context: [['system', '系统指令'], ['history', '摘要与历史'], ['locked', '运行中锁定'], ['empty', '无测量值／空数据']],
    skills: [['overview', '来源与覆盖关系'], ['empty', '空态'], ['error', '读取错误']],
    plugins: [['overview', '插件管理'], ['requests', '请求记录'], ['empty', '空态'], ['loading', '加载中'], ['error', '错误']],
    settings: [['overview', '完整设置'], ['empty', '未配置'], ['error', '连接错误']],
  };
  P.scenes = scenes;
  const tools = document.createElement('details'); tools.className = 'preview-tools'; tools.open = false;
  tools.innerHTML = `<summary>${N.icon('layers', 'i sm')}<b>设计预览工具</b><span>仅本地模拟</span>${N.icon('chev', 'i xs')}</summary><div class="preview-tool-body">
    <div class="preview-tool-row"><label>页面<select id="preview-page">${Object.entries(pages).map(([k,v]) => `<option value="${k}">${v}</option>`).join('')}</select></label><label>场景<select id="preview-scene"></select></label></div>
    <div class="preview-tool-row"><label>主题<select id="preview-theme"><option value="cool-night">星枢 · 黑夜</option><option value="cool-day">星枢 · 白昼</option><option value="jade-night">灵玉 · 黑夜</option><option value="jade-day">灵玉 · 白昼</option></select></label><label>速度<select id="preview-speed">${N.MOTION_LEVELS.map(k => `<option value="${k}" ${k === '1' ? 'selected' : ''}>${N.MOTION_LABELS[k]}</option>`).join('')}</select></label></div>
    <div class="preview-tool-row"><button class="btn ghost sm" id="preview-gallery">组件状态库</button><button class="btn ghost sm" id="preview-replay">重播入场</button><button class="btn ghost sm" id="preview-reset">恢复样本</button><button class="btn ghost sm" id="preview-fail">下次操作失败</button></div>
    <p class="preview-notice">虚构数据 · 刷新复原 · 不连接后端。<a href="README.md" target="_blank">阅读说明</a></p></div>`;
  document.body.append(tools);
  new MutationObserver(() => {
    const root = document.documentElement.dataset;
    $('#preview-theme').value = root.palette + '-' + root.theme;
    $('#preview-speed').value = s.motion;
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'data-palette', 'data-motion'] });
  function options(page, selected) { $('#preview-scene').innerHTML = scenes[page].map(([k,v]) => `<option value="${k}">${v}</option>`).join(''); $('#preview-scene').value = selected || scenes[page][0][0]; }
  function paint() {
    N.composeItems(); N.renderIsland(); N.renderRequests(); N.renderQueue(); N.updateStats(); N.renderModelLabel(); N.renderEffortLabel(); N.syncTargets(true); N.updateSend();
    $$('.view').forEach(v => { v.hidden = v.id !== 'view-' + s.view; });
    $$('.dock-btn').forEach(b => b.classList.toggle('active', b.dataset.view === s.view));
    N.markEnter(s.view); N.renderView(s.view); N.refreshIsland(); N.syncDock(true); N.syncPivot?.();
    requestAnimationFrame(() => { if (s.view === 'chat') { N.scrollEnd(false); N.enterChat(); } else N.stagger($('#view-' + s.view)); });
  }
  P.selectScene = (page, scene) => {
    P.cancel(); N.clearTimers(); N.hostPages?.dispose();
    s.files.forEach(file => { if (file.url?.startsWith('blob:')) URL.revokeObjectURL(file.url); });
    for (const url of P.files.values()) URL.revokeObjectURL(url); P.files.clear();
    if (s.backgroundOnly) N.setBackgroundOnly(false);
    $$('dialog[open]').forEach(d => d.close()); N.closeMenu?.(true);
    if ($('#island').mode !== 'compact') N.morphIsland('compact');
    const preference = { palette: s.palette, theme: s.theme, motion: s.motion };
    P.resetData(); Object.assign(s, preference, { view: page });
    P.scenario = scene; P.failNext = false;
    $('#input').value = ''; $('#input').style.height = ''; s.files = [];
    N.filesRender?.(); $('#usage-panel').hidden = true;
    $$('.view').forEach(v => { v.scrollTop = 0; v._sig = ''; });
    if (scene === 'empty') {
      if (page === 'chat') s.baseItems = [];
      if (page === 'work') s.works = [];
      if (page === 'skills') s.skills = [];
      if (page === 'plugins') s.plugins = [];
      if (page === 'context') { s.context = { system: 0, tools: 0, messages: 0, window: 200000 }; Object.assign(s.contextData, { owner_instructions: [], roles: [], sections: [], summary: null, turns: [] }); }
      if (page === 'settings') { s.providers = []; s.mcp = []; s.current = { provider: '', model: '', effort: '' }; }
    }
    if (scene === 'long') s.baseItems = Array.from({ length: 18 }, (_, i) => [{ key: 'history-u-' + i, index: i * 2, role: 'user', text: '第 ' + (i + 1) + ' 轮：检查长历史阅读位置。', at: '10:20' }, { key: 'history-a-' + i, index: i * 2 + 1, role: 'assistant', text: P.markdown, at: '10:21', elapsed: 8 }]).flat();
    if (scene === 'details') {
      s.baseItems[0] = { ...s.baseItems[0], attachments: [{ name: '设计说明.md', size: 2048, kind: 'file', url: 'README.md' }], images: [N.demoImage()] };
      s.baseItems.push({ key: 'dispatch-sample', role: 'assistant', text: '已派发一项后台工作。', workId: 'work-1', compressed: true, at: '10:22' }, { key: 'result-sample', role: 'assistant', text: '工作结果已整理。', workId: 'work-4', card: 'result', spine: [['整理', '梳理已有规则'], ['核对', '检查设计样张']], at: '10:23', elapsed: 8 });
    }
    if (scene === 'attention') s.requests = [{ id: 'approval-preview', token: 'approval-preview', kind: 'approval', owner: '助手', payload: { name: 'write', arguments: { path: '示例/说明.md' }, reason: '审批动效与按钮状态演示，不会写入文件。' } }, { id: 'question-preview', token: 'question-preview', kind: 'question', owner: '助手', payload: { questions: [{ question: '你更倾向哪种布局？', options: ['均衡', '紧凑'] }] } }];
    if (scene === 'error') {
      if (page === 'chat') { s.baseItems.push({ key: 'failed-tool', role: 'tool', name: 'read', ok: false, error: '演示工具失败', text: '无法读取示例内容。', arguments: { path: '示例/缺失.md' } }, { key: 'error', role: 'notice', text: '演示：回复中断，已显示的内容仍保留。' }); s.queue = [{ id: 101, text: '稍后继续这个话题', status: 'failed', error: '示例失败' }, { id: 102, text: '另一条待处理输入', status: 'held' }]; }
      if (page === 'skills') s.skills.push({ name: 'unreadable-skill', source: 'project', path: 'error', error: '示例读取失败', effective: false, description: '错误卡片不会修改原文件。' });
      if (page === 'plugins') Object.assign(s.plugins[0], { status: 'error', enabled: false, error: '示例加载失败，可重新启用。' });
      if (page === 'settings') Object.assign(s.mcp[0], { connected: false, error: '示例连接超时' });
    }
    if (scene === 'locked') { s.replying = true; s.contextData.running = true; }
    if (scene === 'loading') { Object.assign(s.plugins[0], { status: 'loading', enabled: true, progress: '演示：正在初始化组件…' }); P.later(() => { Object.assign(s.plugins[0], { status: 'ready', progress: '' }); N.renderPlugins(); }, 2000); }
    paint();
    $('#preview-page').value = page; options(page, scene);
    if (page === 'context') { const tab = scene === 'history' || scene === 'locked' ? 'history' : 'system'; $(`[data-act="ctx-tab"][data-k="${tab}"]`)?.click(); }
    if (page === 'plugins') $(`[data-act="plug-tab"][data-k="${scene === 'requests' ? 'requests' : 'plugins'}"]`)?.click();
    if (scene === 'stream') P.later(() => N.api.input('演示一段自然显现的回复', {}), 300);
    if (scene === 'progress') {
      [1000, 2200, 3400].forEach((ms, i) => P.later(() => { const w = s.works[0]; w.work_summary = ['整理完成，开始验证', '检查主题与手机布局', '已完成设计检查'][i]; if (i === 2) { w.state = 'completed'; w.finished = '现在'; w.steps.forEach(step => step.status = 'completed'); } N.renderWork(); N.refreshIsland(); }, ms));
    }
    const url = new URL(location.href); url.searchParams.set('page', page); url.searchParams.set('scene', scene); history.replaceState(null, '', url);
  };
  $('#preview-page').onchange = e => P.selectScene(e.target.value, scenes[e.target.value][0][0]);
  $('#preview-scene').onchange = e => P.selectScene($('#preview-page').value, e.target.value);
  $('#preview-theme').onchange = e => { const [palette, theme] = e.target.value.split('-'); if (s.palette !== palette) N.setPalette(palette, e.target); if (s.theme !== theme) N.setTheme(theme, e.target); };
  $('#preview-speed').onchange = e => N.setMotion(e.target.value);
  $('#preview-replay').onclick = () => s.view === 'chat' ? N.enterChat() : N.stagger($('#view-' + s.view));
  $('#preview-reset').onclick = () => P.selectScene($('#preview-page').value, $('#preview-scene').value);
  $('#preview-fail').onclick = () => { P.failNext = true; N.notify('下一次保存或操作将展示失败，输入会保留。', 'cinnabar'); };
  $('#preview-gallery').onclick = () => P.openGallery();
  document.addEventListener('click', e => { const b = e.target.closest('.dock-btn[data-view]'); if (b) { $('#preview-page').value = b.dataset.view; options(b.dataset.view); } });
  N.refreshScene?.(); N.setMotion('1', true); N.renderProjectButton();
  const query = new URLSearchParams(location.search), page = pages[query.get('page')] ? query.get('page') : 'chat';
  const scene = scenes[page].some(([k]) => k === query.get('scene')) ? query.get('scene') : scenes[page][0][0];
  P.selectScene(page, scene);
  if (query.get('gallery')) P.openGallery(query.get('gallery'));
  if (query.get('tools') === '1') tools.open = true;
  if (query.get('tools') === 'hidden') tools.hidden = true;
  document.fonts?.ready.then(() => { N.syncDock(true); N.syncTargets(true); N.syncPivot?.(); });
})(window.Preview, window.N);
