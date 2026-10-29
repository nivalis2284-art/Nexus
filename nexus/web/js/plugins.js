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

  /* 插件加载在后端是异步的：命令返回时状态仍是 loading，要轮询到它落定。 */
  const BUSY = ['loading', 'draining', 'closing'];
  let watchTimer = 0;
  let watchId = '';
  let watchGaveUp = '';
  function stopWatch() {
    if (watchTimer) clearTimeout(watchTimer);
    watchTimer = 0;
    watchId = '';
  }
  function watchPlugin(id) {
    if (watchId === id || watchGaveUp === id) return;
    stopWatch();
    watchId = id;
    let tries = 0;
    let last = '';
    const tick = async () => {
      watchTimer = 0;
      const current = (state.plugins || []).find(x => x.id === id);
      if (!current) { watchId = ''; return; }
      if (!BUSY.includes(current.status)) {
        watchId = '';
        renderPlugins();
        if (current.error) N.notify(current.error, 'cinnabar', 4200);
        else if (current.status === 'ready') N.notify(`已加载 ${current.name || ''}`, 'jade');
        else if (current.status === 'unloaded') N.notify(`已停用 ${current.name || ''}`, 'jade');
        return;
      }
      if (++tries > 120) {
        watchId = '';
        watchGaveUp = id;
        renderPlugins();
        N.notify('插件仍在加载，稍后刷新查看', 'cinnabar', 4200);
        return;
      }
      try { await N.sync.pluginsOnly(); } catch (_) {}
      const next = (state.plugins || []).find(x => x.id === id);
      const sig = next ? `${next.status}|${next.enabled}|${next.progress}|${next.error}` : '';
      if (sig !== last) { last = sig; renderPlugins(); }
      watchTimer = setTimeout(tick, 500);
    };
    watchTimer = setTimeout(tick, 400);
  }

  function renderPlugins() {
    if (!pv.selected || !state.plugins.some(p => p.id === pv.selected)) pv.selected = state.plugins[0]?.id || null;
    const view = $('#view-plugins');
    const keep = view.scrollTop;
    const p = plugin();
    const frame = $('#plugin-frame');
    const preserve = pv.tab === 'plugins' && frame && pv.frame &&
      pv.frame.pluginId === p?.id && frame.dataset.pluginId === p.id &&
      frame.dataset.pluginPath === pv.frame.path;
    const next = document.createElement('div');
    next.innerHTML = `<div class="page plugins-page">
      <header class="page-head" data-s>
        <div><h1>插件管理</h1><p>查看插件、配置与请求记录。导入或替换版本后，新插件默认不启用。</p></div>
        <span class="pill mono" title="${esc(state.pluginRoot || '')}">${esc(N.shortPath(state.pluginRoot || '插件目录'))}</span>
      </header>
      <div class="tabs" data-s role="tablist"><span class="tab-ink" aria-hidden="true"></span>
        ${[['plugins', '插件'], ['requests', '请求记录']].map(([k, l]) => `<button role="tab" aria-selected="${pv.tab === k}" class="${pv.tab === k ? 'active' : ''}" data-act="plug-tab" data-k="${k}">${l}</button>`).join('')}
      </div>
      <div class="plug-pane">${pv.tab === 'plugins' ? pluginsPane(p) : requestsPane()}</div>
    </div>`;
    if (preserve) {
      // Moving an iframe reloads its document; update only its ancestors' siblings.
      let current = view, fresh = next;
      for (const selector of ['.page', '.plug-pane', '.plug-layout', '.plug-detail']) {
        const retained = current.querySelector(selector), placeholder = fresh.querySelector(selector);
        for (const child of [...current.childNodes]) if (child !== retained) child.remove();
        let after = false;
        for (const child of [...fresh.childNodes]) {
          if (child === placeholder) after = true;
          else current.insertBefore(child, after ? null : retained);
        }
        current = retained;
        fresh = placeholder;
      }
    } else view.replaceChildren(...next.childNodes);
    requestAnimationFrame(() => {
      const tabs = $('.tabs', view);
      if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x', true);
      if (pv.frame) mountFrame();
    });
    view.scrollTop = keep;
    if (pv.animate) {
      pv.animate = false;
      N.cascade?.($('.plug-pane', view), 24, 320);
    }
    const pending = (state.plugins || []).find(x => BUSY.includes(x.status));
    if (pending) watchPlugin(pending.id);
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
    if (!frame || !pv.frame) return;
    const path = String(pv.frame.path || '').replace(/^\/+/, '');
    const url = `/plugins/${encodeURIComponent(pv.frame.pluginId)}/${path}`;
    if (frame.getAttribute('src') !== url) frame.src = url;
    frame.dataset.pluginId = pv.frame.pluginId;
    frame.dataset.pluginPath = pv.frame.path;
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
            ${list.length > 4 || state.modelRequestsNext ? `<button class="btn ghost sm" data-act="req-more" style="width:calc(100% - 12px);margin:6px 6px 2px;justify-content:center">${!pv.more ? '展开记录' : state.modelRequestsNext ? '加载更早记录' : '收起'}</button>` : ''}
          </div>
        </div>
        <div class="card preq-detail" data-s>${sel ? requestDetail(sel) : '<p class="plug-empty-line">选择一条请求查看来源、参与原因与最终 JSON。</p>'}</div>
      </div>`;
  }

  function requestDetail(r) {
    if (r.body === undefined) return '<p class="plug-empty-line">正在读取请求…</p>';
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
  async function openRequest(id) {
    pv.req = id;
    renderPlugins();
    try {
      const detail = await N.api.modelRequest(id);
      const index = state.modelRequests.findIndex(r => String(r.id) === String(id));
      if (index >= 0) state.modelRequests[index] = detail;
      if (pv.req === id) renderPlugins();
    } catch (error) {
      N.notify(error?.message || '读取请求失败', 'cinnabar', 4000);
    }
  }

  async function loadRequests(append = false) {
    const query = new URLSearchParams();
    if (pv.owner) query.set('owner_id', pv.owner);
    if (pv.kind) query.set('kind', pv.kind);
    query.set('limit', '100');
    if (append) query.set('before', state.modelRequestsNext);
    try {
      const value = await N.api.modelRequests(query.toString());
      if (append) value.requests = [...state.modelRequests, ...value.requests];
      N.sync.modelRequests(value);
      renderPlugins();
    } catch (error) {
      N.notify(error?.message || '查询请求记录失败', 'cinnabar', 4000);
    }
  }

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
  async function op(id, action) {
    const p = state.plugins.find(x => x.id === id);
    if (!p || pv.busy) return;
    watchGaveUp = '';
    pv.busy = true;
    pv.notice = null;
    if (pv.frame?.pluginId === id && ['load', 'restart', 'unload'].includes(action)) pv.frame = null;
    renderPlugins();
    try {
      await N.api.pluginCommand(id, action);
      await N.sync.pluginsOnly();
      pv.busy = false;
      pv.animate = true;
      renderPlugins();
      const after = (state.plugins || []).find(x => x.id === id);
      if (after && BUSY.includes(after.status)) watchPlugin(id);
      else N.notify({ load: `已加载 ${p.name}`, restart: `已重载 ${p.name}`, unload: `已停用 ${p.name}`, stop: '已停止在途调用' }[action] || '操作完成', 'jade');
    } catch (error) {
      pv.busy = false;
      await N.sync.pluginsOnly().catch(() => {});
      renderPlugins();
      N.notify(error?.message || '插件操作失败', 'cinnabar', 4200);
    }
  }
  async function uninstall(id) {
    const p = state.plugins.find(x => x.id === id);
    if (!p) return;
    const ok = await N.confirmEdit({
      title: `卸载插件「${p.name}」？`,
      description: '会移除插件登记与本地文件；正在进行的调用会先停止。',
      submitLabel: '卸载插件',
    });
    if (!ok) return;
    pv.busy = true;
    const item = $(`.plug-item[data-id="${CSS.escape(id)}"]`);
    if (item && !N.REDUCED) {
      N.anim(item, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateX(-6px)' }],
        { duration: N.DUR.base, easing: 'ease-in', fill: 'forwards' });
    }
    try {
      await N.api.pluginCommand(id, 'uninstall');
      await N.sync.pluginsOnly();
      pv.busy = false;
      pv.selected = state.plugins[0]?.id || null;
      pv.frame = null;
      pv.animate = true;
      renderPlugins(); N.notify('插件已卸载', 'jade');
      const after = (state.plugins || []).find(x => x.id === id);
      if (after && BUSY.includes(after.status)) watchPlugin(id);
    } catch (error) {
      pv.busy = false;
      renderPlugins();
      N.notify(error?.message || '卸载失败', 'cinnabar', 4200);
    }
  }
  async function scan() {
    pv.busy = true; renderPlugins();
    try {
      await N.api.pluginScan();
      await N.sync.pluginsOnly();
      pv.busy = false;
      pv.notice = { text: state.plugins.length ? `已扫描插件目录，当前登记 ${state.plugins.length} 个插件。` : '插件目录中没有可用插件。' };
      pv.animate = true;
      renderPlugins();
      N.notify('已扫描插件目录', 'jade');
    } catch (error) {
      pv.busy = false;
      renderPlugins();
      N.notify(error?.message || '扫描失败', 'cinnabar', 4200);
    }
  }
  function upload() { $('#plug-file')?.click(); }
  function handleFile(file) {
    if (!file) return;
    const conflict = state.plugins.find(p => file.name.toLowerCase().includes(String(p.id).toLowerCase()));
    if (conflict) {
      pv.notice = { text: `「${file.name}」可能与已登记的 ${conflict.name} 冲突。替换后默认不启用。`, action: 'plug-replace', label: '替换版本' };
      pv._pendingFile = { file };
      renderPlugins();
      return;
    }
    importPlugin(file, false);
  }
  async function importPlugin(file, replace) {
    pv.busy = true; renderPlugins();
    try {
      const result = await N.api.importPlugin(file, replace);
      await N.sync.pluginsOnly();
      pv.busy = false;
      if (result?.conflict) {
        pv.notice = { text: `插件「${result.conflict}」已存在。替换版本后默认不启用。`, action: 'plug-replace', label: '替换版本' };
        pv._pendingFile = { file };
        renderPlugins();
        return;
      }
      if (result?.id) pv.selected = result.id;
      pv.notice = { text: replace ? '已替换版本；插件默认不启用。' : `已导入「${file.name}」；插件默认不启用。` };
      renderPlugins(); N.notify('插件包已导入', 'jade');
    } catch (error) {
      pv.busy = false;
      renderPlugins();
      N.notify(error?.message || '导入失败', 'cinnabar', 4200);
    }
  }
  async function saveSettings(id) {
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
    try {
      await N.api.pluginSettings(id, next);
      await N.sync.pluginsOnly();
      pv.dirty = false;
      renderPlugins();
      const nextStatus = $('#plug-settings-status');
      if (nextStatus) { nextStatus.hidden = false; nextStatus.className = 'status'; nextStatus.textContent = '设置已保存，立即生效。'; }
      N.notify('插件设置已保存', 'jade');
    } catch (error) {
      status.hidden = false; status.className = 'status err'; status.textContent = error?.message || '保存失败';
    }
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
      N.sync.pluginsOnly().then(() => {
        pv.busy = false; pv.animate = true; pv.notice = null; renderPlugins(); N.notify('插件列表已刷新', 'jade');
      }).catch(error => {
        pv.busy = false; renderPlugins();
        N.notify(error?.message || '刷新失败', 'cinnabar', 4200);
      });
      return;
    }
    if (act === 'plug-scan') return scan();
    if (act === 'plug-upload') return upload();
    if (act === 'plug-replace') {
      const pending = pv._pendingFile;
      if (pending) importPlugin(pending.file, true);
      return;
    }
    if (act === 'plug-save') return saveSettings(t.dataset.id);
    if (act === 'req-open') return openRequest(t.dataset.id);
    if (act === 'req-more') {
      if (pv.more && state.modelRequestsNext) return loadRequests(true);
      pv.more = !pv.more;
      return renderPlugins();
    }
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
    return loadRequests();
  });

  N.renderPlugins = renderPlugins;
})(window.N);
