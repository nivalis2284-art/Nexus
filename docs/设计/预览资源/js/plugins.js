/* ==========================================================================
   插件管理：列表、详情、能力、功能页、设置、请求记录
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, state } = N;
  const pv = { tab: 'plugins', selected: null, req: null, owner: '', kind: '', plugin_id: '', dialogue_id: '', job_id: '', purpose: '', more: false, dirty: false, busy: false, frame: null, notice: null };
  const KIND_LABEL = { ling: '对话', task: '工作', subagent: '子代理', chat: '交流' };
  const managerDialog = $('#plugin-manager-dialog');
  let managerScrollTop = 0;
  managerDialog.addEventListener('scroll', e => {
    if (managerDialog.open && e.target.classList.contains('plug-detail-scroll')) managerScrollTop = e.target.scrollTop;
  }, true);

  function managerButton(detail, expanded) {
    const button = $('.plug-expand', detail);
    if (!button) return;
    button.innerHTML = icon(expanded ? 'close' : 'expand');
    button.setAttribute('aria-label', expanded ? '收起插件管理弹窗' : '放大插件管理');
    button.setAttribute('aria-expanded', String(expanded));
  }
  function expandManager() {
    if (managerDialog.open) { N.closeDialog(managerDialog); return; }
    const detail = $('.plug-detail', $('#view-plugins'));
    if (!detail || !plugin()) return;
    const scroll = $('.plug-detail-scroll', detail), top = scroll.scrollTop;
    managerScrollTop = top;
    managerDialog.replaceChildren(detail);
    managerButton(detail, true);
    N.openDialog(managerDialog);
    scroll.scrollTop = top;
  }
  managerDialog.addEventListener('close', () => {
    const detail = $('.plug-detail', managerDialog);
    const slot = $('#plug-detail-slot');
    if (!detail || !slot) return;
    const scroll = $('.plug-detail-scroll', detail), top = managerScrollTop;
    slot.appendChild(detail);
    managerButton(detail, false);
    scroll.scrollTop = top;
    if (state.view === 'plugins') $('.plug-expand', detail)?.focus({ preventScroll: true });
  });

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
    const ink = view.querySelector('.tabs .tab-ink');
    const p = plugin();
    const currentDetail = $('.plug-detail');
    const samePlugin = !!currentDetail && currentDetail.dataset.pluginId === p?.id;
    const detailTop = samePlugin ? $('.plug-detail-scroll', currentDetail)?.scrollTop || 0 : 0;
    const draft = samePlugin && pv.dirty ? $('#plug-settings', currentDetail) : null;
    if (pv.frame) {
      const owner = state.plugins.find(item => item.id === pv.frame.pluginId);
      if (!owner || (pv.frame.wasReady && owner.status !== 'ready')) closePage();
    }
    const next = document.createElement('div');
    next.innerHTML = `<div class="page plugins-page">
      <header class="page-head" data-s>
        <div><h1>插件管理</h1><p>查看插件、配置与请求记录。新导入的插件默认不启用，更新时保留原启用状态。</p></div>
        <span class="pill mono" title="${esc(state.pluginRoot || '')}">${esc(N.shortPath(state.pluginRoot || '插件目录'))}</span>
      </header>
      <div class="tabs" data-s role="tablist"><span class="tab-ink" aria-hidden="true"></span>
        ${[['plugins', '插件'], ['requests', '请求记录']].map(([k, l]) => `<button role="tab" aria-selected="${pv.tab === k}" class="${pv.tab === k ? 'active' : ''}" data-act="plug-tab" data-k="${k}">${l}</button>`).join('')}
      </div>
      <div class="plug-pane">${pv.tab === 'plugins' ? pluginsPane(p) : requestsPane()}</div>
    </div>`;
    view.replaceChildren(...next.childNodes);
    const detail = $('.plug-detail', view);
    if (draft) $('#plug-settings', detail)?.replaceWith(draft);
    if (managerDialog.open) {
      if (p && pv.tab === 'plugins') {
        managerDialog.replaceChildren(detail);
        managerButton(detail, true);
      } else {
        managerDialog.replaceChildren();
        N.closeDialog(managerDialog);
      }
    }
    const detailScroll = detail && $('.plug-detail-scroll', detail);
    if (detailScroll) detailScroll.scrollTop = detailTop;
    if (managerDialog.open) managerScrollTop = detailTop;
    if (ink) view.querySelector('.tabs .tab-ink').replaceWith(ink);
    requestAnimationFrame(() => {
      const tabs = $('.tabs', view);
      if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x', !pv.tabSlide);
      pv.tabSlide = false;
    });
    view.scrollTop = keep;
    if (pv.animate) {
      pv.animate = false;
      N.cascade?.($('.plug-pane', view));
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
            ${x.active_calls ? `<span class="plug-item-calls">进行中 ${x.active_calls}</span>` : ''}
          </button>`;
        }).join('') : '<p class="plug-empty-line">暂无插件。</p>'}
      </div>
      <div id="plug-detail-slot">${detailPane(p)}</div>
    </div>`;
  }

  function detailPane(p) {
    if (!p) return `<div class="card plug-detail" data-s><p class="plug-empty-line">选择一个插件查看详情。</p></div>`;
    const draining = p.status === 'draining' || p.status === 'closing';
    const loading = p.status === 'loading';
    const caps = p.capabilities || [];
    const pages = p.pages || [];
    const props = Object.keys(p.settings_schema?.properties || {});
    return `<div class="card plug-detail" data-plugin-id="${esc(p.id)}" data-s>
      <div class="plug-detail-head" data-s>
        <div style="flex:1;min-width:0">
          <div class="plug-title-row"><h2 id="plug-detail-title">${esc(p.name)}</h2></div>
          <div class="plug-version">v${esc(p.version || '未声明版本')} · ${esc(p.id)}</div>
        </div>
        <button type="button" class="ic-btn plug-expand" data-act="plug-expand" aria-label="放大插件管理" aria-expanded="false" aria-haspopup="dialog">${icon('expand')}</button>
      </div>
      <div class="plug-detail-scroll" tabindex="0" role="region" aria-label="插件管理内容">
      <p class="plug-desc">${esc(p.description || '此插件没有填写简介。')}</p>
      ${p.progress ? `<div class="plug-progress" data-s>${esc(p.progress)}</div>` : ''}
      ${p.error ? `<div class="plug-error" data-s>插件错误：${esc(p.error)}</div>` : ''}
      <div class="plug-status-row" data-s>
        <div class="plug-meta">
        <span>当前调用 ${Number(p.active_calls || 0)}</span>
        <span>运行状态 ${esc(p.status || '—')}</span>
        ${caps.length ? `<span>能力 ${caps.length} 项</span>` : ''}
        </div>
        <div class="plug-enable"><span>启用插件</span><button type="button" class="switch" role="switch" aria-label="启用 ${esc(p.name)}" aria-checked="${!!p.enabled || loading}" aria-busy="${pv.busy || draining || loading}" data-act="plug-op" data-op="${p.enabled || loading ? 'unload' : 'load'}" data-id="${esc(p.id)}" ${pv.busy || draining ? 'disabled' : ''}></button></div>
      </div>
      <div class="plug-actions" data-s>
        ${draining ? '<span class="note">插件正在收尾；页面会自动刷新状态，此时不能重复下发操作。</span>' : ''}
        <div class="plug-action-row">
          ${!draining && !loading && p.enabled ? `<div class="plug-action-group">
               <button class="btn ghost sm" data-act="plug-op" data-op="restart" data-id="${esc(p.id)}" ${pv.busy ? 'disabled' : ''}>重载</button>
               <button class="btn ghost sm" data-act="plug-op" data-op="stop" data-id="${esc(p.id)}">停止当前调用</button>
             </div>` : ''}
          <div class="plug-action-group plug-action-secondary">
            <button class="btn ghost sm" data-act="plug-readme" data-id="${esc(p.id)}">查看介绍</button>
            <button class="btn ghost sm danger" data-act="plug-op" data-op="uninstall" data-id="${esc(p.id)}" ${draining || loading ? 'disabled' : ''}>卸载</button>
          </div>
        </div>
        ${pv.busy ? '<span class="note">正在处理…</span>' : ''}
      </div>

      <div class="plug-sec" data-s><h3>宿主能力与运行</h3><p class="help">查看能力版本、依赖、公开服务、独立对话和作业。</p><button class="btn ghost sm" data-act="host-runtime" data-id="${esc(p.id)}">查看运行状态</button></div>
      <div class="plug-sec" data-s><h3>能力</h3>
        <div class="plug-tags">${caps.length ? caps.map(c => {
          const label = typeof c === 'object' ? (c.title || c.name || c.id || JSON.stringify(c)) : String(c);
          return `<span title="${esc(typeof c === 'object' ? c.description || '' : '')}">${esc(label)}</span>`;
        }).join('') : '<span class="plug-empty-line">未声明能力</span>'}</div>
      </div>

      <div class="plug-sec" data-s><h3>功能页</h3>
        <div class="plug-pages">${pages.length ? pages.map(pg => `<div class="plug-page"><b>${esc(pg.title || pg.id || '功能页')}</b><span>${esc(pg.renderer === 'host' ? 'Nexus UI v1' : pg.path)}</span><button data-act="plug-page" data-page-id="${esc(pg.id)}" data-path="${esc(pg.path || '')}" data-title="${esc(pg.title || pg.id || '功能页')}" data-renderer="${esc(pg.renderer || 'iframe')}" ${pg.renderer === 'host' && p.status !== 'ready' ? 'disabled' : ''}>打开页面</button></div>`).join('') : '<p class="plug-empty-line">此插件没有提供功能页。</p>'}</div>
      </div>

      <div class="plug-sec" data-s><h3>设置</h3>
        ${props.length ? `<div class="plug-settings" id="plug-settings">
          ${props.map(key => fieldHTML(p, key, p.settings_schema.properties[key])).join('')}
          <div class="plug-settings-foot"><span class="status" id="plug-settings-status" hidden></span><button class="btn solid sm" data-act="plug-save" data-id="${esc(p.id)}">保存设置</button></div>
        </div>` : '<p class="plug-empty-line">此插件没有声明可生成的设置项。</p>'}
      </div>
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

  let pageFocus = null;
  let readmePlugin = null;
  let readmeController = null;

  function disposePage() {
    N.hostPages?.dispose();
    pv.frame = null;
    $('#plugin-page-body').replaceChildren();
  }

  function closePage() {
    disposePage();
    N.closeDialog($('#plugin-page-dialog'));
  }

  function openPage(trigger) {
    const owner = plugin();
    if (!owner) return;
    disposePage();
    pageFocus = { pluginId: owner.id, pageId: trigger.dataset.pageId };
    pv.frame = { pluginId: owner.id, title: trigger.dataset.title, path: trigger.dataset.path,
      pageId: trigger.dataset.pageId, renderer: trigger.dataset.renderer || 'iframe',
      wasReady: owner.status === 'ready' };
    $('#plugin-page-title').textContent = `${owner.name} · ${pv.frame.title}`;
    $('#plugin-page-body').innerHTML = pv.frame.renderer === 'host'
      ? '<div id="plugin-host-page"></div>'
      : `<iframe id="plugin-frame" title="${esc(pv.frame.title)}"></iframe>`;
    N.openDialog($('#plugin-page-dialog'));
    mountFrame();
  }

  $('#plugin-page-dialog').addEventListener('close', () => {
    disposePage();
    if (pageFocus?.pluginId === plugin()?.id) {
      $(`[data-act="plug-page"][data-page-id="${CSS.escape(pageFocus.pageId)}"]`)?.focus({ preventScroll: true });
    }
    pageFocus = null;
  });

  $('#plugin-readme-dialog').addEventListener('close', () => {
    readmeController?.abort(); readmeController = null;
    $('#plugin-readme-body').replaceChildren();
    $(`[data-act="plug-readme"][data-id="${CSS.escape(readmePlugin || '')}"]`)?.focus({ preventScroll: true });
    readmePlugin = null;
  });

  async function openReadme(id) {
    const owner = state.plugins.find(item => item.id === id);
    if (!owner) return;
    readmeController?.abort();
    const controller = new AbortController();
    readmeController = controller; readmePlugin = id;
    const body = $('#plugin-readme-body');
    $('#plugin-readme-title').textContent = `${owner.name} · 插件介绍`;
    body.innerHTML = '<p class="help" role="status">正在读取介绍…</p>';
    N.openDialog($('#plugin-readme-dialog'));
    try {
      const response = await N.previewFetch(`/api/plugins/${encodeURIComponent(id)}/readme`, { signal: controller.signal });
      const value = await response.json();
      if (!response.ok) throw new Error(value.error || '读取介绍失败');
      if (controller !== readmeController || controller.signal.aborted) return;
      if (!value.content) {
        body.innerHTML = '<p class="help">此插件未提供介绍文档</p>';
        return;
      }
      body.innerHTML = `<div class="md">${N.md(value.content)}</div>`;
      const base = new URL(`/plugins/${encodeURIComponent(id)}/`, location.origin);
      for (const node of body.querySelectorAll('a, img')) {
        const attr = node.tagName === 'IMG' ? 'src' : 'href';
        const raw = node.dataset.file || node.getAttribute(attr);
        node.removeAttribute('data-file'); node.classList.remove('file-link');
        if (!raw) continue;
        const url = new URL(raw, base);
        const external = /^(https?:|mailto:|tel:)/i.test(raw);
        if (external || (url.origin === base.origin && url.pathname.startsWith(base.pathname))) {
          node.setAttribute(attr, url.href);
          if (attr === 'href') { node.target = '_blank'; node.rel = 'noopener noreferrer'; }
        } else node.removeAttribute(attr);
      }
      N.decorate(body);
    } catch (error) {
      if (controller !== readmeController || controller.signal.aborted) return;
      body.innerHTML = `<p class="sheet-error" role="alert">读取介绍失败：${esc(error.message)}</p><button class="btn ghost sm" data-act="plug-readme" data-id="${esc(id)}">重新读取</button>`;
    }
  }

  function mountFrame() {
    if (pv.frame?.renderer === 'host') {
      const root = $('#plugin-host-page');
      if (root) {
        root.dataset.pluginId = pv.frame.pluginId;
        root.dataset.pluginPath = pv.frame.pageId;
        N.hostPages.mount(root, pv.frame.pluginId, pv.frame.pageId);
      }
      return;
    }
    N.hostPages?.dispose();
    const frame = $('#plugin-frame');
    if (!frame || !pv.frame) return;
    const path = String(pv.frame.path || '').replace(/^\/+/, '');
    const url = '预览资源/iframe示例.html';
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
        ${[['plugin_id','插件'],['dialogue_id','独立对话'],['job_id','插件作业'],['purpose','用途']].map(([key,label])=>`<label>${label}<input id="f-${key}" type="text" value="${esc(pv[key])}"></label>`).join('')}
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

  let requestVersion = 0;
  async function loadRequests(append = false) {
    const version = ++requestVersion;
    const query = new URLSearchParams();
    if (pv.owner) query.set('owner_id', pv.owner);
    if (pv.kind) query.set('kind', pv.kind);
    for (const field of ['plugin_id','dialogue_id','job_id','purpose']) if (pv[field]) query.set(field,pv[field]);
    query.set('limit', '100');
    if (append) query.set('before', state.modelRequestsNext);
    try {
      const value = await N.api.modelRequests(query.toString());
      if (version !== requestVersion) return;
      if (append) value.requests = [...state.modelRequests, ...value.requests];
      N.sync.modelRequests(value);
      renderPlugins();
    } catch (error) {
      if (version !== requestVersion) return;
      N.notify(error?.message || '查询请求记录失败', 'cinnabar', 4000);
    }
  }

  function selectPlugin(id) {
    closePage();
    pv.selected = id; pv.dirty = false;
    $$('.plug-item').forEach(b => b.classList.toggle('is-selected', b.dataset.id === id));
    const current = $('.plug-detail');
    if (!current) { renderPlugins(); return; }
    const tmp = document.createElement('div');
    tmp.innerHTML = detailPane(plugin());
    const fresh = tmp.firstElementChild;
    current.replaceWith(fresh);
    N.cascade?.(fresh);
  }
  async function op(id, action) {
    const p = state.plugins.find(x => x.id === id);
    if (!p || pv.busy) return;
    watchGaveUp = '';
    pv.busy = true;
    pv.notice = null;
    if (pv.frame?.pluginId === id && ['load', 'restart', 'unload'].includes(action)) closePage();
    renderPlugins();
    try {
      await N.api.pluginCommand(id, action);
      await N.sync.pluginsOnly();
      pv.busy = false;
      pv.animate = true;
      renderPlugins();
      const after = (state.plugins || []).find(x => x.id === id);
      if (after && BUSY.includes(after.status)) watchPlugin(id);
      else N.notify({ load: `已加载 ${p.name}`, restart: `已重载 ${p.name}`, unload: `已停用 ${p.name}`, stop: '已停止当前调用' }[action] || '操作完成', 'jade');
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
        { duration: N.DUR.exit, easing: N.EASE_EXIT, fill: 'forwards' });
    }
    try {
      await N.api.pluginCommand(id, 'uninstall');
      await N.sync.pluginsOnly();
      pv.busy = false;
      pv.selected = state.plugins[0]?.id || null;
      closePage();
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
    importPlugin(file, false);
  }
  async function importPlugin(file, replace, pluginId = '') {
    pv.busy = true; renderPlugins();
    try {
      const result = await N.api.importPlugin(file, replace);
      await N.sync.pluginsOnly();
      pv.busy = false;
      if (result?.conflict) {
        renderPlugins();
        const plugin = result.plugin || {};
        const ok = await N.confirmEdit({
          title: '更新插件',
          description: `「${plugin.name || plugin.id}」已安装。当前版本：${result.installed_version || '未标明'}；待安装版本：${plugin.version || '未标明'}。更新时保留设置和数据，并恢复原启用状态。`,
          submitLabel: '更新插件',
        });
        if (ok) await importPlugin(file, true, plugin.id);
        return;
      }
      if (result?.id) pv.selected = result.id;
      const failed = replace && result?.plugins?.find(p => p.id === pluginId && p.status === 'error' && p.error);
      if (failed) throw new Error(failed.error);
      pv.notice = { text: replace ? '更新已开始，插件将按原启用状态恢复。' : `已导入「${file.name}」；插件默认不启用。` };
      renderPlugins(); N.notify(replace ? '插件更新已开始' : '插件包已导入', 'jade');
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
      closePage();
      pv.tab = t.dataset.k;
      pv.tabSlide = true;
      renderPlugins();
      N.stagger?.($('.plug-pane'));
      return;
    }
    if (act === 'plug-select') return selectPlugin(t.dataset.id);
    if (act === 'plug-expand') return expandManager();
    if (act === 'plug-op') {
      if (t.dataset.op === 'uninstall') return uninstall(t.dataset.id);
      return op(t.dataset.id, t.dataset.op);
    }
    if (act === 'plug-page') return openPage(t);
    if (act === 'plug-readme') return openReadme(t.dataset.id);
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
    for (const field of ['plugin_id','dialogue_id','job_id','purpose']) pv[field] = $('#f-'+field).value.trim();
    pv.more = false;
    return loadRequests();
  });

  N.renderPlugins = renderPlugins;
  N.closePluginPage = () => {
    closePage();
    N.closeDialog(managerDialog);
    N.closeDialog($('#plugin-readme-dialog'));
  };
})(window.N);
