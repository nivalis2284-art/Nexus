/* ==========================================================================
   设置：模型提供商、基本原则、MCP 服务、权限与工作目录
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, state } = N;
  let editingProvider = null;
  let editingModels = [];
  let editingMcp = null;

  const familyLabel = f => ({ anthropic_messages: 'Anthropic', openai_chat_completions: 'Chat', openai_responses: 'Responses' }[f] || f);
  const FAMILY_FULL = {
    anthropic_messages: 'Anthropic Messages（/v1/messages）',
    openai_chat_completions: 'OpenAI Chat Completions（/v1/chat/completions）',
    openai_responses: 'OpenAI Responses（/v1/responses）',
  };

  function syncSettingsUi(instant = true) {
    const mode = document.documentElement.dataset.theme === 'night' ? 'night' : 'day';
    const view = $('#view-settings');
    if (!view) return;
    $$('.mode-seg button', view).forEach(b => {
      const on = b.dataset.v === mode;
      b.classList.toggle('active', on);
      b.setAttribute('aria-checked', String(on));
    });
    $$('.motion-seg button', view).forEach(b => {
      const on = b.dataset.v === state.motion;
      b.classList.toggle('active', on);
      b.setAttribute('aria-checked', String(on));
    });
    const modeInk = $('.mode-seg .seg-ink', view);
    if (modeInk) N.moveInk(modeInk, $('.mode-seg button.active', view), 'x', instant);
    const motionInk = $('.motion-seg .seg-ink', view);
    if (motionInk) N.moveInk(motionInk, $('.motion-seg button.active', view), 'x', instant);
    $$('.theme-card', view).forEach(card => {
      const t = N.THEMES.find(x => x.id === card.dataset.id);
      const on = card.dataset.id === state.palette;
      card.classList.toggle('is-active', on);
      card.setAttribute('aria-pressed', String(on));
      if (t) {
        const pair = t[mode];
        const [p, a] = $$('.theme-pair i', card);
        if (p) p.style.background = pair.p;
        if (a) a.style.background = pair.a;
      }
    });
    const perm = $('.perm', view);
    if (perm) N.moveInk($('.tab-ink', perm), $('button.active', perm), 'x', true);
  }

  function playMotionDemo() {
    const dots = $$('#motion-demo i');
    if (!dots.length) return;
    dots.forEach((d, i) => {
      d.getAnimations().forEach(a => a.cancel());
      N.anim(d, [{ opacity: .15, transform: 'translateX(-12px) scale(.6)' }, { opacity: 1, transform: 'none' }],
        { duration: 420, delay: i * 90, easing: N.SP.bouncy.e });
    });
  }

  function renderSettings() {
    const view = $('#view-settings');
    const keep = view.scrollTop;
    const mode = document.documentElement.dataset.theme === 'night' ? 'night' : 'day';
    const p = N.providerOf(state.current.provider);
    const m = N.currentModel();
    const budget = m?.context_window_tokens || state.context?.window;
    view.innerHTML = `<div class="page set-page">
      <header class="page-head" data-s>
        <div><h1>设置</h1><p>配置界面、模型、MCP、权限与工作目录。</p></div>
        <span class="pill">仅预览，不连接服务</span>
      </header>

      <section class="card set-sec" data-s>
        <h3>界面</h3><p>动效速度与主题色即时生效；一套暖色、一套冷色，各有浅色与深色。</p>
        <div class="ui-row" data-s>
          <div><b>动效速度</b><small>统一影响页面切换、消息入场、灵岛形变与流式节奏。</small></div>
          <div class="ui-controls">
            <div class="seg ui-seg motion-seg" role="radiogroup" aria-label="动效速度"><span class="seg-ink"></span>
              ${[['fast', '快'], ['normal', '适中'], ['slow', '慢']].map(([k, l]) => `<button role="radio" aria-checked="${state.motion === k}" class="${state.motion === k ? 'active' : ''}" data-act="motion" data-v="${k}">${l}</button>`).join('')}
            </div>
            <div class="motion-demo" id="motion-demo" aria-hidden="true" title="切换速度时演示"><i></i><i></i><i></i></div>
            <button class="btn ghost sm motion-play" type="button" data-act="motion-demo">预览</button>
          </div>
        </div>
        <div class="ui-row" data-s>
          <div><b>明暗模式</b><small>浅色为宣纸底，深色为墨底；切换时从按钮扩散。</small></div>
          <div class="seg ui-seg mode-seg" role="radiogroup" aria-label="明暗模式"><span class="seg-ink"></span>
            ${[['day', '浅色'], ['night', '深色']].map(([k, l]) => `<button role="radio" aria-checked="${mode === k}" class="${mode === k ? 'active' : ''}" data-act="mode" data-v="${k}">${l}</button>`).join('')}
          </div>
        </div>
        <div class="theme-picker" data-s>
          <div class="theme-copy"><b>主题色</b><small>灵青·朱砂是暖色互补，浅蓝·淡紫是冷色邻近；主色用于运行、确认与聚焦，强调色用于待决定与提醒。</small></div>
          <div class="theme-grid">
            ${N.THEMES.map(t => {
              const pair = t[mode];
              const on = state.palette === t.id;
              return `<button class="theme-card${on ? ' is-active' : ''}" data-s data-act="palette" data-id="${t.id}" aria-pressed="${on}" title="${t.desc}">
                <span class="theme-pair"><i style="background:${pair.p}"></i><i style="background:${pair.a}"></i></span>
                <span class="theme-name">${t.name}</span>
                <span class="theme-check">${icon('check', 'i xs')}</span>
              </button>`;
            }).join('')}
          </div>
        </div>
      </section>

      <section class="card set-sec" data-s>
        <h3>模型提供商</h3><p>填入各提供商的 API 密钥即可使用其模型；主交流与后台工作共用当前选择。</p>
        <div class="provider-list">
          ${state.providers.length ? state.providers.map(x => `<div class="provider-row" data-s>
            <span class="pdot${x.has_key ? ' on' : ''}" title="${x.has_key ? '已配置密钥' : '未配置密钥'}"></span>
            <span class="pname">${esc(x.name)}</span>
            <span class="pinfo" title="${esc(x.base_url)}">${familyLabel(x.family)} · ${(x.models || []).length} 个模型</span>
            <button class="pedit" data-act="edit-provider" data-id="${esc(x.id)}">编辑</button>
          </div>`).join('') : '<p class="mcp-empty">还没有配置提供商。</p>'}
        </div>
        <div class="save-bar" style="margin-top:12px"><button class="btn ghost sm" data-act="add-provider">${icon('plus', 'i xs')}添加模型提供商</button></div>
      </section>

      <section class="card set-sec" data-s>
        <h3>基本原则</h3><p>这些原则会随每次请求交给灵；只有保存后才会生效。</p>
        <label class="field"><span class="sr">原则与交流偏好</span><textarea id="instructions" rows="5" placeholder="例如：先说明关键取舍，再执行；遇到不确定的外部影响时先停下确认。">${esc(state.instructions)}</textarea></label>
      </section>

      <section class="card set-sec" data-s>
        <h3>MCP 服务</h3><p>连接本地 stdio 或 HTTP MCP 服务；启停与重连只影响预览状态。</p>
        <div class="mcp-list">
          ${state.mcp.length ? state.mcp.map((s, i) => {
            const connected = s.enabled !== false && s.connected;
            return `<div class="mcp-row" data-s>
              <div class="mcp-main">
                <div class="mcp-title"><span class="mcp-dot${connected ? ' on' : ''}" title="${connected ? '已连接' : '未连接'}"></span><span class="mcp-name">${esc(s.name)}</span>${s.managed ? '<span class="mcp-badge">系统服务</span>' : ''}</div>
                <div class="mcp-meta"><span>${s.transport === 'http' ? 'HTTP' : 'stdio'}</span><span>${s.enabled === false ? '已停用' : connected ? '已连接' : '未连接'}</span><span>工具 ${s.tool_count === null || s.tool_count === undefined ? '—' : s.tool_count}</span></div>
                <div class="mcp-recent${s.error ? ' has-error' : ''}">最近错误：${esc(s.error || '无')}</div>
              </div>
              <div class="mcp-actions">
                ${s.managed ? '' : `<button data-act="mcp-toggle" data-i="${i}">${s.enabled === false ? '启用' : '停用'}</button><button data-act="mcp-edit" data-i="${i}">编辑</button>`}
                <button data-act="mcp-reconnect" data-i="${i}">重连</button>
                ${s.managed ? '' : `<button class="danger" data-act="mcp-del" data-i="${i}">删除</button>`}
              </div>
            </div>`;
          }).join('') : '<div class="mcp-empty">尚未添加 MCP 服务。</div>'}
        </div>
        <div class="save-bar" style="margin-top:12px"><button class="btn ghost sm" data-act="add-mcp">${icon('plus', 'i xs')}添加 MCP 服务</button></div>
      </section>

      <section class="card set-sec" data-s>
        <h3>其他</h3><p>权限与工作目录对所有工作生效。</p>
        <div class="perm" role="radiogroup"><span class="tab-ink" aria-hidden="true"></span>
          ${[['ask', '写前询问', '写文件和运行命令前询问'], ['yolo', '全部放行', '不再逐项确认']].map(([v, b, s]) => `<button role="radio" aria-checked="${state.permission === v}" class="${state.permission === v ? 'active' : ''}" data-act="perm" data-v="${v}"><b>${b}</b><small>${s}</small></button>`).join('')}
        </div>
        <div class="set-row" style="margin-top:8px"><div><b>工作目录</b><small>后台工作的默认根目录。</small></div><label class="field" style="min-width:260px"><span class="sr">工作目录</span><input id="workspace-input" type="text" value="${esc(state.workspace)}" placeholder="C:\\path\\to\\project"></label></div>
        <p class="ctx-window-info" id="ctx-window-info">${budget ? `当前模型上下文预算：${N.fmtNum(budget)} tok（可在编辑提供商时为每个模型单独设置）` : ''}</p>
        <p class="sheet-error" id="settings-error" role="alert"></p>
      </section>

      <div class="save-bar" data-s><button class="btn ghost" data-act="reset-settings">恢复演示设置</button><button class="btn solid" data-act="save-settings">保存设置</button></div>
      <p class="foot-note" data-s>当前模型：${esc(p?.name || '—')} · ${esc(m?.id || '—')}。切换模型请使用主交流输入舱的模型按钮。</p>
    </div>`;
    requestAnimationFrame(() => N.syncSettingsUi?.(true));
    view.scrollTop = keep;
    if (N.takeEnter?.('settings')) N.stagger?.(view);
  }

  /* ---------------------------------------------------------------- 提供商 */
  function renderProviderModels() {
    const box = $('#p-models-list');
    if (!editingModels.length) {
      box.innerHTML = '<p class="model-empty">还没有模型。至少添加一个，或先填写模型名后保存。</p>';
      return;
    }
    box.innerHTML = editingModels.map((m, i) => `<div class="model-row">
      <input type="text" value="${esc(m.id || '')}" placeholder="模型名" aria-label="模型名" data-model-name="${i}">
      <label class="win"><input type="number" min="1" step="1" value="${m.context_window_tokens ? esc(m.context_window_tokens) : ''}" placeholder="默认" aria-label="${esc(m.id || '模型')} 的上下文窗口 token" data-model-window="${i}"><span>tok</span></label>
      <button class="rm" type="button" data-act="p-rm-model" data-i="${i}" aria-label="移除模型 ${esc(m.id || '')}">${icon('close', 'i xs')}</button>
    </div>`).join('');
  }
  function openProvider(provider) {
    editingProvider = provider || null;
    editingModels = provider ? N.clone(provider.models || []) : [];
    $('#provider-title').textContent = provider ? '编辑提供商' : '添加提供商';
    $('#p-name').value = provider?.name || '';
    $('#p-family').value = provider?.family || 'anthropic_messages';
    $('#p-base').value = provider?.base_url || '';
    $('#p-model-new').value = '';
    $('#p-models-error').textContent = '';
    $('#p-key').value = '';
    $('#p-key').placeholder = provider?.has_key ? '已配置，留空则不修改' : 'sk-...';
    $('#provider-error').textContent = '';
    $('#p-delete').hidden = !provider;
    renderProviderModels();
    const dlg = $('#provider-dialog');
    dlg.showModal();
    setTimeout(() => $('#p-name').focus(), 40);
    N.anim(dlg, [{ opacity: 0, transform: 'translateY(22px) scale(.94)' }, { opacity: 1, transform: 'none' }],
      { duration: N.SP.pop.d, easing: N.SP.pop.e });
  }
  function addModel() {
    const id = $('#p-model-new').value.trim();
    if (!id) return;
    if (editingModels.some(m => m.id === id)) { $('#p-models-error').textContent = '模型名不能重复。'; return; }
    $('#p-models-error').textContent = '';
    editingModels.push({ id });
    $('#p-model-new').value = '';
    renderProviderModels();
    $('#p-model-new').focus();
  }
  function saveProvider() {
    const err = $('#provider-error');
    err.textContent = '';
    const name = $('#p-name').value.trim();
    const base = $('#p-base').value.trim();
    if (!name) { err.textContent = '请填写提供商名称。'; return; }
    if (base && !/^https?:\/\//i.test(base)) { err.textContent = 'Base URL 需要以 http:// 或 https:// 开头。'; return; }
    const models = [];
    const seen = new Set();
    for (const m of editingModels) {
      const id = String(m.id || '').trim();
      if (!id) continue;
      if (seen.has(id)) { err.textContent = '模型名不能重复：' + id; return; }
      seen.add(id);
      const raw = String(m.context_window_tokens ?? '').trim();
      const entry = { id };
      if (raw) {
        if (!/^\d+$/.test(raw) || Number(raw) <= 0) { err.textContent = `模型“${id}”的上下文窗口需要填写正整数，或留空使用默认值。`; return; }
        entry.context_window_tokens = Number(raw);
      }
      models.push(entry);
    }
    if (!models.length) { err.textContent = '请至少配置一个模型。'; return; }
    const key = $('#p-key').value.trim();
    if (editingProvider) {
      Object.assign(editingProvider, { name, family: $('#p-family').value, base_url: base, models });
      if (key) editingProvider.has_key = true;
      if (!state.providers.some(p => p.id === editingProvider.id)) state.providers[0] = editingProvider;
    } else {
      const p = { id: 'p-' + Date.now(), name, family: $('#p-family').value, base_url: base, models, has_key: !!key };
      state.providers.push(p);
    }
    const active = state.providers.find(p => p.id === state.current.provider);
    if (!active || !(active.models || []).some(m => m.id === state.current.model)) {
      state.current.provider = (active || state.providers[0]).id;
      state.current.model = (active || state.providers[0]).models[0].id;
      N.renderModelLabel();
    }
    $('#provider-dialog').close();
    renderSettings(); N.notify('提供商已保存', 'jade'); N.save();
  }
  async function deleteProvider() {
    if (!editingProvider) return;
    const ok = await N.confirmEdit({
      title: `删除提供商「${editingProvider.name}」？`,
      description: '会同时移除它的密钥与模型列表；正在使用它的交流需要重新选择模型。',
      submitLabel: '删除提供商',
    });
    if (!ok) return;
    state.providers = state.providers.filter(p => p !== editingProvider);
    if (!state.providers.length) {
      state.providers.push({ id: 'empty', name: '未配置', family: 'openai_chat_completions', base_url: '', has_key: false, models: [{ id: '未选择模型' }] });
    }
    if (!state.providers.some(p => p.id === state.current.provider)) {
      state.current.provider = state.providers[0].id;
      state.current.model = state.providers[0].models[0].id;
    }
    N.renderModelLabel();
    $('#provider-dialog').close();
    renderSettings(); N.notify('提供商已删除', 'jade'); N.save();
  }

  /* ---------------------------------------------------------------- MCP */
  function syncTransport() {
    const http = $('#mcp-transport').value === 'http';
    $('#mcp-stdio-fields').hidden = http;
    $('#mcp-http-fields').hidden = !http;
  }
  function syncTokenClear() {
    const clear = $('#mcp-clear-token').checked;
    $('#mcp-token').disabled = clear;
    if (clear) $('#mcp-token').value = '';
  }
  function openMcp(server) {
    editingMcp = server || null;
    $('#mcp-dialog-title').textContent = server ? '编辑 MCP 服务' : '添加 MCP 服务';
    $('#mcp-name').value = server?.name || '';
    $('#mcp-transport').value = server?.transport === 'http' ? 'http' : 'stdio';
    $('#mcp-command').value = server?.command || '';
    $('#mcp-args').value = Array.isArray(server?.args) ? server.args.join('\n') : '';
    $('#mcp-env').value = server?.env && Object.keys(server.env).length ? JSON.stringify(server.env, null, 2) : '';
    $('#mcp-url').value = server?.url || '';
    $('#mcp-token').value = '';
    $('#mcp-token').disabled = false;
    $('#mcp-token').placeholder = server?.has_token ? '已保存，留空则保留' : '可选';
    $('#mcp-clear-token').checked = false;
    $('#mcp-clear-token-wrap').hidden = !server?.has_token;
    $('#mcp-enabled').checked = server ? server.enabled !== false : true;
    $('#mcp-delete').hidden = !server;
    $('#mcp-error').textContent = '';
    syncTransport(); syncTokenClear();
    const dlg = $('#mcp-dialog');
    dlg.showModal();
    N.anim(dlg, [{ opacity: 0, transform: 'translateY(22px) scale(.94)' }, { opacity: 1, transform: 'none' }],
      { duration: N.SP.pop.d, easing: N.SP.pop.e });
  }
  function saveMcp() {
    const err = $('#mcp-error');
    err.textContent = '';
    const name = $('#mcp-name').value.trim();
    if (!name) { err.textContent = '请填写服务名称'; return; }
    const transport = $('#mcp-transport').value;
    let payload;
    if (transport === 'stdio') {
      const command = $('#mcp-command').value.trim();
      if (!command) { err.textContent = '请填写启动命令'; return; }
      let args = [];
      const rawArgs = $('#mcp-args').value.trim();
      if (rawArgs) {
        if (rawArgs.startsWith('[')) {
          try { args = JSON.parse(rawArgs); } catch { err.textContent = '参数 JSON 格式不正确'; return; }
          if (!Array.isArray(args)) { err.textContent = '参数 JSON 必须是字符串数组'; return; }
          args = args.map(String);
        } else args = rawArgs.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      }
      let env = {};
      const rawEnv = $('#mcp-env').value.trim();
      if (rawEnv) {
        try { env = JSON.parse(rawEnv); } catch { err.textContent = '环境变量 JSON 格式不正确'; return; }
        if (!env || typeof env !== 'object' || Array.isArray(env)) { err.textContent = '环境变量必须是 JSON 对象'; return; }
      }
      payload = { name, transport, command, args, env, enabled: $('#mcp-enabled').checked, url: '' };
    } else {
      const url = $('#mcp-url').value.trim();
      if (!url) { err.textContent = '请填写 HTTP URL'; return; }
      if (!/^https?:\/\//i.test(url)) { err.textContent = 'HTTP URL 必须使用 http:// 或 https://'; return; }
      payload = { name, transport, url, enabled: $('#mcp-enabled').checked, command: '', args: [], env: {} };
      if ($('#mcp-clear-token').checked) payload.token = '';
      else if ($('#mcp-token').value.trim()) payload.token = $('#mcp-token').value.trim();
    }
    if (editingMcp) Object.assign(editingMcp, payload, { has_token: payload.token === '' ? false : (payload.token ? true : editingMcp.has_token) });
    else state.mcp.push({ ...payload, connected: false, tool_count: null, error: '', has_token: !!payload.token });
    $('#mcp-dialog').close();
    renderSettings(); N.notify('MCP 服务已保存', 'jade'); N.save();
  }
  async function deleteMcp(i) {
    const s = state.mcp[i];
    if (!s) return;
    const ok = await N.confirmEdit({ title: `删除 MCP 服务「${s.name}」？`, description: '删除后需要重新添加并连接。', submitLabel: '删除服务' });
    if (!ok) return;
    state.mcp.splice(i, 1);
    renderSettings(); N.notify('MCP 服务已删除', 'jade'); N.save();
  }
  function reconnect(i) {
    const s = state.mcp[i];
    if (!s) return;
    s.error = '';
    renderSettings();
    N.notify('正在重连…');
    setTimeout(() => {
      s.connected = true;
      s.error = '';
      s.tool_count = s.tool_count ?? 9;
      renderSettings(); N.notify(`「${s.name}」已连接`, 'jade'); N.save();
    }, 900);
  }
  function saveSettings() {
    const err = $('#settings-error');
    err.textContent = '';
    const ws = $('#workspace-input').value.trim();
    state.instructions = $('#instructions').value;
    state.workspace = ws;
    const env = state.contextData.sections.find(s => s.id === 'env');
    if (env) env.text = `Platform: win32\nDate: 2026-10-28\nWorkspace: ${ws || '未设置'}\nShell: Git Bash`;
    renderSettings(); N.notify('设置已保存', 'jade'); N.save();
  }
  function resetSettings() {
    const fresh = N.seed();
    state.providers = fresh.providers;
    state.current = fresh.current;
    state.instructions = fresh.instructions;
    state.permission = fresh.permission;
    state.workspace = fresh.workspace;
    state.mcp = fresh.mcp;
    N.renderModelLabel(); N.renderEffortLabel();
    renderSettings(); N.notify('已恢复演示设置', 'jade'); N.save();
  }

  /* ---------------------------------------------------------------- 事件 */
  document.addEventListener('click', e => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const act = t.dataset.act;
    if (act === 'motion') return N.setMotion(t.dataset.v);
    if (act === 'motion-demo') return N.playMotionDemo();
    if (act === 'mode') return N.setTheme(t.dataset.v, t);
    if (act === 'palette') return N.setPalette(t.dataset.id, t);
    if (act === 'add-provider') return openProvider(null);
    if (act === 'edit-provider') return openProvider(state.providers.find(p => p.id === t.dataset.id));
    if (act === 'p-rm-model') {
      editingModels.splice(+t.dataset.i, 1);
      return renderProviderModels();
    }
    if (act === 'add-mcp') return openMcp(null);
    if (act === 'mcp-edit') return openMcp(state.mcp[+t.dataset.i]);
    if (act === 'mcp-toggle') {
      const s = state.mcp[+t.dataset.i];
      s.enabled = s.enabled === false;
      if (s.enabled === false) s.connected = false;
      renderSettings(); N.notify(s.enabled === false ? '服务已停用' : '服务已启用', 'jade'); N.save();
      return;
    }
    if (act === 'mcp-del') return deleteMcp(+t.dataset.i);
    if (act === 'mcp-reconnect') return reconnect(+t.dataset.i);
    if (act === 'perm') {
      state.permission = t.dataset.v;
      $$('.perm button').forEach(b => {
        const on = b === t;
        b.classList.toggle('active', on);
        b.setAttribute('aria-checked', String(on));
      });
      N.moveInk($('.perm .tab-ink'), t, 'x');
      N.save();
      return;
    }
    if (act === 'save-settings') return saveSettings();
    if (act === 'reset-settings') return resetSettings();
  });
  document.addEventListener('input', e => {
    if (e.target.id === 'p-model-new' || e.target.dataset.modelName !== undefined) { /* 编辑即生效 */ }
    if (e.target.dataset.modelName !== undefined) {
      const i = +e.target.dataset.modelName;
      if (editingModels[i]) editingModels[i].id = e.target.value;
    }
    if (e.target.dataset.modelWindow !== undefined) {
      const i = +e.target.dataset.modelWindow;
      if (editingModels[i]) {
        const raw = e.target.value.trim();
        if (raw) editingModels[i].context_window_tokens = raw;
        else delete editingModels[i].context_window_tokens;
      }
    }
  });
  $('#p-model-add').addEventListener('click', addModel);
  $('#p-model-new').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addModel(); } });
  $('#p-save').addEventListener('click', saveProvider);
  $('#p-delete').addEventListener('click', deleteProvider);
  $('#mcp-transport').addEventListener('change', syncTransport);
  $('#mcp-clear-token').addEventListener('change', syncTokenClear);
  $('#mcp-save').addEventListener('click', saveMcp);
  $('#mcp-delete').addEventListener('click', async () => {
    if (!editingMcp) return;
    const i = state.mcp.indexOf(editingMcp);
    await deleteMcp(i);
    $('#mcp-dialog').close();
  });

  N.renderSettings = renderSettings;
  N.syncSettingsUi = syncSettingsUi;
  N.playMotionDemo = playMotionDemo;
})(window.N);
