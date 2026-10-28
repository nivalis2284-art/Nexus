'use strict';

// Settings owns only its namespace; conversation data and the chat picker stay in preview.js.
(function () {
  const P = window.P;
  if (!P) return;
  P.views = P.views || {};

  const WORKSPACE = 'H:/文档/LingNexus/Nexus';
  const FAMILIES = [
    ['anthropic_messages', 'Anthropic Messages'],
    ['openai_chat_completions', 'OpenAI Chat Completions'],
    ['openai_responses', 'OpenAI Responses'],
  ];
  const DEFAULT_RULES = [
    '当前任务以明确需求为准，不提前扩展功能。',
    '先完成最小可用闭环，必要时说明取舍。',
  ];

  function seedSettings() {
    return {
      providers: [
        {
          id: 'prov_deepseek',
          name: 'DeepSeek',
          family: 'anthropic_messages',
          base_url: 'https://api.example.com/anthropic',
          key: 'sk-demo-deepseek',
          models: [
            { id: 'deepseek-v4.1-flash', context_window_tokens: 65536 },
            { id: 'deepseek-v4.1', context_window_tokens: 131072 },
          ],
        },
        {
          id: 'prov_local',
          name: '本地实验网关',
          family: 'openai_chat_completions',
          base_url: 'http://127.0.0.1:8080/v1',
          key: 'sk-demo-local',
          models: [{ id: 'qwen3-32b', context_window_tokens: 32768 }],
        },
      ],
      current: { provider: 'prov_deepseek', model: 'deepseek-v4.1-flash' },
      effort: 'high',
      permission: 'ask',
      workspace: WORKSPACE,
      mcp: [
        {
          name: 'filesystem', transport: 'stdio', command: 'npx',
          args: ['-y', '@modelcontextprotocol/server-filesystem', WORKSPACE],
          env: {}, enabled: true, url: '', token: '', connected: true,
          tool_count: 11, error: '',
        },
        {
          name: 'linear-docs', transport: 'http', command: '', args: [], env: {},
          enabled: true, url: 'https://mcp.example.com/mcp', token: 'demo-mcp-token',
          connected: false, tool_count: 6, error: '连接超时：演示服务未响应',
        },
        {
          name: 'plugin_demo_filesystem', transport: 'stdio', command: 'npx',
          args: ['-y', '@modelcontextprotocol/server-filesystem', WORKSPACE],
          env: {}, enabled: true, url: '', token: '', connected: true,
          tool_count: 3, error: '', managed: true, plugin_id: 'demo', service: 'filesystem',
        },
      ],
    };
  }

  if (!P.data.settings) P.data.settings = seedSettings();
  const initial = P.data.settings;
  if (!Array.isArray(initial.providers)) initial.providers = [];
  if (!Array.isArray(initial.mcp)) initial.mcp = [];
  if (!initial.current || typeof initial.current !== 'object') {
    initial.current = { provider: '', model: '' };
  }
  if (!P.data.context) P.data.context = {};
  if (!Array.isArray(P.data.context.rules)) P.data.context.rules = DEFAULT_RULES.slice();

  const text = value => P.esc(value === undefined || value === null ? '' : String(value));

  function familyLabel(value) {
    const found = FAMILIES.find(item => item[0] === value);
    return found ? found[1] : String(value || '未知协议');
  }

  function normalizeTransport(value) {
    return String(value || '').toLowerCase() === 'http' ? 'http' : 'stdio';
  }

  function formatWindow(value) {
    const number = Number(value);
    return number > 0 ? number.toLocaleString('zh-CN') + ' tok' : '默认窗口';
  }

  function shortPath(value) {
    const parts = String(value || '').replace(/\\/g, '/').split('/').filter(Boolean);
    return parts.length > 2 ? '…/' + parts.slice(-2).join('/') : String(value || '');
  }

  function currentProvider() {
    const settings = P.data.settings;
    return settings.providers.find(item => item.id === settings.current.provider) ||
      settings.providers[0] || null;
  }

  function currentModel(provider) {
    if (!provider) return null;
    return (provider.models || []).find(item => item.id === P.data.settings.current.model) ||
      (provider.models || [])[0] || null;
  }

  function ensureCurrent() {
    const settings = P.data.settings;
    const provider = settings.providers.find(item => item.id === settings.current.provider) ||
      settings.providers[0];
    if (!provider) {
      settings.current = { provider: '', model: '' };
      return null;
    }
    const model = (provider.models || []).find(item => item.id === settings.current.model) ||
      (provider.models || [])[0];
    settings.current = { provider: provider.id, model: model ? model.id : '' };
    return provider;
  }

  function errorText(value) {
    if (!value) return '';
    if (typeof value === 'string') return value;
    try {
      return JSON.stringify(value);
    } catch (_) {
      return String(value);
    }
  }

  function setBarError(message) {
    const target = document.querySelector('[data-settings-error]');
    if (target) target.textContent = message || '';
  }

  function statusFor(server) {
    if (server.enabled === false) return { label: '已停用', tone: 'is-off' };
    if (server.connected) return { label: '已连接', tone: 'is-on' };
    return { label: '未连接', tone: 'is-err' };
  }

  function providerRow(provider) {
    const settings = P.data.settings;
    const activeProvider = provider.id === settings.current.provider;
    const models = Array.isArray(provider.models) ? provider.models : [];
    const modelChips = models.length
      ? models.map(model => {
        const active = activeProvider && model.id === settings.current.model;
        return `<span class="st-model-chip${active ? ' is-current' : ''}">` +
          `<b>${text(model.id)}</b><small>${text(formatWindow(model.context_window_tokens))}</small></span>`;
      }).join('')
      : '<span class="st-hint">还没有模型</span>';
    return `<article class="st-provider${activeProvider ? ' is-current' : ''}" data-provider="${text(provider.id)}">
      <div class="st-provider-head">
        <div class="st-provider-title">
          <span class="st-key-dot${provider.key ? ' is-set' : ''}" aria-hidden="true"></span>
          <div>
            <h4>${text(provider.name)}${activeProvider ? '<span class="st-badge is-current">当前</span>' : ''}</h4>
            <p class="st-provider-url" title="${text(provider.base_url)}">${text(provider.base_url)}</p>
          </div>
        </div>
        <div class="st-actions">
          <button class="st-mini" type="button" data-do="settings-edit-provider" data-id="${text(provider.id)}">编辑</button>
          <button class="st-mini is-danger" type="button" data-do="settings-delete-provider" data-id="${text(provider.id)}">删除</button>
        </div>
      </div>
      <div class="st-provider-meta">
        <span class="st-badge">${text(familyLabel(provider.family))}</span>
        <span>${models.length} 个模型</span>
        <span>${provider.key ? '密钥已配置' : '未配置密钥'}</span>
      </div>
      <div class="st-model-chips">${modelChips}</div>
    </article>`;
  }

  function mcpRow(server, index) {
    const status = statusFor(server);
    const managed = server.managed === true;
    const enabled = server.enabled !== false;
    const tools = server.tool_count === null || server.tool_count === undefined
      ? '—'
      : String(server.tool_count);
    const recent = errorText(server.error);
    const actions = managed
      ? `<button class="st-mini" type="button" data-do="settings-reconnect-mcp" data-name="${text(server.name)}">重连</button>`
      : `<button class="st-mini" type="button" data-do="settings-mcp-toggle" data-name="${text(server.name)}">${enabled ? '停用' : '启用'}</button>
        <button class="st-mini" type="button" data-do="settings-edit-mcp" data-name="${text(server.name)}">编辑</button>
        <button class="st-mini" type="button" data-do="settings-reconnect-mcp" data-name="${text(server.name)}">重连</button>
        <button class="st-mini is-danger" type="button" data-do="settings-delete-mcp" data-name="${text(server.name)}">删除</button>`;
    return `<article class="st-mcp${managed ? ' is-managed' : ''}${enabled ? '' : ' is-off'}" data-mcp-row="${index}">
      <span class="st-status-dot ${status.tone}" aria-hidden="true"></span>
      <div class="st-mcp-main">
        <div class="st-mcp-title">
          <b>${text(server.name)}</b>
          ${managed ? `<span class="st-badge is-managed" title="来自插件 ${text(server.plugin_id || '')} · 请在插件管理中修改">插件托管</span>` : ''}
        </div>
        <div class="st-mcp-meta">
          <span>${normalizeTransport(server.transport) === 'http' ? 'HTTP' : 'stdio'}</span>
          <span>${status.label}</span>
          <span>工具 ${tools}</span>
        </div>
        <p class="st-mcp-error${recent ? ' has-error' : ''}">最近错误：${text(recent || '无')}</p>
      </div>
      <div class="st-mcp-actions">${actions}</div>
    </article>`;
  }

  function renderSettings() {
    const settings = P.data.settings;
    const provider = currentProvider();
    const model = currentModel(provider);
    const rules = P.data.context.rules || [];
    const mcp = settings.mcp || [];
    const enabledMcp = mcp.filter(item => item.enabled !== false);
    const connectedMcp = enabledMcp.filter(item => item.connected).length;
    const ask = settings.permission !== 'yolo';
    const workspace = settings.workspace || '';
    const body = `<div class="settings-page">
      <section class="card st-overview" aria-label="当前设置">
        <div class="st-overview-main">
          <span class="st-kicker">当前模型</span>
          <h3>${text(provider ? provider.name : '未配置提供商')}<span class="st-sep">·</span><span class="mono">${text(model ? model.id : '选择模型')}</span></h3>
          <p>${provider ? `${text(familyLabel(provider.family))} · ${(provider.models || []).length} 个模型 · ${text(formatWindow(model && model.context_window_tokens))}` : '添加提供商后，主交流才能选择模型。'}</p>
        </div>
        <div class="st-overview-grid">
          <div><span>权限</span><b data-perm-label>${ask ? '写前询问' : '全部放行'}</b></div>
          <div><span>MCP</span><b>${connectedMcp} / ${enabledMcp.length} 已连接</b></div>
          <div class="st-overview-path"><span>工作目录</span><b data-workspace-label title="${text(workspace)}">${text(shortPath(workspace))}</b></div>
        </div>
      </section>

      <section class="card set-sec st-sec" aria-labelledby="st-providers-title">
        <div class="st-sec-head">
          <div><h3 id="st-providers-title">模型提供商</h3><p>一个提供商对应一个接口协议与密钥；每个模型可以单独设置上下文窗口。</p></div>
          <button class="btn st-primary sm" type="button" data-do="settings-add-provider">${P.icon('plus')}添加提供商</button>
        </div>
        <div class="st-providers">
          ${settings.providers.length ? settings.providers.map(providerRow).join('') : '<div class="empty-state">还没有提供商。添加后即可在交流中选择模型。</div>'}
        </div>
      </section>

      <section class="card set-sec st-sec" aria-labelledby="st-rules-title">
        <div class="st-sec-head">
          <div><h3 id="st-rules-title">基本原则</h3><p>与上下文管理共享同一份所有者原则，保存后随每次请求交给灵。</p></div>
          <span class="st-badge is-shared">与上下文共享</span>
        </div>
        <label class="field st-field-wide">原则与交流偏好
          <textarea class="st-rules" data-settings-rules rows="6" placeholder="每行一条，例如：先说明关键取舍，再执行。">${text(rules.join('\n'))}</textarea>
        </label>
        <p class="st-hint">每行一条；留空表示不附加所有者原则。修改在底部统一保存。</p>
      </section>

      <section class="card set-sec st-sec" aria-labelledby="st-runtime-title">
        <div class="st-sec-head">
          <div><h3 id="st-runtime-title">权限与工作目录</h3><p>控制写文件、运行命令前的确认方式，以及工作的默认根目录。</p></div>
        </div>
        <div class="st-perm" role="radiogroup" aria-label="操作权限" data-perm-group>
          <button class="st-perm-btn${ask ? ' is-active' : ''}" type="button" role="radio" aria-checked="${ask}" data-do="settings-permission" data-value="ask">
            <b>写前询问</b><small>写文件或运行命令前询问</small>
          </button>
          <button class="st-perm-btn${ask ? '' : ' is-active'}" type="button" role="radio" aria-checked="${!ask}" data-do="settings-permission" data-value="yolo">
            <b>全部放行</b><small>不再逐项确认</small>
          </button>
        </div>
        <div class="st-workspace">
          <label class="field">工作目录
            <input data-settings-workspace type="text" value="${text(workspace)}" placeholder="H:/path/to/project" autocomplete="off" spellcheck="false">
          </label>
        </div>
        <p class="st-hint">写前询问会在每一次写文件或运行命令前停下；全部放行适合只读演示目录。交流进行中不能切换工作目录。</p>
      </section>

      <section class="card set-sec st-sec" aria-labelledby="st-mcp-title">
        <div class="st-sec-head">
          <div><h3 id="st-mcp-title">MCP 服务</h3><p>连接本地进程或 HTTP 服务。插件托管的服务在这里只读显示。</p></div>
          <button class="btn st-primary sm" type="button" data-do="settings-add-mcp">${P.icon('plus')}添加服务</button>
        </div>
        <div class="st-mcp-list" aria-live="polite">
          ${mcp.length ? mcp.map(mcpRow).join('') : '<div class="empty-state">尚未添加 MCP 服务。</div>'}
        </div>
        <p class="st-hint st-mcp-note">重连会保留配置并清除最近错误；插件托管的服务请在插件管理中修改。</p>
      </section>
      <div class="st-savebar" data-settings-bar>
        <p class="st-error" data-settings-error aria-live="polite"></p>
        <button class="btn ghost sm" type="button" data-do="settings-close">关闭</button>
        <button class="btn st-primary sm" type="button" data-do="settings-save">保存设置</button>
      </div>
    </div>`;
    return P.page('设置', '让模型、原则、权限与连接各归其位。', body,
      '<span class="pill st-pill">本地演示 · 统一保存</span>');
  }

  function newId(prefix) {
    return prefix + '_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  }

  function refreshChat() {
    if (typeof P.refreshModel === 'function') P.refreshModel();
  }

  function buildProviderDialog(provider) {
    const editing = Boolean(provider);
    const initialModels = editing ? (provider.models || []).map(item => Object.assign({}, item)) : [];
    const keyHint = editing && provider.key ? '已保存，留空则不修改' : '演示密钥，可留空';
    const dialog = P.dialog({
      title: editing ? '编辑提供商' : '添加提供商',
      submit: '保存',
      wide: true,
      body: `<div class="st-form">
        <div class="fields">
          <label class="field">名称
            <input name="name" type="text" autocomplete="off" value="${text(editing ? provider.name : '')}" placeholder="例如：DeepSeek">
          </label>
          <label class="field">接口协议
            <select name="family">
              ${FAMILIES.map(item => `<option value="${item[0]}"${editing && provider.family === item[0] ? ' selected' : ''}>${item[1]}</option>`).join('')}
            </select>
          </label>
          <label class="field st-span">Base URL
            <input name="base_url" type="url" autocomplete="off" value="${text(editing ? provider.base_url : '')}" placeholder="https://api.example.com/v1">
          </label>
          <label class="field st-span">API Key
            <input name="api_key" type="password" autocomplete="off" value="" placeholder="${keyHint}">
          </label>
        </div>
        <div class="st-models">
          <div class="st-models-head"><b>模型</b><span>上下文窗口只影响本地裁剪与压缩预算；留空使用默认值。</span></div>
          <div class="st-model-list" data-model-list></div>
          <div class="st-model-add">
            <input data-new-model type="text" autocomplete="off" placeholder="新增模型名，例如 deepseek-v4.1-flash" aria-label="新增模型名">
            <button class="st-mini" type="button" data-add-model>添加</button>
          </div>
          <p class="st-error" data-models-error aria-live="polite"></p>
        </div>
      </div>`,
      onSubmit(form) {
        const name = String(form.elements.namedItem('name').value || '').trim();
        if (!name) throw new Error('请填写提供商名称。');
        const family = String(form.elements.namedItem('family').value || '');
        if (!FAMILIES.some(item => item[0] === family)) throw new Error('请选择有效的接口协议。');
        const baseUrl = String(form.elements.namedItem('base_url').value || '').trim().replace(/\/+$/, '');
        if (!/^https?:\/\//i.test(baseUrl)) throw new Error('Base URL 需以 http:// 或 https:// 开头。');

        const rows = Array.from(dialog.querySelectorAll('[data-model-row]'));
        const models = [];
        const seen = new Set();
        const renamed = new Map();
        for (const row of rows) {
          const id = String(row.querySelector('[data-model-id]').value || '').trim();
          if (!id) continue;
          if (seen.has(id)) throw new Error('模型名不能重复：' + id);
          seen.add(id);
          const entry = Object.assign({}, row.model || {}, { id });
          const raw = String(row.querySelector('[data-model-window]').value || '').trim();
          if (!raw) delete entry.context_window_tokens;
          else if (!/^\d+$/.test(raw) || Number(raw) <= 0) {
            throw new Error('模型“' + id + '”的上下文窗口需要填写正整数，或留空使用默认值。');
          } else {
            entry.context_window_tokens = Number(raw);
          }
          if (row.model && row.model.id) renamed.set(row.model.id, id);
          models.push(entry);
        }
        if (!models.length) throw new Error('请至少配置一个模型。');

        const settings = P.data.settings;
        const key = String(form.elements.namedItem('api_key').value || '').trim();
        if (editing) {
          const index = settings.providers.indexOf(provider);
          if (index < 0) throw new Error('提供商已不存在，请重新打开设置。');
          const next = Object.assign({}, provider, { name, family, base_url: baseUrl, models });
          if (key) next.key = key;
          if (settings.current.provider === provider.id && renamed.has(settings.current.model)) {
            settings.current.model = renamed.get(settings.current.model);
          }
          settings.providers[index] = next;
        } else {
          settings.providers.push({
            id: newId('prov'), name, family, base_url: baseUrl, key, models,
          });
        }
        ensureCurrent();
        P.save();
        P.render();
        refreshChat();
        P.toast(editing ? '提供商已更新' : '提供商已添加');
      },
    });

    const list = dialog.querySelector('[data-model-list]');
    const error = dialog.querySelector('[data-models-error]');
    const input = dialog.querySelector('[data-new-model]');

    function addModelRow(model) {
      const row = document.createElement('div');
      row.className = 'st-model-row';
      row.dataset.modelRow = '';
      row.model = model;
      const windowValue = Number(model.context_window_tokens) > 0
        ? String(model.context_window_tokens)
        : '';
      row.innerHTML = `<input data-model-id type="text" autocomplete="off" placeholder="模型名" aria-label="模型名" value="${text(model.id)}">
        <label class="st-window"><span>窗口</span>
          <input data-model-window type="number" min="1" step="1" inputmode="numeric" placeholder="默认" aria-label="${text(model.id || '模型')} 的上下文窗口 token" value="${text(windowValue)}">
          <span>tok</span>
        </label>
        <button class="st-icon-btn is-danger" type="button" data-remove-model aria-label="移除模型 ${text(model.id || '')}">${P.icon('trash')}</button>`;
      list.appendChild(row);
    }

    (initialModels.length ? initialModels : [{ id: '' }]).forEach(addModelRow);

    list.addEventListener('click', event => {
      const remove = event.target.closest('[data-remove-model]');
      if (!remove) return;
      const row = remove.closest('[data-model-row]');
      if (row) row.remove();
    });

    function addModel() {
      const id = String(input.value || '').trim();
      if (!id) return;
      const ids = Array.from(list.querySelectorAll('[data-model-id]'))
        .map(item => item.value.trim()).filter(Boolean);
      if (ids.includes(id)) {
        error.textContent = '模型名不能重复。';
        return;
      }
      error.textContent = '';
      addModelRow({ id });
      input.value = '';
      input.focus();
    }

    dialog.querySelector('[data-add-model]').addEventListener('click', addModel);
    input.addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      addModel();
    });
  }

  function parseArgs(raw) {
    const value = String(raw || '').trim();
    if (!value) return [];
    if (!value.startsWith('[')) {
      return value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
    }
    let parsed;
    try {
      parsed = JSON.parse(value);
    } catch (_) {
      throw new Error('参数 JSON 格式不正确。');
    }
    if (!Array.isArray(parsed)) throw new Error('参数 JSON 必须是字符串数组。');
    return parsed.map(item => {
      if (item !== null && typeof item === 'object') throw new Error('参数 JSON 必须是字符串数组。');
      return String(item);
    });
  }

  function parseEnv(raw) {
    const value = String(raw || '').trim();
    if (!value) return {};
    let parsed;
    try {
      parsed = JSON.parse(value);
    } catch (_) {
      throw new Error('环境变量 JSON 格式不正确。');
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('环境变量必须是 JSON 对象。');
    }
    const result = {};
    for (const [key, item] of Object.entries(parsed)) {
      if (item !== null && typeof item === 'object') throw new Error('环境变量的值必须是字符串。');
      result[key] = String(item);
    }
    return result;
  }

  function buildMcpDialog(server) {
    const editing = Boolean(server);
    const transport = normalizeTransport(server ? server.transport : 'stdio');
    const args = server && Array.isArray(server.args) ? server.args.join('\n') : '';
    const env = server && server.env && typeof server.env === 'object' && Object.keys(server.env).length
      ? JSON.stringify(server.env, null, 2)
      : '';
    const hasToken = Boolean(server && server.token);
    const dialog = P.dialog({
      title: editing ? '编辑 MCP 服务' : '添加 MCP 服务',
      submit: '保存',
      body: `<div class="st-form">
        <div class="fields">
          <label class="field">名称
            <input name="mcp_name" type="text" autocomplete="off" value="${text(editing ? server.name : '')}" placeholder="例如：filesystem">
          </label>
          <label class="field">类型
            <select name="mcp_transport">
              <option value="stdio"${transport === 'stdio' ? ' selected' : ''}>本地进程（stdio）</option>
              <option value="http"${transport === 'http' ? ' selected' : ''}>HTTP</option>
            </select>
          </label>
        </div>
        <div class="st-mcp-fields" data-mcp-fields="stdio"${transport === 'stdio' ? '' : ' hidden'}>
          <label class="field">命令
            <input name="mcp_command" type="text" autocomplete="off" value="${text(editing ? server.command : '')}" placeholder="例如：npx">
          </label>
          <label class="field">参数（每行一个，或 JSON 数组）
            <textarea name="mcp_args" rows="3" placeholder="-y&#10;@modelcontextprotocol/server-filesystem&#10;H:/path/to/work">${text(args)}</textarea>
          </label>
          <label class="field">环境变量（JSON 对象）
            <textarea name="mcp_env" rows="3" placeholder='{"KEY":"value"}'>${text(env)}</textarea>
          </label>
        </div>
        <div class="st-mcp-fields" data-mcp-fields="http"${transport === 'http' ? '' : ' hidden'}>
          <label class="field">URL
            <input name="mcp_url" type="url" autocomplete="off" value="${text(editing ? server.url : '')}" placeholder="https://example.com/mcp">
          </label>
          <label class="field">Bearer token
            <input name="mcp_token" type="password" autocomplete="off" value="" placeholder="${hasToken ? '已保存，留空则保留' : '可选'}">
          </label>
          <label class="st-check"${hasToken ? '' : ' hidden'}>
            <input name="mcp_clear_token" type="checkbox"> 清除已保存的 token
          </label>
        </div>
        <label class="st-check">
          <input name="mcp_enabled" type="checkbox"${!editing || server.enabled !== false ? ' checked' : ''}> 启用此服务
        </label>
        <p class="st-hint">只修改本地演示数据，不会启动进程或连接远程地址。</p>
      </div>`,
      onSubmit(form) {
        const name = String(form.elements.namedItem('mcp_name').value || '').trim();
        if (!name) throw new Error('请填写服务名称。');
        const settings = P.data.settings;
        const clash = settings.mcp.find(item => item !== server && item.name === name);
        if (clash) {
          throw new Error(clash.managed ? '此名称属于插件托管服务，请在插件管理中操作。' : '服务名称已存在。');
        }
        const kind = normalizeTransport(form.elements.namedItem('mcp_transport').value);
        const enabled = form.elements.namedItem('mcp_enabled').checked;
        const entry = Object.assign({}, server || {
          name, transport: kind, command: '', args: [], env: {}, enabled: true,
          url: '', token: '', connected: false, tool_count: 0, error: '',
        }, { name, transport: kind, enabled });

        if (kind === 'stdio') {
          const command = String(form.elements.namedItem('mcp_command').value || '').trim();
          if (!command) throw new Error('请填写启动命令。');
          entry.command = command;
          entry.args = parseArgs(form.elements.namedItem('mcp_args').value);
          entry.env = parseEnv(form.elements.namedItem('mcp_env').value);
          entry.url = '';
        } else {
          const url = String(form.elements.namedItem('mcp_url').value || '').trim();
          let parsed;
          try {
            parsed = new URL(url);
          } catch (_) {
            throw new Error('HTTP URL 格式不正确。');
          }
          if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
            throw new Error('HTTP URL 必须使用 http:// 或 https://。');
          }
          entry.command = '';
          entry.args = [];
          entry.env = {};
          entry.url = url;
          if (form.elements.namedItem('mcp_clear_token').checked) {
            entry.token = '';
          } else {
            const token = String(form.elements.namedItem('mcp_token').value || '').trim();
            if (token) entry.token = token;
          }
        }

        entry.connected = enabled;
        entry.error = '';
        if (editing) Object.assign(server, entry);
        else settings.mcp.push(entry);
        P.save();
        P.render();
        P.toast(editing ? 'MCP 服务已更新' : 'MCP 服务已添加');
      },
    });

    const transportInput = dialog.querySelector('[name="mcp_transport"]');
    const tokenInput = dialog.querySelector('[name="mcp_token"]');
    const clearInput = dialog.querySelector('[name="mcp_clear_token"]');
    const syncFields = () => {
      const kind = normalizeTransport(transportInput.value);
      dialog.querySelector('[data-mcp-fields="stdio"]').hidden = kind !== 'stdio';
      dialog.querySelector('[data-mcp-fields="http"]').hidden = kind !== 'http';
    };
    const syncClear = () => {
      if (clearInput.checked) tokenInput.value = '';
      tokenInput.disabled = clearInput.checked;
    };
    transportInput.addEventListener('change', syncFields);
    clearInput.addEventListener('change', syncClear);
    syncFields();
    syncClear();
  }

  function providerById(id) {
    return P.data.settings.providers.find(item => item.id === id) || null;
  }

  function mcpByName(name) {
    return P.data.settings.mcp.find(item => item.name === name) || null;
  }

  P.views.settings = renderSettings;

  P.action('settings-add-provider', () => buildProviderDialog(null));
  P.action('settings-edit-provider', button => {
    const provider = providerById(button.dataset.id || '');
    if (provider) buildProviderDialog(provider);
  });
  P.action('settings-delete-provider', button => {
    const provider = providerById(button.dataset.id || '');
    if (!provider) return;
    const settings = P.data.settings;
    if (settings.providers.length <= 1) {
      P.toast('至少保留一个提供商');
      return;
    }
    P.confirm('删除提供商', `删除“${provider.name}”及其密钥？`, () => {
      settings.providers = settings.providers.filter(item => item !== provider);
      ensureCurrent();
      P.save();
      P.render();
      refreshChat();
      P.toast('提供商已删除');
    });
  });

  P.action('settings-permission', button => {
    const value = button.dataset.value;
    if (value !== 'ask' && value !== 'yolo') return;
    const group = button.closest('[data-perm-group]');
    if (group) {
      group.querySelectorAll('[data-do="settings-permission"]').forEach(item => {
        const active = item === button;
        item.classList.toggle('is-active', active);
        item.setAttribute('aria-checked', String(active));
      });
    }
    setBarError('');
  });

  P.action('settings-save', () => {
    const workspaceInput = document.querySelector('[data-settings-workspace]');
    const rulesInput = document.querySelector('[data-settings-rules]');
    const chosen = document.querySelector('[data-do="settings-permission"].is-active');
    if (!workspaceInput || !rulesInput) return;
    const workspace = workspaceInput.value.trim();
    if (!workspace) {
      workspaceInput.focus();
      setBarError('请填写工作目录。');
      return;
    }
    const permission = chosen && chosen.dataset.value === 'yolo' ? 'yolo' : 'ask';
    const settings = P.data.settings;
    if (workspace !== settings.workspace && P.running) {
      setBarError('请等当前交流完成后再切换工作目录。');
      return;
    }
    settings.permission = permission;
    settings.workspace = workspace;
    P.data.context.rules = rulesInput.value.split(/\r?\n/)
      .map(item => item.trim()).filter(Boolean);
    P.save();
    P.render();
    P.toast('设置已保存');
  });

  P.action('settings-close', () => P.go('chat'));

  P.action('settings-add-mcp', () => buildMcpDialog(null));
  P.action('settings-edit-mcp', button => {
    const server = mcpByName(button.dataset.name || '');
    if (!server) return;
    if (server.managed) {
      P.toast('插件托管的服务请在插件管理中修改');
      return;
    }
    buildMcpDialog(server);
  });
  P.action('settings-mcp-toggle', button => {
    const server = mcpByName(button.dataset.name || '');
    if (!server || server.managed) return;
    server.enabled = server.enabled === false;
    server.connected = server.enabled;
    server.error = '';
    P.save();
    P.render();
    P.toast(server.enabled ? `已启用 ${server.name}` : `已停用 ${server.name}`);
  });
  P.action('settings-reconnect-mcp', button => {
    const server = mcpByName(button.dataset.name || '');
    if (!server) return;
    if (server.enabled === false) {
      P.toast('服务已停用，请先启用');
      return;
    }
    server.connected = true;
    server.error = '';
    P.save();
    P.render();
    P.toast(`${server.name} 已重连`);
  });
  P.action('settings-delete-mcp', button => {
    const server = mcpByName(button.dataset.name || '');
    if (!server || server.managed) return;
    P.confirm('删除 MCP 服务', `删除“${server.name}”？`, () => {
      P.data.settings.mcp = P.data.settings.mcp.filter(item => item !== server);
      P.save();
      P.render();
      P.toast('MCP 服务已删除');
    });
  });
})();
