/* ==========================================================================
   上下文管理：所有者指令、角色、自动组装段、历史
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, state } = N;
  const ctx = { tab: 'system', selected: new Set(), undo: null, canUndo: false, busy: false };

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
    const counts = { system: (d.owner_instructions || []).length + (d.roles || []).length + (d.sections || []).length, history: (d.messages || []).length };
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
          ${owners.length ? owners.map((r, i) => `<div class="ctx-owner-row" data-s><span class="ctx-idx">${String(i + 1).padStart(2, '0')}</span><p>${esc(r)}</p><div class="ctx-owner-acts"><button data-act="edit-rule" data-i="${i}" ${locked() ? 'disabled' : ''}>编辑</button><button class="del" data-act="del-rule" data-i="${i}" ${locked() ? 'disabled' : ''}>删除</button></div></div>`).join('') : '<p class="ctx-hint">还没有所有者指令。</p>'}
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

  function historyPanel() {
    const d = state.contextData;
    const msgs = d.messages || [];
    const sum = d.summary;
    const sel = [...ctx.selected].filter(i => msgs.some(m => m.index === i));
    return `<div class="ctx-panel" data-panel="history">
      ${ctx.canUndo ? `<div class="ctx-undo" data-s>已修改交流记录<button data-act="ctx-undo" ${locked() ? 'disabled' : ''}>撤销最近一次修改</button></div>` : ''}
      ${locked() ? '<div class="ctx-lock" data-s>灵正在处理当前输入，请先停止后再编辑或删除历史。</div>' : ''}
      <section class="card ctx-card" data-s>
        <div class="ctx-card-head"><div><h2>已压缩上下文</h2><p>此前交流收束为摘要；编辑会替换当前摘要。</p></div><button class="btn ghost sm" data-act="edit-summary" ${!sum || locked() ? 'disabled' : ''}>${icon('edit', 'i xs')}编辑摘要</button></div>
        ${sum ? `<div class="ctx-summary"><div class="ctx-summary-meta"><span>覆盖此前 ${Number(sum.through) + 1} 条记录</span><span>编辑后影响后续请求</span></div><p>${esc(sum.text)}</p></div>` : '<p class="ctx-hint">当前还没有压缩摘要。</p>'}
        <p class="ctx-hint">摘要是此前的工作记录，最新的所有者要求仍然优先。</p>
      </section>

      <section class="card ctx-card" data-s>
        <div class="ctx-card-head"><div><h2>未压缩上下文</h2><p>保留的会话消息，可选择删除。</p></div><span class="pill">${msgs.length} 条</span></div>
        <div class="ctx-toolbar">
          <label class="ctx-check"><input type="checkbox" data-act="ctx-all" ${sel.length === msgs.length && msgs.length ? 'checked' : ''} ${locked() || !msgs.length ? 'disabled' : ''}>全选消息<span class="sel">${sel.length ? '· 已选 ' + sel.length + ' 条' : ''}</span></label>
          <button class="btn ghost sm danger" data-act="ctx-del" ${locked() || !sel.length ? 'disabled' : ''}>删除所选${sel.length ? ` (${sel.length})` : ''}</button>
        </div>
        <div class="ctx-msgs">
          ${msgs.length ? msgs.map(m => {
            const on = ctx.selected.has(m.index);
            const label = MSG_LABEL[m.role] || m.role;
            const avatar = m.role === 'user' ? '我' : m.role === 'assistant' ? '灵' : m.role === 'tool' ? '具' : '记';
            return `<article class="ctx-msg${on ? ' is-selected' : ''}">
              <input type="checkbox" data-act="ctx-pick" data-i="${m.index}" ${on ? 'checked' : ''} ${locked() ? 'disabled' : ''} aria-label="选择第 ${m.index + 1} 条${label}消息">
              <span class="ctx-avatar role-${esc(m.role)}">${avatar}</span>
              <div><div class="ctx-msg-meta"><strong>${label}</strong>${m.at ? `<span>${esc(String(m.at).slice(11, 16))}</span>` : ''}${m.name ? `<span>${esc(m.name)}</span>` : ''}</div>
              <div class="ctx-msg-text">${esc(m.text || (m.name ? '调用 ' + m.name : ''))}</div></div>
            </article>`;
          }).join('') : '<p class="ctx-hint">没有未压缩的会话消息。</p>'}
        </div>
        <p class="ctx-hint">删除前会再次确认；会话尚未继续时，可撤销最近一次历史修改。</p>
      </section>
    </div>`;
  }

  function syncSelection() {
    const msgs = state.contextData.messages || [];
    const sel = ctx.selected;
    $$('#view-context .ctx-msg').forEach(row => {
      const cb = row.querySelector('input[data-act="ctx-pick"]');
      if (!cb) return;
      const on = sel.has(+cb.dataset.i);
      cb.checked = on;
      row.classList.toggle('is-selected', on);
    });
    const all = $('#view-context [data-act="ctx-all"]');
    if (all) {
      all.checked = msgs.length > 0 && sel.size === msgs.length;
      all.indeterminate = sel.size > 0 && sel.size < msgs.length;
    }
    const label = $('#view-context .ctx-check .sel');
    if (label) label.textContent = sel.size ? `· 已选 ${sel.size} 条` : '';
    const del = $('#view-context [data-act="ctx-del"]');
    if (del) {
      del.disabled = locked() || !sel.size;
      del.textContent = sel.size ? `删除所选 (${sel.size})` : '删除所选';
    }
  }

  function growMeter(view, pct) {
    if (N.REDUCED) return;
    const big = $('.ctx-big strong', view);
    if (big) {
      big.textContent = '0';
      setTimeout(() => N.countUp?.(big, pct, 760 * N.motionScale()), 140 * N.motionScale());
    }
    const wrap = $('.ctx-big', view);
    if (wrap) N.anim(wrap, [{ opacity: 0, transform: 'scale(.92)' }, { opacity: 1, transform: 'none' }],
      { duration: 560, delay: 120, easing: N.SP.soft.e });
    $$('.ctx-meter i', view).forEach((n, i) => N.anim(n, [{ transform: 'scaleX(0)' }, { transform: 'none' }],
      { duration: 640, delay: 200 + i * 70, easing: N.SP.soft.e, fill: 'backwards' }));
    $$('.ctx-legend span', view).forEach((n, i) => N.anim(n, [{ opacity: 0, transform: 'translateY(4px)' }, { opacity: 1, transform: 'none' }],
      { duration: 380, delay: 430 + i * 60, easing: N.EASE_OUT, fill: 'backwards' }));
  }

  function renderContext() {
    const view = $('#view-context');
    const entering = N.takeEnter?.('context');
    const keep = view.scrollTop;
    const c = state.context;
    const used = (c.system || 0) + (c.tools || 0) + (c.messages || 0);
    const pct = c.window ? Math.min(100, Math.round(used / c.window * 100)) : 0;
    const denom = used || 1;
    const d = state.contextData;
    view.innerHTML = `<div class="page ctx-page">
      <header class="page-head" data-s>
        <div><p class="eyebrow">CONTEXT WORKSPACE</p><h1>上下文管理</h1><p>查看灵正在参考的信息，让指令与历史各归其位。</p></div>
        <div class="ctx-head-acts"><button class="btn ghost sm" data-act="ctx-refresh">${icon('refresh', 'i xs')}刷新</button><button class="btn ghost sm" data-act="back-chat">${icon('back', 'i xs')}返回主交流</button></div>
      </header>

      <section class="card ctx-overview" data-s>
        <div class="ctx-ov-top">
          <div class="ctx-big"><strong>${pct}</strong><small>%</small></div>
          <div class="ctx-ov-main"><p>当前请求已用上下文<br>按本轮运行条件自动组装</p>
            <span class="ctx-state ${locked() ? 'locked' : ''}"><i></i>${locked() ? '灵正在处理，历史修改需先停止' : '灵当前空闲，可调整后续请求参考的上下文'}</span></div>
          <span class="ctx-window">模型窗口 ${N.fmtNum(c.window)} tok</span>
        </div>
        <div class="ctx-meter">${SEGS.map(([n, get, color]) => `<i style="width:${Math.round(get() / denom * 100)}%;background:${color}" title="${n} ${N.approx(get())}"></i>`).join('')}</div>
        <div class="ctx-legend">${SEGS.map(([n, get, color]) => `<span><i style="background:${color}"></i>${n}<b>${N.approx(get())}</b></span>`).join('')}</div>
        <div class="ctx-metrics">
          <div><span>所有者指令</span><strong>${(d.owner_instructions || []).length} 条</strong></div>
          <div><span>角色</span><strong>${(d.roles || []).length} 个</strong></div>
          <div><span>未压缩消息</span><strong>${(d.messages || []).length} 条</strong></div>
          <div><span>压缩摘要</span><strong>${d.summary ? '覆盖 ' + (Number(d.summary.through) + 1) + ' 条' : '暂无'}</strong></div>
        </div>
      </section>

      ${tabsHTML()}
      ${ctx.tab === 'system' ? systemPanel() : historyPanel()}
    </div>`;
    view.scrollTop = keep;
    if (entering) {
      N.stagger(view, 24);
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

  /* ---------------------------------------------------------------- 操作 */
  async function addRule() {
    const value = await N.ask({
      title: '添加所有者指令', text: '', submitLabel: '添加指令',
      description: '保存后用于后续请求；新指令追加在现有指令之后。',
    });
    if (value === null) return;
    state.contextData.owner_instructions.push(String(value).trim());
    renderContext(); N.notify('已添加指令', 'jade'); N.save();
  }
  async function editRule(i) {
    const list = state.contextData.owner_instructions;
    const value = await N.ask({
      title: `编辑所有者指令 ${i + 1}`, text: list[i],
      description: '保存后用于后续请求；取消不会修改现有指令。',
    });
    if (value === null) return;
    list[i] = String(value).trim();
    renderContext(); N.notify('已保存指令', 'jade'); N.save();
  }
  async function delRule(i) {
    const list = state.contextData.owner_instructions;
    const ok = await N.confirmEdit({
      title: '删除这条所有者指令？', description: list[i], submitLabel: '删除指令',
    });
    if (!ok) return;
    list.splice(i, 1);
    renderContext(); N.notify('已删除指令', 'jade'); N.save();
  }
  async function editRole(id) {
    const role = state.contextData.roles.find(r => r.id === id);
    if (!role) return;
    const value = await N.ask({
      title: `编辑${role.title}规则`, text: role.text,
      description: '保存后用于后续请求；取消不会修改现有规则。',
    });
    if (value === null) return;
    role.text = String(value).trim();
    renderContext(); N.notify('角色规则已保存', 'jade'); N.save();
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
    ctx.undo = N.clone(state.contextData);
    ctx.canUndo = true;
    sum.text = String(value).trim();
    renderContext(); N.notify('历史摘要已保存', 'jade'); N.save();
  }
  async function delMessages() {
    const msgs = state.contextData.messages || [];
    const indices = [...ctx.selected].filter(i => msgs.some(m => m.index === i)).sort((a, b) => a - b);
    if (!indices.length) return;
    if (locked()) { N.notify('请先停止灵的当前输入再删除历史', 'cinnabar'); return; }
    const ok = await N.confirmEdit({
      title: `删除 ${indices.length} 条消息？`,
      description: '删除后可在会话继续前撤销最近一次修改；已执行的工具与后台工作不会回滚。',
      submitLabel: '删除消息',
    });
    if (!ok) return;
    ctx.undo = N.clone(state.contextData);
    ctx.canUndo = true;
    state.contextData.messages = msgs.filter(m => !indices.includes(m.index));
    ctx.selected.clear();
    renderContext(); N.notify('已删除所选消息', 'jade'); N.save();
  }
  function undo() {
    if (!ctx.undo) return;
    state.contextData = N.clone(ctx.undo);
    ctx.undo = null; ctx.canUndo = false;
    ctx.selected.clear();
    renderContext(); N.notify('已撤销最近一次历史修改', 'jade'); N.save();
  }
  function refresh() {
    const btn = $('[data-act="ctx-refresh"]');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<span class="spin">${icon('refresh', 'i xs')}</span>刷新中`;
    }
    setTimeout(() => {
      renderContext();
      N.notify('上下文已刷新', 'jade');
    }, 520);
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
        N.anim(oldPanel, [{ opacity: 1 }, { opacity: 0, transform: 'translateY(-4px)' }], { duration: 100, easing: 'ease-in' }).onfinish = swap;
      } else swap();
      return;
    }
    if (act === 'add-rule') return addRule();
    if (act === 'edit-rule') return editRule(+t.dataset.i);
    if (act === 'del-rule') return delRule(+t.dataset.i);
    if (act === 'edit-role') return editRole(t.dataset.id);
    if (act === 'edit-summary') return editSummary();
    if (act === 'ctx-all') {
      const msgs = state.contextData.messages || [];
      ctx.selected = t.checked ? new Set(msgs.map(m => m.index)) : new Set();
      syncSelection(); return;
    }
    if (act === 'ctx-pick') {
      const i = +t.dataset.i;
      if (t.checked) ctx.selected.add(i); else ctx.selected.delete(i);
      syncSelection(); return;
    }
    if (act === 'ctx-del') return delMessages();
    if (act === 'ctx-undo') return undo();
    if (act === 'ctx-refresh') return refresh();
    if (act === 'back-chat') return N.go('chat');
  });

  N.renderContext = renderContext;
  N.ctxLock = locked;
})(window.N);
