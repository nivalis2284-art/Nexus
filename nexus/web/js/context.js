/* ==========================================================================
   上下文管理：所有者指令、角色、自动组装段、历史
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, state } = N;
  const ctx = { tab: 'system', selected: new Set(), undo: null, canUndo: false, busy: false, enteredAt: 0 };

  const ROLE_CAPTION = { main: '持续交流与工作监督', work: '围绕目标持续执行', subagent: '聚焦独立的委派范围' };
  const MSG_LABEL = { user: '所有者', assistant: '灵', tool: '工具', notice: '记录' };
  const SEGS = [
    ['系统提示词', () => state.context.system, 'var(--ink)'],
    ['工具定义', () => state.context.tools, 'var(--cinnabar)'],
    ['对话消息', () => state.context.messages, 'var(--jade)'],
  ];

  const locked = () => state.replying;

  function tabsHTML() {
    const d = state.contextData;
    const counts = { system: (d.owner_instructions || []).length + (d.roles || []).length + (d.sections || []).length, history: (d.turns || []).length };
    return `<div class="tabs" data-s role="tablist"><span class="tab-ink" aria-hidden="true"></span>
      ${[['system', '系统指令'], ['history', '历史上下文']].map(([k, l]) => `<button role="tab" aria-selected="${ctx.tab === k}" class="${ctx.tab === k ? 'active' : ''}" data-act="ctx-tab" data-k="${k}">${l}<span class="tab-count">${counts[k]} ${k === 'system' ? '项' : '条'}</span></button>`).join('')}
    </div>`;
  }

  function systemPanel() {
    const d = state.contextData;
    const owners = d.owner_instructions || [];
    const roles = d.roles || [];
    const sections = d.sections || [];
    return `<div class="ctx-panel" data-panel="system">
      <section class="card ctx-card" data-s>
        <div class="ctx-card-head">
          <div><h2>所有者指令</h2><p>每条指令独立生效，可逐条编辑、删除或新增。</p></div>
          <div class="ctx-card-acts"><span class="pill jade">可编辑</span><button class="btn ghost sm" data-act="add-rule" ${locked() ? 'disabled' : ''}>${icon('plus', 'i xs')}添加指令</button></div>
        </div>
        <div class="card ctx-owner">
          ${owners.length ? owners.map((r, i) => `<div class="ctx-owner-row" data-s data-i="${i}"><span class="ctx-idx">${String(i + 1).padStart(2, '0')}</span><p>${esc(r)}</p><div class="ctx-owner-acts"><button data-act="edit-rule" data-i="${i}" ${locked() ? 'disabled' : ''}>编辑</button><button class="del" data-act="del-rule" data-i="${i}" ${locked() ? 'disabled' : ''}>删除</button></div></div>`).join('') : '<p class="ctx-hint">还没有所有者指令。</p>'}
        </div>
        <p class="ctx-hint">当前明确要求优先于一般偏好；这里的内容不代替实际权限设置。</p>
      </section>

      <section class="card ctx-card" data-s>
        <div class="ctx-card-head"><div><h2>角色与工作规则</h2><p>三个角色分别生效，编辑后在弹窗中保存。</p></div><span class="pill">可编辑</span></div>
        <div class="ctx-roles">
          ${roles.map(role => `<article class="ctx-role" data-s>
            <div class="ctx-role-top"><b>${esc(role.title)}</b><button data-act="edit-role" data-id="${esc(role.id)}" ${locked() ? 'disabled' : ''}>编辑</button></div>
            <span class="cap">${esc(ROLE_CAPTION[role.id] || '生效中的角色规则')}</span>
            <div class="txt">${esc(role.text)}</div>
          </article>`).join('')}
        </div>
      </section>

      <div class="sec-h" data-s><h3>本次自动组装的信息</h3><span>由当前环境与运行状态提供 · 只读</span></div>
      <div class="ctx-sections">
        ${sections.map((s, i) => `<details class="ctx-sec" data-s ${i === 0 ? 'open' : ''}>
          <summary><i class="dot"></i>${esc(s.title)}<span class="lock">只读</span>${icon('chev', 'i')}</summary>
          <div class="body">${esc(s.text)}</div>
        </details>`).join('')}
      </div>
    </div>`;
  }

  function turnClock(at) {
    const text = String(at || '');
    const hit = /T(\d{2}):(\d{2})/.exec(text);
    return hit ? hit[1] + ':' + hit[2] : '';
  }

  /* 一轮对话一条记录：只显示提问与回复正文，思考与工具调用收在里面。 */
  function historyPanel() {
    const d = state.contextData;
    const turns = d.turns || [];
    const sum = d.summary;
    const sel = [...ctx.selected].filter(i => turns[i]);
    return `<div class="ctx-panel" data-panel="history">
      ${ctx.canUndo ? `<div class="ctx-undo" data-s>已修改交流记录<button data-act="ctx-undo" ${locked() ? 'disabled' : ''}>撤销最近一次修改</button></div>` : ''}
      ${locked() ? '<div class="ctx-lock" data-s>灵正在处理当前输入，请先停止后再编辑或删除历史。</div>' : ''}
      <section class="card ctx-card" data-s>
        <div class="ctx-card-head"><div><h2>已压缩上下文</h2><p>此前交流收束为摘要；编辑会替换当前摘要。</p></div><button class="btn ghost sm" data-act="edit-summary" ${!sum || locked() ? 'disabled' : ''}>${icon('edit', 'i xs')}编辑摘要</button></div>
        ${sum ? `<div class="ctx-summary"><div class="ctx-summary-meta"><span>覆盖此前 ${Number(sum.through) + 1} 条记录</span><span>编辑后影响后续请求</span></div><p>${esc(sum.text)}</p></div>` : '<p class="ctx-hint">当前还没有压缩摘要。</p>'}
        <p class="ctx-hint">摘要是此前的工作记录，最新的所有者要求仍然优先。</p>
      </section>

      <section class="card ctx-card" data-s>
        <div class="ctx-card-head"><div><h2>未压缩上下文</h2><p>一轮对话算一条；只显示提问与回复正文。</p></div><span class="pill">${turns.length} 轮</span></div>
        <div class="ctx-toolbar">
          <label class="ctx-check"><input type="checkbox" data-act="ctx-all" ${sel.length === turns.length && turns.length ? 'checked' : ''} ${locked() || !turns.length ? 'disabled' : ''}>全选<span class="sel">${sel.length ? '· 已选 ' + sel.length + ' 轮' : ''}</span></label>
          <button class="btn ghost sm danger" data-act="ctx-del" ${locked() || !sel.length ? 'disabled' : ''}>删除所选${sel.length ? ` (${sel.length})` : ''}</button>
        </div>
        <div class="ctx-msgs">
          ${turns.length ? turns.map((t, i) => {
            const on = ctx.selected.has(i);
            const meta = [turnClock(t.at), t.tools ? `${t.tools} 次工具调用` : '', t.indices.length > 2 ? `${t.indices.length} 条记录` : ''].filter(Boolean).join(' · ');
            return `<article class="ctx-msg ctx-turn${on ? ' is-selected' : ''}" data-turn="${i}">
              <input type="checkbox" data-act="ctx-pick" data-i="${i}" ${on ? 'checked' : ''} ${locked() ? 'disabled' : ''} aria-label="选择第 ${i + 1} 轮对话">
              <div class="ctx-turn-body">
                <div class="ctx-turn-q">${esc(t.user || '（无提问）')}</div>
                <div class="ctx-turn-a">${esc(t.reply || '（本轮没有回复正文）')}</div>
                ${meta ? `<div class="ctx-turn-meta">${esc(meta)}</div>` : ''}
              </div>
            </article>`;
          }).join('') : '<p class="ctx-hint">没有未压缩的会话消息。</p>'}
        </div>
        <p class="ctx-hint">删除一轮会连同这一轮的思考与工具记录一起移除；会话尚未继续时，可撤销最近一次历史修改。</p>
      </section>
    </div>`;
  }

  function syncSelection() {
    const turns = state.contextData.turns || [];
    $$('#view-context .ctx-turn').forEach(row => {
      const i = +row.dataset.turn;
      const cb = row.querySelector('input[data-act="ctx-pick"]');
      const on = ctx.selected.has(i);
      if (cb) cb.checked = on;
      row.classList.toggle('is-selected', on);
    });
    const all = $('#view-context [data-act="ctx-all"]');
    if (all) {
      all.checked = turns.length > 0 && ctx.selected.size === turns.length;
      all.indeterminate = ctx.selected.size > 0 && ctx.selected.size < turns.length;
    }
    const label = $('#view-context .ctx-check .sel');
    if (label) label.textContent = ctx.selected.size ? `· 已选 ${ctx.selected.size} 轮` : '';
    const del = $('#view-context [data-act="ctx-del"]');
    if (del) {
      del.disabled = locked() || !ctx.selected.size;
      del.textContent = ctx.selected.size ? `删除所选 (${ctx.selected.size})` : '删除所选';
    }
  }

  function growMeter(view, pct) {
    if (N.REDUCED) return;
    const big = $('.ctx-big strong', view);
    if (big) {
      if (pct === null) big.textContent = '—';
      else {
        big.textContent = '0';
        setTimeout(() => N.countUp?.(big, pct, 760 * N.motionScale()), 140 * N.motionScale());
      }
    }
    const wrap = $('.ctx-big', view);
    if (wrap) N.anim(wrap, [{ opacity: 0, transform: 'scale(.92)' }, { opacity: 1, transform: 'none' }],
      { duration: N.DUR.enter, delay: 120, easing: N.SP.soft.e });
    $$('.ctx-meter i', view).forEach((n, i) => N.anim(n, [{ transform: 'scaleX(0)' }, { transform: 'none' }],
      { duration: N.DUR.enter, delay: 200 + i * 70, easing: N.SP.soft.e, fill: 'backwards' }));
    $$('.ctx-legend span', view).forEach((n, i) => N.anim(n, [{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }],
      { duration: N.DUR.move, delay: 430 + i * 60, easing: N.EASE_OUT, fill: 'backwards' }));
  }

  function renderContext() {
    const view = $('#view-context');
    const entering = N.takeEnter?.('context');
    const keep = view.scrollTop;
    const openSecs = $$('.ctx-sec', view).map(d => d.open);
    const c = state.context;
    const used = (c.system || 0) + (c.tools || 0) + (c.messages || 0);
    const known = used > 0;
    const pct = known && c.window ? Math.min(100, Math.round(used / c.window * 100)) : null;
    const denom = used || 1;
    const d = state.contextData;
    view.innerHTML = `<div class="page ctx-page">
      <header class="page-head" data-s>
        <div><p class="eyebrow">CONTEXT WORKSPACE</p><h1>上下文管理</h1><p>查看灵正在参考的信息，让指令与历史各归其位。</p></div>
        <div class="ctx-head-acts"><button class="btn ghost sm" data-act="ctx-refresh">${icon('refresh', 'i xs')}刷新</button><button class="btn ghost sm" data-act="back-chat">${icon('back', 'i xs')}返回主交流</button></div>
      </header>

      <section class="card ctx-overview" data-s>
        <div class="ctx-ov-top">
          <div class="ctx-big"><strong${pct === null ? ' class="unknown"' : ''}>${pct === null ? '—' : pct}</strong>${pct === null ? '' : '<small>%</small>'}</div>
          <div class="ctx-ov-main"><p>当前请求已用上下文<br>按本轮运行条件自动组装</p>
            <span class="ctx-state ${locked() ? 'locked' : ''}"><i></i>${locked() ? '灵正在处理，历史修改需先停止' : '灵当前空闲，可调整后续请求参考的上下文'}</span></div>
          <span class="ctx-window">模型窗口 ${N.fmtNum(c.window)} tok</span>
        </div>
        <div class="ctx-meter">${SEGS.map(([n, get, color]) => `<i style="width:${Math.round(get() / denom * 100)}%;background:${color}" title="${n} ${N.approx(get())}"></i>`).join('')}</div>
        <div class="ctx-legend">${SEGS.map(([n, get, color]) => `<span><i style="background:${color}"></i>${n}<b>${N.approx(get())}</b></span>`).join('')}</div>
        <div class="ctx-metrics">
          <div><span>所有者指令</span><strong>${(d.owner_instructions || []).length} 条</strong></div>
          <div><span>角色</span><strong>${(d.roles || []).length} 个</strong></div>
          <div><span>未压缩对话</span><strong>${(d.turns || []).length} 轮</strong></div>
          <div><span>压缩摘要</span><strong>${d.summary ? '覆盖 ' + (Number(d.summary.through) + 1) + ' 条' : '暂无'}</strong></div>
        </div>
      </section>

      ${tabsHTML()}
      ${ctx.tab === 'system' ? systemPanel() : historyPanel()}
    </div>`;
    $$('.ctx-sec', view).forEach((d, i) => {
      if (openSecs[i] !== undefined) d.open = openSecs[i];
    });
    view.scrollTop = keep;
    maybeSync();
    if (entering) {
      ctx.enteredAt = Date.now();
      growMeter(view, pct);
      setTimeout(() => {
        const tabs = $('.tabs', view);
        if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x');
      }, 170);
    } else {
      requestAnimationFrame(() => {
        const tabs = $('.tabs', view);
        if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x', true);
      });
    }
  }

  /* 页面只在进入时读一次内存快照，聊完回来还是旧数字；这里补一次自动刷新。 */
  let syncing = false;
  let lastSync = 0;
  function maybeSync() {
    if (syncing || Date.now() - lastSync < 1500) return;
    /* 入场动画还没播完就整页重绘，会把逐段登场打断；等它落定再拉。 */
    const wait = (ctx.enteredAt || 0) + 700 - Date.now();
    if (wait > 0) { setTimeout(maybeSync, wait); return; }
    syncing = true;
    Promise.resolve()
      .then(() => N.sync.contextOnly())
      .then(() => N.sync.chatOnly())
      .then(() => {
        lastSync = Date.now();
        N.composeItems?.();
        renderContext();
      })
      .catch(() => {})
      .finally(() => { syncing = false; });
  }

  /* ---------------------------------------------------------------- 操作 */
  async function saveOwners(items, message) {
    try {
      await N.api.saveContext('owners', { items });
      await N.sync.contextOnly();
      renderContext(); N.notify(message, 'jade');
    } catch (error) {
      N.notify(error?.message || '保存指令失败', 'cinnabar', 4200);
      await N.sync.contextOnly().catch(() => {});
      renderContext();
    }
  }

  async function addRule() {
    const value = await N.ask({
      title: '添加所有者指令', text: '', submitLabel: '添加指令',
      description: '保存后用于后续请求；新指令追加在现有指令之后。',
    });
    if (value === null) return;
    await saveOwners([...(state.contextData.owner_instructions || []), String(value).trim()], '已添加指令');
  }

  async function editRule(i) {
    const list = state.contextData.owner_instructions || [];
    const value = await N.ask({
      title: `编辑所有者指令 ${i + 1}`, text: list[i],
      description: '保存后用于后续请求；取消不会修改现有指令。',
    });
    if (value === null) return;
    const items = list.slice();
    items[i] = String(value).trim();
    await saveOwners(items, '已保存指令');
  }

  async function delRule(i) {
    const list = state.contextData.owner_instructions || [];
    const ok = await N.confirmEdit({
      title: '删除这条所有者指令？', description: list[i], submitLabel: '删除指令',
    });
    if (!ok) return;
    const row = $(`.ctx-owner-row[data-i="${i}"]`);
    if (row && !N.REDUCED) {
      await N.anim(row, [{ opacity: 1, height: row.offsetHeight + 'px' }, { opacity: 0, height: '0px', transform: 'scale(.98)' }],
        { duration: N.DUR.base, easing: 'ease-in', fill: 'forwards' }).finished;
    }
    const items = list.slice();
    items.splice(i, 1);
    await saveOwners(items, '已删除指令');
  }

  async function editRole(id) {
    const role = (state.contextData.roles || []).find(r => r.id === id);
    if (!role) return;
    const value = await N.ask({
      title: `编辑${role.title}规则`, text: role.text,
      description: '保存后用于后续请求；取消不会修改现有规则。',
    });
    if (value === null) return;
    try {
      await N.api.saveContext('roles', { id, text: String(value).trim() });
      await N.sync.contextOnly();
      renderContext(); N.notify('角色规则已保存', 'jade');
    } catch (error) {
      N.notify(error?.message || '保存角色规则失败', 'cinnabar', 4200);
    }
  }

  async function editSummary() {
    const sum = state.contextData.summary;
    if (!sum) return;
    if (locked()) { N.notify('请先停止灵的当前输入再修改摘要', 'cinnabar'); return; }
    const value = await N.ask({
      title: '编辑已压缩上下文', text: sum.text,
      description: '摘要会替代此前记录；最新的所有者要求仍然优先。',
    });
    if (value === null) return;
    try {
      await N.api.saveContext('summary', {
        text: String(value).trim(),
        version: state.contextData.version,
        index: sum.index,
      });
      await N.sync.contextOnly();
      ctx.canUndo = true;
      renderContext(); N.notify('历史摘要已保存', 'jade');
    } catch (error) {
      N.notify(error?.message || '保存摘要失败', 'cinnabar', 4200);
    }
  }

  async function delTurns() {
    const turns = state.contextData.turns || [];
    const picked = [...ctx.selected].filter(i => turns[i]).sort((a, b) => a - b);
    if (!picked.length) return;
    if (locked()) { N.notify('请先停止灵的当前输入再删除历史', 'cinnabar'); return; }
    const indices = [...new Set(picked.flatMap(i => turns[i].indices))].sort((a, b) => a - b);
    const ok = await N.confirmEdit({
      title: `删除 ${picked.length} 轮对话？`,
      description: '会连同这一轮的思考、工具调用与任务事件一起移除；已执行的工具与后台工作不会回滚。',
      submitLabel: '删除对话',
    });
    if (!ok) return;
    const rows = picked.map(i => $(`#view-context .ctx-turn[data-turn="${i}"]`)).filter(Boolean);
    if (rows.length && !N.REDUCED) {
      await Promise.all(rows.map((row, k) => N.anim(row,
        [{ opacity: 1, height: row.offsetHeight + 'px' }, { opacity: 0, height: '0px', transform: 'scale(.97)' }],
        { duration: N.DUR.base, delay: k * 18, easing: 'ease-in', fill: 'forwards' }).finished));
    }
    try {
      await N.api.history({ operation: 'delete', version: state.contextData.version, indices });
      await N.sync.contextOnly();
      await N.sync.chatOnly();
      N.composeItems?.(); N.renderChat?.();
      ctx.selected.clear(); ctx.canUndo = true;
      renderContext(); N.notify('已删除所选对话', 'jade');
    } catch (error) {
      N.notify(error?.message || '删除对话失败', 'cinnabar', 4200);
      await N.sync.contextOnly().catch(() => {});
      renderContext();
    }
  }

  async function undo() {
    try {
      await N.api.history({ operation: 'undo', version: state.contextData.version });
      await N.sync.contextOnly();
      await N.sync.chatOnly();
      N.composeItems?.(); N.renderChat?.();
      ctx.selected.clear(); ctx.canUndo = false;
      renderContext(); N.notify('已撤销最近一次历史修改', 'jade');
    } catch (error) {
      N.notify(error?.message || '撤销失败', 'cinnabar', 4200);
    }
  }

  async function refresh() {
    const btn = $('[data-act="ctx-refresh"]');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span class="spin">${icon('refresh', 'i xs')}</span>刷新中`;
    }
    try {
      await N.sync.contextOnly();
      await N.sync.chatOnly();
      lastSync = Date.now();
      N.composeItems?.();
      renderContext();
      N.notify('上下文已刷新', 'jade');
    } catch (error) {
      N.notify(error?.message || '刷新失败', 'cinnabar', 4200);
      renderContext();
    }
  }

  /* ---------------------------------------------------------------- 委托 */
  document.addEventListener('click', e => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const act = t.dataset.act;
    if (!act?.startsWith('ctx-') && !['add-rule', 'edit-rule', 'del-rule', 'edit-role', 'edit-summary', 'back-chat'].includes(act)) return;
    if (act === 'ctx-tab') {
      if (ctx.tab === t.dataset.k) return;
      ctx.tab = t.dataset.k;
      const oldPanel = $('.ctx-panel');
      const swap = () => {
        renderContext();
        const panel = $('.ctx-panel');
        if (panel) N.stagger(panel, 24);
      };
      if (oldPanel && !N.REDUCED) {
        N.anim(oldPanel, [{ opacity: 1 }, { opacity: 0, transform: 'translateY(-4px)' }], { duration: N.DUR.fast, easing: 'ease-in' }).onfinish = swap;
      } else swap();
      return;
    }
    if (act === 'add-rule') return addRule();
    if (act === 'edit-rule') return editRule(+t.dataset.i);
    if (act === 'del-rule') return delRule(+t.dataset.i);
    if (act === 'edit-role') return editRole(t.dataset.id);
    if (act === 'edit-summary') return editSummary();
    if (act === 'ctx-all') {
      const turns = state.contextData.turns || [];
      ctx.selected = t.checked ? new Set(turns.map((_, i) => i)) : new Set();
      syncSelection(); return;
    }
    if (act === 'ctx-pick') {
      const i = +t.dataset.i;
      if (t.checked) ctx.selected.add(i); else ctx.selected.delete(i);
      syncSelection(); return;
    }
    if (act === 'ctx-del') return delTurns();
    if (act === 'ctx-undo') return undo();
    if (act === 'ctx-refresh') return refresh();
    if (act === 'back-chat') return N.go('chat');
  });

  N.renderContext = renderContext;
  N.ctxLock = locked;
})(window.N);
