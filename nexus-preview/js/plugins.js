/* ==========================================================================
   插件管理：列表、详情、能力、功能页、设置、请求记录
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, state } = N;
  const pv = { tab: 'plugins', selected: null, req: null, owner: '', kind: '', more: false, dirty: false, busy: false, frame: null, notice: null };
  const KIND_LABEL = { ling: '主交流', task: '工作', subagent: '子代理', chat: '交流' };

  function plugin() { return state.plugins.find(p => p.id === pv.selected) || state.plugins[0]; }
  function statusOf(p) {
    if (p.error) return { label: '错误', tone: 'err' };
    if (p.status === 'draining' || p.status === 'closing') return { label: '收尾中', tone: 'busy' };
    if (p.status === 'loading') return { label: '加载中', tone: 'busy' };
    if (p.enabled) return { label: '已启用', tone: 'ok' };
    return { label: '未启用', tone: 'off' };
  }
  function time(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }

  function renderPlugins() {
    if (!pv.selected || !state.plugins.some(p => p.id === pv.selected)) pv.selected = state.plugins[0]?.id || null;
    const view = $('#view-plugins');
    const keep = view.scrollTop;
    const p = plugin();
    view.innerHTML = `<div class="page plugins-page">
      <header class="page-head" data-s>
        <div><h1>插件管理</h1><p>查看插件、配置与请求记录。导入或替换版本后，新插件默认不启用。</p></div>
        <span class="pill mono" title="${esc(p?.root || '')}">${esc(N.shortPath(p?.root || '插件目录'))}</span>
      </header>
      <div class="tabs" data-s role="tablist"><span class="tab-ink" aria-hidden="true"></span>
        ${[['plugins', '插件'], ['requests', '请求记录']].map(([k, l]) => `<button role="tab" aria-selected="${pv.tab === k}" class="${pv.tab === k ? 'active' : ''}" data-act="plug-tab" data-k="${k}">${l}</button>`).join('')}
      </div>
      <div class="plug-pane">${pv.tab === 'plugins' ? pluginsPane(p) : requestsPane()}</div>
    </div>`;
    requestAnimationFrame(() => {
      const tabs = $('.tabs', view);
      if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x', true);
      if (pv.frame) mountFrame();
    });
    view.scrollTop = keep;
    if (N.takeEnter?.('plugins')) N.stagger?.(view);
  }

  /* ---------------------------------------------------------------- 插件栏 */
  function pluginsPane(p) {
    return `<div class="plug-toolbar" data-s>
      <span class="count">已登记插件 · ${state.plugins.length} 个</span>
      <button class="btn ghost sm" data-act="plug-refresh" ${pv.busy ? 'disabled' : ''}>${icon('refresh', 'i xs')}刷新</button>
      <button class="btn ghost sm" data-act="plug-scan" ${pv.busy ? 'disabled' : ''}>${icon('search', 'i xs')}发现插件</button>
      <button class="btn solid sm" data-act="plug-upload" ${pv.busy ? 'disabled' : ''}>${icon('upload', 'i xs')}上传 ZIP</button>
      <input type="file" id="plug-file" accept=".zip,application/zip" hidden>
    </div>
    <p class="plug-note" data-s>停用会让插件停止接收新工作，并等待已有操作收尾；卸载会移除登记与本地文件。</p>
    ${pv.notice ? `<div class="plug-notice" data-s>${icon('alert', 'i xs')}<span>${esc(pv.notice.text)}</span>${pv.notice.action ? `<button class="btn ghost sm" data-act="${pv.notice.action}">${esc(pv.notice.label)}</button>` : ''}</div>` : ''}
    <div class="plug-layout">
      <div class="plug-list" data-s>
        ${state.plugins.length ? state.plugins.map(x => {
          const st = statusOf(x);
          return `<button class="plug-item${x.id === pv.selected ? ' is-selected' : ''}" data-s data-act="plug-select" data-id="${esc(x.id)}">
            <span class="plug-item-top"><span class="plug-item-name">${esc(x.name)}</span><span class="pstate ${st.tone}">${st.label}</span></span>
            <span class="plug-item-sub"><span>v${esc(x.version || '?')}</span><span>${esc(x.id)}</span></span>
            ${x.active_calls ? `<span class="plug-item-calls">在途 ${x.active_calls}</span>` : ''}
          </button>`;
        }).join('') : '<p class="plug-empty-line">暂无插件。</p>'}
      </div>
      ${detailPane(p)}
    </div>`;
  }

  function detailPane(p) {
    if (!p) return `<div class="card plug-detail" data-s><p class="plug-empty-line">选择一个插件查看详情。</p></div>`;
    if (pv.frame && pv.frame.pluginId === p.id) {
      return `<div class="card plug-detail" data-s>
        <div class="plug-frame">
          <div class="plug-frame-head"><b>${esc(pv.frame.title)}</b><span>${esc(pv.frame.path)}</span><button data-act="plug-close-frame">关闭页面</button></div>
          <iframe id="plugin-frame" title="插件功能页" loading="lazy"></iframe>
        </div>
      </div>`;
    }
    const st = statusOf(p);
    const draining = p.status === 'draining' || p.status === 'closing';
    const loading = p.status === 'loading';
    const caps = p.capabilities || [];
    const pages = p.pages || [];
    const props = Object.keys(p.settings_schema?.properties || {});
    return `<div class="card plug-detail" data-s>
      <div class="plug-detail-head" data-s>
        <div style="flex:1;min-width:0">
          <div class="plug-title-row"><h2>${esc(p.name)}</h2><span class="pstate ${st.tone}">${st.label}</span></div>
          <div class="plug-version">v${esc(p.version || '未声明版本')} · ${esc(p.id)}</div>
          <p class="plug-desc">${esc(p.description || '此插件没有填写简介。')}</p>
        </div>
      </div>
      ${p.progress ? `<div class="plug-progress" data-s>${esc(p.progress)}</div>` : ''}
      ${p.error ? `<div class="plug-error" data-s>插件错误：${esc(p.error)}</div>` : ''}
      <div class="plug-meta" data-s>
        <span>${p.enabled ? '已启用' : '未启用'}</span>
        <span>在途调用 ${Number(p.active_calls || 0)}</span>
        <span>状态 ${esc(p.status || '—')}</span>
      </div>
      <div class="plug-actions" data-s>
        ${draining ? '<span class="note">插件正在收尾；页面会自动刷新状态，此时不能重复下发操作。</span>' : loading
          ? `<button class="btn ghost sm" data-act="plug-op" data-op="unload" data-id="${esc(p.id)}">停用</button>`
          : p.enabled
            ? `<button class="btn ghost sm" data-act="plug-op" data-op="unload" data-id="${esc(p.id)}">停用</button>
               <button class="btn ghost sm" data-act="plug-op" data-op="restart" data-id="${esc(p.id)}">重载</button>
               <button class="btn ghost sm" data-act="plug-op" data-op="stop" data-id="${esc(p.id)}">停止在途</button>`
            : `<button class="btn solid sm" data-act="plug-op" data-op="load" data-id="${esc(p.id)}">加载</button>`}
        <button class="btn ghost sm danger" data-act="plug-op" data-op="uninstall" data-id="${esc(p.id)}" ${draining || loading ? 'disabled' : ''}>卸载</button>
        ${pv.busy ? '<span class="note">正在处理…</span>' : ''}
      </div>

      <div class="plug-sec" data-s><h3>能力</h3>
        <div class="plug-tags">${caps.length ? caps.map(c => {
          const label = typeof c === 'object' ? (c.title || c.name || c.id || JSON.stringify(c)) : String(c);
          return `<span title="${esc(typeof c === 'object' ? c.description || '' : '')}">${esc(label)}</span>`;
        }).join('') : '<span class="plug-empty-line">未声明能力</span>'}</div>
      </div>

      <div class="plug-sec" data-s><h3>功能页</h3>
        <div class="plug-pages">${pages.length ? pages.map(pg => `<div class="plug-page"><b>${esc(pg.title || pg.id || '功能页')}</b><span>${esc(pg.path)}</span><button data-act="plug-page" data-path="${esc(pg.path)}" data-title="${esc(pg.title || pg.id || '功能页')}">打开页面</button></div>`).join('') : '<p class="plug-empty-line">此插件没有提供功能页。</p>'}</div>
      </div>

      <div class="plug-sec" data-s><h3>设置</h3>
        ${props.length ? `<div class="plug-settings" id="plug-settings">
          ${props.map(key => fieldHTML(p, key, p.settings_schema.properties[key])).join('')}
          <div class="plug-settings-foot"><span class="status" id="plug-settings-status" hidden></span><button class="btn solid sm" data-act="plug-save" data-id="${esc(p.id)}">保存设置</button></div>
        </div>` : '<p class="plug-empty-line">此插件没有声明可生成的设置项。</p>'}
      </div>
    </div>`;
  }

  function fieldHTML(p, key, schema) {
    const value = p.settings && Object.prototype.hasOwnProperty.call(p.settings, key) ? p.settings[key] : schema.default;
    const type = String(schema.type || '').toLowerCase();
    const title = schema.title || key;
    let control;
    if (type === 'boolean') {
      control = `<span class="check-line"><input type="checkbox" data-field="${esc(key)}" ${value === true ? 'checked' : ''}><span>启用</span></span>`;
    } else if (Array.isArray(schema.enum) && schema.enum.length) {
      control = `<select data-field="${esc(key)}">${schema.enum.map(v => `<option ${String(v) === String(value) ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`;
    } else if (type === 'number' || type === 'integer') {
      control = `<input type="number" data-field="${esc(key)}" value="${esc(value ?? '')}" step="${type === 'integer' ? '1' : 'any'}" ${schema.minimum !== undefined ? `min="${schema.minimum}"` : ''} ${schema.maximum !== undefined ? `max="${schema.maximum}"` : ''}>`;
    } else if (type === 'string') {
      control = `<input type="text" data-field="${esc(key)}" value="${esc(value ?? '')}">`;
    } else {
      return `<div class="pfield"><label><span>${esc(title)}</span></label><p class="unsupported">此设置类型暂不支持，当前值会原样保留：${esc(type || '未声明类型')}</p></div>`;
    }
    return `<div class="pfield"><label><span>${esc(title)}</span>${control}</label>${schema.description ? `<p class="help">${esc(schema.description)}</p>` : ''}</div>`;
  }

  function mountFrame() {
    const frame = $('#plugin-frame');
    if (!frame) return;
    const p = plugin();
    frame.srcdoc = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
      :root{color-scheme:light}body{margin:0;padding:22px;font:14px/1.7 "Segoe UI","Microsoft YaHei",sans-serif;background:#f7f6f2;color:#15161a}
      h1{font-size:19px;margin:0 0 4px}p{color:#4b4d55;margin:0 0 16px}
      .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:18px}
      .card{background:#fff;border-radius:14px;padding:14px;box-shadow:0 1px 2px rgba(21,22,26,.05),inset 0 0 0 1px rgba(21,22,26,.07)}
      .card b{display:block;font-size:22px;letter-spacing:-.02em}.card span{font-size:12px;color:#8b8d94}
      .bar{height:8px;border-radius:5px;background:#ebe8e0;overflow:hidden;margin-top:6px}
      .bar i{display:block;height:100%;background:#00b48f;border-radius:5px}
      button{border:0;border-radius:12px;padding:9px 14px;background:#15161a;color:#f3f1eb;font:inherit;font-size:13px;cursor:pointer}
      .tag{display:inline-block;font-size:12px;padding:2px 9px;border-radius:9px;background:rgba(0,180,143,.11);color:#036657;margin-right:6px}
    </style></head><body>
      <h1>${esc(pv.frame.title)}</h1>
      <p>${esc(p.name)} 提供的功能页 · 预览环境，不读取真实数据。</p>
      <div class="grid">
        <div class="card"><span>本周读取</span><b>1,284</b><div class="bar"><i style="width:72%"></i></div></div>
        <div class="card"><span>命中缓存</span><b>86%</b><div class="bar"><i style="width:86%"></i></div></div>
        <div class="card"><span>在途调用</span><b>${Number(p.active_calls || 0)}</b><div class="bar"><i style="width:24%"></i></div></div>
      </div>
      <div class="card">
        <div style="margin-bottom:10px"><span class="tag">已启用</span><span class="tag">${esc(p.id)}</span></div>
        <p style="margin:0 0 12px">这个页面由插件自带，外层只负责提供容器与关闭入口。</p>
        <button onclick="this.textContent='已刷新 · '+new Date().toLocaleTimeString()">刷新数据</button>
      </div>
    </body></html>`;
  }

  /* ---------------------------------------------------------------- 请求记录 */
  function requestsPane() {
    const kinds = [...new Set(state.modelRequests.map(r => r.kind))];
    const list = state.modelRequests.filter(r =>
      (!pv.owner || String(r.owner_id || '').includes(pv.owner)) &&
      (!pv.kind || String(r.kind || '').includes(pv.kind)));
    const shown = pv.more ? list : list.slice(0, 4);
    const sel = state.modelRequests.find(r => String(r.id) === String(pv.req)) || null;
    return `<div class="plug-toolbar" data-s><span class="count">请求记录 · ${list.length} 条</span><span class="plug-note" style="margin:0">仅保留最近 7 天</span></div>
      <form class="plug-filters" data-s id="plug-filters">
        <label>活动标识<input id="f-owner" type="text" autocomplete="off" placeholder="可按 owner_id 筛选" value="${esc(pv.owner)}"></label>
        <label>类型<input id="f-kind" type="text" autocomplete="off" list="kind-options" placeholder="可按 kind 筛选" value="${esc(pv.kind)}"><datalist id="kind-options">${kinds.map(k => `<option value="${esc(k)}">`).join('')}</datalist></label>
        <button class="btn ghost sm" type="submit">查询</button>
      </form>
      <div class="plug-req-layout">
        <div>
          <div class="card" style="padding:6px" data-s>
            <div class="plug-req-list">
              ${shown.length ? shown.map(r => `<button class="preq${String(pv.req) === String(r.id) ? ' is-selected' : ''}" data-s data-act="req-open" data-id="${esc(r.id)}">
                <time>${time(r.created_at)}</time>
                <span class="pm"><b>${esc(KIND_LABEL[r.kind] || r.kind || '未知类型')}</b><span>${esc(r.owner_id || '无活动标识')} · ${esc(r.model || '模型未记录')}</span></span>
                <span class="pst ${r.status === 'error' || r.status === 'failed' ? 'err' : ''}">${esc(r.status || '状态未记录')}</span>
              </button>`).join('') : '<p class="plug-empty-line" style="padding:14px">没有符合条件的请求。</p>'}
            </div>
            ${list.length > 4 ? `<button class="btn ghost sm" data-act="req-more" style="width:calc(100% - 12px);margin:6px 6px 2px;justify-content:center">${pv.more ? '收起' : `加载更早记录（还有 ${list.length - 4} 条）`}</button>` : ''}
          </div>
        </div>
        <div class="card preq-detail" data-s>${sel ? requestDetail(sel) : '<p class="plug-empty-line">选择一条请求查看来源、参与原因与最终 JSON。</p>'}</div>
      </div>`;
  }

  function requestDetail(r) {
    const participation = Array.isArray(r.plugins) ? r.plugins : [];
    const statusLabel = { used: '已参与', not_applicable: '无需参与', failed: '失败', skipped: '未参与' };
    return `<h2>请求 #${esc(r.id)}<span class="pst ${r.status === 'error' ? 'err' : ''}">${esc(r.status || '状态未记录')}</span></h2>
      <div class="preq-meta">
        ${[['时间', time(r.created_at)], ['活动', r.owner_id], ['类型', KIND_LABEL[r.kind] || r.kind], ['模型', r.model], r.protocol ? ['协议', r.protocol] : null].filter(Boolean).map(([k, v]) => `<div><span>${k}</span><span>${esc(v || '未记录')}</span></div>`).join('')}
      </div>
      ${r.error ? `<div class="plug-error" data-s>请求错误：${esc(r.error)}</div>` : ''}
      <div class="preq-sec"><h3>来源与参与原因</h3>
        ${participation.length ? participation.map(e => {
          const label = statusLabel[e.status] || e.status || (e.used === false ? '未参与' : '已参与');
          return `<div class="preq-row">${esc(e.name || '未命名插件')} · ${esc(label)}${e.reason ? ' · 原因：' + esc(e.reason) : ''}${e.error ? ' · 错误：' + esc(e.error) : ''}${e.required ? ' · 本次必需' : ''}</div>`;
        }).join('') : '<p class="plug-empty-line">此请求没有记录插件参与。</p>'}
      </div>
      ${r.materials !== undefined ? `<div class="preq-sec"><h3>材料</h3><pre class="preq-json">${esc(JSON.stringify(r.materials, null, 2))}</pre></div>` : ''}
      <div class="preq-sec"><h3>最终 JSON</h3><pre class="preq-json">${esc(JSON.stringify(r.body, null, 2))}</pre></div>`;
  }

  /* ---------------------------------------------------------------- 操作 */
  function selectPlugin(id) {
    pv.selected = id; pv.frame = null; pv.dirty = false;
    $$('.plug-item').forEach(b => b.classList.toggle('is-selected', b.dataset.id === id));
    const current = $('.plug-detail');
    if (!current) { renderPlugins(); return; }
    const tmp = document.createElement('div');
    tmp.innerHTML = detailPane(plugin());
    const fresh = tmp.firstElementChild;
    current.replaceWith(fresh);
    N.cascade?.(fresh, 26, 340);
    requestAnimationFrame(() => { if (pv.frame) mountFrame(); });
  }
  function op(id, action) {
    const p = state.plugins.find(x => x.id === id);
    if (!p || pv.busy) return;
    pv.busy = true;
    pv.notice = null;
    if (action === 'load') { p.status = 'loading'; p.progress = '正在加载插件…'; }
    if (action === 'restart') { p.status = 'loading'; p.progress = '正在重载插件…'; }
    if (action === 'unload') { p.status = 'draining'; p.progress = `正在等待 ${p.active_calls || 0} 个在途调用收尾…`; }
    if (action === 'stop') { p.active_calls = 0; p.progress = '已停止在途调用。'; pv.busy = false; renderPlugins(); N.notify('已停止在途调用', 'jade'); N.save(); return; }
    renderPlugins();
    setTimeout(() => {
      if (action === 'load' || action === 'restart') {
        p.enabled = true; p.status = 'loaded'; p.progress = ''; p.error = '';
        N.notify(action === 'load' ? `已加载 ${p.name}` : `已重载 ${p.name}`, 'jade');
      } else if (action === 'unload') {
        p.enabled = false; p.status = 'unloaded'; p.active_calls = 0; p.progress = '';
        N.notify(`已停用 ${p.name}`, 'jade');
      }
      pv.busy = false; renderPlugins(); N.save();
    }, action === 'unload' ? 2100 : 1300);
  }
  async function uninstall(id) {
    const p = state.plugins.find(x => x.id === id);
    if (!p) return;
    const ok = await N.confirmEdit({
      title: `卸载插件「${p.name}」？`,
      description: '会移除插件登记与本地文件；正在进行的调用会先停止。预览中可用左下角重置恢复。',
      submitLabel: '卸载插件',
    });
    if (!ok) return;
    state.plugins = state.plugins.filter(x => x.id !== id);
    pv.selected = state.plugins[0]?.id || null;
    pv.frame = null;
    renderPlugins(); N.notify('插件已卸载', 'jade'); N.save();
  }
  function scan() {
    pv.busy = true; renderPlugins();
    setTimeout(() => {
      const exists = state.plugins.find(p => p.id === 'found-pack');
      if (!exists) {
        state.plugins.push({
          id: 'found-pack', name: '发现的新插件', version: '1.0.0', enabled: false, status: 'unloaded',
          description: '从插件目录发现的本地插件，尚未启用。',
          capabilities: [{ title: '示例工具' }], pages: [],
          settings_schema: { properties: { label: { type: 'string', title: '显示名称', default: '示例' } } },
          settings: { label: '示例' }, active_calls: 0, progress: '', error: '',
          root: 'C:/Users/34375/.nexus/plugins',
        });
        pv.selected = 'found-pack';
        pv.notice = { text: '发现 1 个新插件；新插件默认不启用。' };
        N.notify('已发现 1 个插件', 'jade');
      } else {
        pv.notice = { text: '没有发现新的插件。' };
      }
      pv.busy = false; renderPlugins(); N.save();
    }, 900);
  }
  function upload() { $('#plug-file')?.click(); }
  function handleFile(file) {
    if (!file) return;
    const id = 'uploaded-' + file.name.replace(/\.zip$/i, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 18);
    const conflict = state.plugins.find(p => p.id === id);
    if (conflict) {
      pv.notice = { text: `「${file.name}」与已登记的 ${conflict.name} 版本冲突。替换后默认不启用。`, action: 'plug-replace', label: '替换版本' };
      pv._pendingFile = { file, id };
      renderPlugins();
      return;
    }
    importPlugin(file, id, false);
  }
  function importPlugin(file, id, replace) {
    pv.busy = true; renderPlugins();
    setTimeout(() => {
      if (replace) {
        const p = state.plugins.find(x => x.id === id);
        if (p) { p.version = '1.1.0'; p.enabled = false; p.status = 'unloaded'; p.active_calls = 0; }
      } else {
        state.plugins.push({
          id, name: file.name.replace(/\.zip$/i, ''), version: '1.0.0', enabled: false, status: 'unloaded',
          description: '从 ZIP 导入的插件；新插件默认不启用。',
          capabilities: [{ title: '导入能力' }], pages: [],
          settings_schema: { properties: { enabled_note: { type: 'string', title: '备注', default: '' } } },
          settings: { enabled_note: '' }, active_calls: 0, progress: '', error: '',
          root: 'C:/Users/34375/.nexus/plugins',
        });
        pv.selected = id;
      }
      pv.notice = { text: replace ? '已替换版本；插件默认不启用。' : `已导入「${file.name}」；插件默认不启用。` };
      pv.busy = false; renderPlugins(); N.notify('插件包已导入', 'jade'); N.save();
    }, 1200);
  }
  function saveSettings(id) {
    const p = state.plugins.find(x => x.id === id);
    if (!p) return;
    const status = $('#plug-settings-status');
    const next = {};
    try {
      $$('#plug-settings [data-field]').forEach(el => {
        const key = el.dataset.field;
        const schema = p.settings_schema.properties[key] || {};
        const type = String(schema.type || '').toLowerCase();
        if (type === 'boolean') next[key] = el.checked;
        else if (Array.isArray(schema.enum) && schema.enum.length) next[key] = el.value;
        else if (type === 'number' || type === 'integer') {
          const raw = el.value.trim();
          if (!raw) throw new Error((schema.title || key) + ' 需要填写数值');
          const n = Number(raw);
          if (!Number.isFinite(n) || (type === 'integer' && !Number.isInteger(n))) throw new Error((schema.title || key) + ' 需要填写有效' + (type === 'integer' ? '整数' : '数值'));
          if (schema.minimum !== undefined && n < Number(schema.minimum)) throw new Error((schema.title || key) + ' 不能小于 ' + schema.minimum);
          if (schema.maximum !== undefined && n > Number(schema.maximum)) throw new Error((schema.title || key) + ' 不能大于 ' + schema.maximum);
          next[key] = n;
        } else if (type === 'string') next[key] = el.value;
      });
    } catch (e) {
      status.hidden = false; status.className = 'status err'; status.textContent = e.message;
      return;
    }
    p.settings = next;
    pv.dirty = false;
    status.hidden = false; status.className = 'status'; status.textContent = '设置已保存，立即生效。';
    N.notify('插件设置已保存', 'jade'); N.save();
  }

  /* ---------------------------------------------------------------- 事件 */
  document.addEventListener('click', e => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const act = t.dataset.act;
    if (act === 'plug-tab') {
      if (pv.tab === t.dataset.k) return;
      pv.tab = t.dataset.k;
      renderPlugins();
      N.stagger?.($('.plug-pane'), 26);
      return;
    }
    if (act === 'plug-select') return selectPlugin(t.dataset.id);
    if (act === 'plug-op') {
      if (t.dataset.op === 'uninstall') return uninstall(t.dataset.id);
      return op(t.dataset.id, t.dataset.op);
    }
    if (act === 'plug-page') {
      pv.frame = { pluginId: pv.selected, title: t.dataset.title, path: t.dataset.path };
      return renderPlugins();
    }
    if (act === 'plug-close-frame') { pv.frame = null; return renderPlugins(); }
    if (act === 'plug-refresh') {
      pv.busy = true; renderPlugins();
      setTimeout(() => { pv.busy = false; pv.notice = null; renderPlugins(); N.notify('插件列表已刷新', 'jade'); }, 600);
      return;
    }
    if (act === 'plug-scan') return scan();
    if (act === 'plug-upload') return upload();
    if (act === 'plug-replace') {
      const pending = pv._pendingFile;
      if (pending) importPlugin(pending.file, pending.id, true);
      return;
    }
    if (act === 'plug-save') return saveSettings(t.dataset.id);
    if (act === 'req-open') { pv.req = t.dataset.id; return renderPlugins(); }
    if (act === 'req-more') { pv.more = !pv.more; return renderPlugins(); }
  });
  document.addEventListener('input', e => {
    if (!e.target.closest('#plug-settings')) return;
    pv.dirty = true;
    const status = $('#plug-settings-status');
    if (status) { status.hidden = false; status.className = 'status warn'; status.textContent = '有未保存改动，保存后立即生效。'; }
  });
  document.addEventListener('change', e => {
    if (e.target.id === 'plug-file') { handleFile(e.target.files?.[0]); e.target.value = ''; }
  });
  document.addEventListener('submit', e => {
    if (e.target.id !== 'plug-filters') return;
    e.preventDefault();
    pv.owner = $('#f-owner').value.trim();
    pv.kind = $('#f-kind').value.trim();
    pv.more = false;
    renderPlugins();
  });

  N.renderPlugins = renderPlugins;
})(window.N);
