/* ==========================================================================
   工作：列表页与灵岛详情
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, state } = N;

  const GROUPS = [
    { id: 'active', title: '进行中', hint: '独立推进，不需要你盯着', states: ['running', 'queued', 'pending', 'pausing'] },
    { id: 'waiting', title: '等待你处理', hint: '回答后继续', states: ['waiting_user'] },
    { id: 'paused', title: '已挂起', hint: '需要时恢复', states: ['paused'] },
    { id: 'ended', title: '已结束', hint: '过程与结果保留', states: ['completed', 'cancelled'] },
  ];
  const pendingCount = w => Array.isArray(w.pending_interactions) ? w.pending_interactions.length : 0;

  function workCard(w) {
    const child = !!w.parent_id;
    return `<button class="work-item" data-s data-state="${w.state}" data-work-open="${esc(w.id)}">
      <span class="wico">${icon(child ? 'orbit' : w.state === 'completed' ? 'check' : w.state === 'waiting_user' ? 'alert' : 'work', 'i sm')}</span>
      <span>
        <span class="wtitle">${child ? '<span class="child">↳</span>' : ''}${esc(w.title)}<span class="pill state-${w.state}">${esc(N.stateLabel(w))}</span></span>
        <span class="wsub">${esc(N.progressText(w))}</span>
        <span class="wmeta">
          ${w.model ? `<span class="mono">${esc(w.model)}</span>` : ''}
          <span>${esc(w.finished || w.started || '')}</span>
          ${w.path ? `<span class="mono">${esc(N.shortPath(w.path))}</span>` : ''}
        </span>
      </span>
      <span class="wend">${pendingCount(w) ? `<span class="pending">${pendingCount(w)} 项待处理</span>` : ''}${icon('chev', 'i go sm')}</span>
    </button>`;
  }

  function renderWork() {
    const view = $('#view-work');
    const keep = view.scrollTop;
    const works = state.works;
    const sig = JSON.stringify(works);
    const entering = N.takeEnter?.('work');
    if (!entering && view._sig === sig && view.innerHTML) return;
    view._sig = sig;
    const counts = {
      active: works.filter(w => GROUPS[0].states.includes(w.state)).length,
      waiting: works.filter(w => w.state === 'waiting_user').length,
      paused: works.filter(w => w.state === 'paused').length,
    };
    const groups = GROUPS.map(g => {
      const list = works.filter(w => g.states.includes(w.state));
      if (!list.length) return '';
      return `<section class="work-group" data-s>
        <div class="work-group-h" data-s><h3>${g.title}</h3><span>${list.length} 项 · ${g.hint}</span></div>
        ${list.map(workCard).join('')}
      </section>`;
    }).join('');
    view.innerHTML = `<div class="page work-page">
      <header class="page-head" data-s>
        <div><h1>工作</h1><p>每项工作的过程、证据与结果都留在这里；补充和控制从主交流交代。</p></div>
        <div class="work-head-actions">
          <span class="pill">${counts.active} 项进行 · ${counts.waiting} 项待你处理</span>
          ${counts.active ? `<button class="btn ghost sm" data-act="pause-all">${icon('pause', 'i xs')}全部挂起</button>` : ''}
        </div>
      </header>
      ${groups || `<div class="card work-empty" data-s><span class="orb-mini"></span><b>还没有工作</b><span>在主交流里交代一件具体的事，它会独立推进。</span></div>`}
      <p class="foot-note" data-s>工作记录只读。开始、挂起、恢复或取消，以及补充要求，都通过主交流交代。</p>
    </div>`;
    view.scrollTop = keep;
    if (entering) N.stagger?.(view);
  }

  /* ---------------------------------------------------------------- 详情 */
  const VERIFY = { verified: '已验证', failed: '验证失败', unknown: '验证未知', '': '未验证' };
  const STEP = { completed: '完成', in_progress: '进行中', blocked: '受阻', pending: '未开始' };

  function miniItem(item) {
    if (item.role === 'user') {
      return `<article class="msg msg-user"><div class="bubble">${esc(item.text || '查看附件')}</div>
        <div class="user-meta"><span>你 · ${esc(item.at || '')}</span></div></article>`;
    }
    if (item.role === 'notice') return `<article class="msg notice">${esc(item.text)}</article>`;
    if (item.role === 'thinking') {
      return `<details class="step"><summary><span class="sicon">◇</span><span class="stitle">${esc(N.truncate(N.firstLine(item.thinking) || '思考', 100))}</span><span class="spreview">${(item.thinking || '').length} 字</span></summary><div class="thought-text">${esc(item.thinking || '')}</div></details>`;
    }
    if (item.role === 'tool') {
      const args = item.arguments || {};
      const label = args.path || args.pattern || args.command || args.task || '';
      return `<details class="step tool-step ${item.ok === false ? 'error' : 'done'}"><summary><span class="sicon">${item.ok === false ? '✕' : '✓'}</span><span class="sname">${esc(item.name || 'tool')}</span>${label ? `<span class="sargs">${esc(N.truncate(String(label), 70))}</span>` : ''}<span class="sstate ${item.ok === false ? 'bad' : 'good'}">${item.ok === false ? '失败' : '完成'}</span></summary><div class="sbody"><pre class="tool-output">${esc(item.text || '')}</pre></div></details>`;
    }
    return `<article class="msg msg-ling"><header class="ling-head"><span class="orb-mini"></span><b>灵</b><time>${esc(item.at || '')}</time></header>
      <div class="ling-body"><div class="md">${N.md(item.text || '')}</div></div>${item.elapsed ? `<div class="msg-meta">用时 ${item.elapsed} 秒</div>` : ''}</article>`;
  }

  function detailHTML(id) {
    const w = state.works.find(x => x.id === id);
    if (!w) return '';
    const parent = w.parent_id && state.works.find(x => x.id === w.parent_id);
    const kv = [];
    if (parent) kv.push(['归属', `父工作 · ${esc(parent.title)}`]);
    else if (w.parent_kind === 'session') kv.push(['归属', '主交流']);
    if (w.goal) kv.push(['目标', esc(w.goal)]);
    if (w.constraints) kv.push(['约束', esc(w.constraints)]);
    if (w.work_summary) kv.push([w.state === 'completed' ? '结果' : '进展', esc(w.work_summary)]);
    if (w.next_step && w.state !== 'completed') kv.push(['下一步', esc(w.next_step)]);
    if (w.evidence?.length) kv.push(['证据', w.evidence.map(e => esc(e.summary)).join('；')]);
    if (w.blocked) kv.push(['阻塞', esc(w.blocked)]);
    if (w.unfinished_operations?.length) kv.push(['未确认操作', w.unfinished_operations.map(o => esc(o.tool_name)).join('、')]);

    const steps = (w.steps || []).map(s => `<li class="${s.status === 'completed' ? 'done' : s.status === 'in_progress' ? 'active' : s.status === 'blocked' ? 'blocked' : ''}">
      <div class="st-top"><span>${esc(s.content)}</span></div>
      <span class="st-badge ${s.verification === 'verified' ? 'verified' : s.verification === 'failed' ? 'failed' : ''}">${STEP[s.status] || s.status} / ${VERIFY[s.verification || ''] || '未验证'}</span>
    </li>`).join('');

    const records = (w.records || []).length
      ? `<div class="isl-thread">${w.records.map(miniItem).join('')}</div>`
      : '<p class="plug-empty-line">还没有工作记录。</p>';

    const inter = (w.pending_interactions || []).length
      ? `<div class="isl-inter">${w.pending_interactions.map(i => {
        const p = i.payload || {};
        const text = p.questions ? p.questions.map(q => q.question || q.text).join(' / ') : p.tool ? '需要批准 ' + p.tool : '需要你的确认';
        return `<div class="isl-inter-row">${i.kind === 'approval' ? '审批' : '问题'} · ${esc(text)}</div>`;
      }).join('')}</div><p class="isl-detail-note" style="margin-top:8px">回答入口在主交流上方，回答后会作用到这项工作。</p>`
      : '';

    const children = (w.children || []).length
      ? `<div class="isl-sec"><div class="isl-sec-h">协作子代理</div><div class="isl-children">${w.children.map(c => `<button class="isl-child" data-isl="detail" data-id="${esc(c.task_id)}">${icon('orbit', 'i xs')}${esc(c.title)} · ${esc(N.STATE_LABEL[c.state] || c.state)}</button>`).join('')}</div></div>`
      : '';

    return `<div class="isl-panel wide">
      <div class="isl-head">
        <button class="isl-ibtn" data-isl="overview" aria-label="返回工作概览">${icon('back')}</button>
        <h2>工作详情</h2>
        <button class="isl-ibtn" data-isl="compact" aria-label="关闭">${icon('close')}</button>
      </div>
      <div class="isl-scroll">
        <div class="isl-badge-row"><span class="det-badge ${w.state === 'waiting_user' ? 'waiting' : w.state === 'completed' ? 'done' : ''}"><i class="d"></i>${esc(N.stateLabel(w))}</span>${w.parent_id ? '<span class="det-badge done"><i class="d"></i>子代理</span>' : ''}</div>
        <h3 class="isl-title" id="det-title">${esc(w.title)}</h3>
        <p class="isl-detail-note">${esc(N.statusDetail(w))}</p>
        <div class="isl-meta">
          <div><small>执行模型</small><span>${esc(w.model || '—')}</span></div>
          <div><small>开始</small><span>${esc(w.started || '—')}${w.finished ? ' – ' + esc(w.finished) : ''}</span></div>
          <div><small>范围</small><span class="mono">${esc(w.path || '—')}</span></div>
        </div>
        <div class="isl-sec"><div class="isl-sec-h">概要</div>
          <dl class="kv">${kv.map(([k, v]) => `<div class="kv-row"><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>
        </div>
        ${children}
        <div class="isl-sec"><div class="isl-sec-h">步骤与验证</div>
          ${steps ? `<ol class="isl-steps">${steps}</ol>` : '<p class="plug-empty-line">还没有步骤记录。</p>'}
        </div>
        <div class="isl-sec"><div class="isl-sec-h">工作记录</div>${records}</div>
        ${inter ? `<div class="isl-sec"><div class="isl-sec-h">待处理请求</div>${inter}</div>` : ''}
      </div>
      <div class="isl-foot2">
        <p>这里的记录只读。补充要求和状态控制通过主交流交代。</p>
        ${w.state !== 'completed' && w.state !== 'cancelled' ? `<button class="ibtn-solid" data-isl="supplement" data-id="${esc(w.id)}">在主交流中补充</button>` : ''}
      </div>
    </div>`;
  }

  async function openWork(id) {
    if (!state.works.some(w => w.id === id)) return;
    N.go('chat', { silent: true });
    N.morphIsland('detail', { id });
    try {
      await N.sync.taskDetail(id);
      N.renderIsland?.();
      N.refreshRefs?.(id);
    } catch (error) {
      N.notify(error?.message || '读取工作详情失败', 'cinnabar', 4000);
    }
  }

  document.addEventListener('click', e => {
    const t = e.target.closest('[data-work-open]');
    if (t) { e.preventDefault(); openWork(t.dataset.workOpen); }
  });

  N.renderWork = renderWork;
  N.workDetailHTML = detailHTML;
  N.openWork = openWork;
})(window.N);
