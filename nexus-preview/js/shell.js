/* ==========================================================================
   外壳：导航、灵岛、状态、主题、预览场景
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, anim, SP, state } = N;
  const island = $('#island'), islandInner = $('#island-inner');
  island.mode = 'compact'; island.data = null;
  let islandAnim = null;
  let toastTimer = null;
  const scrim = $('#island-scrim');
  function setScrim(on) {
    if (on) {
      scrim.hidden = false;
      requestAnimationFrame(() => scrim.classList.add('show'));
    } else {
      scrim.classList.remove('show');
      setTimeout(() => { if (!scrim.classList.contains('show')) scrim.hidden = true; }, 300);
    }
  }

  const activeWorks = () => state.works.filter(w => ['running', 'waiting_user', 'queued', 'pending', 'pausing'].includes(w.state));
  const pendingReq = () => state.requests.length + state.works.reduce((n, w) => n + (w.pending_interactions?.length || 0), 0);

  /* ---------------------------------------------------------------- 状态文本 */
  function statusText() {
    if (state.connectionError) return '连接暂时中断，稍后自动重试';
    if (state.replying) return '正在回应…';
    if (state.requests.length) return '灵在等你处理请求';
    if (state.held) return '有输入等待恢复';
    const a = activeWorks();
    if (!a.length) return '执行位空闲';
    const r = a.filter(w => w.state === 'running').length;
    const w = a.filter(w => w.state === 'waiting_user').length;
    return [r ? `${r} 项进行` : '', w ? `<em>${w} 项待你决定</em>` : ''].filter(Boolean).join(' · ');
  }
  function updateMood() {
    const waiting = state.requests.length || state.works.some(w => w.state === 'waiting_user');
    const active = activeWorks().length || state.replying;
    document.body.dataset.mood = waiting ? 'waiting' : active ? 'running' : 'calm';
  }

  /* ---------------------------------------------------------------- 灵岛 */
  function slotList() {
    const a = activeWorks().slice(0, 3);
    while (a.length < 3) a.push(null);
    return a;
  }
  function slotBtn(w) {
    return w
      ? `<button class="slot" data-state="${w.state}" data-isl="detail" data-id="${esc(w.id)}" aria-label="${esc(w.title)} · ${esc(N.stateLabel(w))}"><i class="core"></i></button>`
      : `<button class="slot" data-state="empty" data-isl="overview" aria-label="空闲执行位"><i class="core"></i></button>`;
  }
  function workRow(w) {
    if (!w) return `<div class="wrow empty"><span class="slot" data-state="empty"><i class="core"></i></span><span><b>空闲执行位</b><span class="sub">在主交流中交代新工作</span></span><span></span></div>`;
    const sub = w.state === 'waiting_user'
      ? esc(w.pending_interactions?.[0]?.payload?.questions?.[0]?.question || w.wait_reason || '等待你处理')
      : `${esc(N.progressText(w))}`;
    return `<button class="wrow ${w.state}" data-isl="detail" data-id="${esc(w.id)}"><span class="slot" data-state="${w.state}"><i class="core"></i></span><span><b>${esc(w.title)}</b><span class="sub">${sub}</span></span>${icon('chev', 'i go')}</button>`;
  }

  function compactHTML() {
    const queue = state.queue.filter(q => q.status !== 'failed').length;
    return `<div class="isl-compact">
      <button class="isl-main" data-isl="overview" aria-label="查看后台工作"><span class="orb-mini${state.replying ? ' busy' : ''}" id="isl-orb"></span><span class="isl-text"><b>灵</b><span class="isl-status" id="isl-status">${statusText()}</span></span></button>
      ${queue ? `<span class="isl-sep"></span><span class="isl-queue-count">${queue} 条输入排队</span>` : ''}
      <span class="isl-sep"></span><div class="isl-slots">${slotList().map(slotBtn).join('')}</div></div>`;
  }

  function overviewHTML() {
    const slots = slotList();
    const done = state.works.filter(w => w.state === 'completed').slice(0, 4);
    const held = state.held;
    const active = activeWorks();
    return `<div class="isl-panel">
      <div class="isl-head"><span class="orb-mini"></span><h2>后台工作<small>执行位 ${active.length}/3</small></h2><button class="isl-ibtn" data-isl="compact" aria-label="收起">${icon('close')}</button></div>
      <div class="slot-meter">${slots.map(w => `<i class="${w ? w.state : ''}"></i>`).join('')}</div>
      ${state.queue.length ? `<div class="isl-queue">${icon('arrow', 'i xxs')}<b>${state.queue.length} 条输入排队</b><span>当前回应结束后处理</span></div>` : ''}
      ${slots.map(workRow).join('')}
      ${done.length ? `<p class="isl-sub">已完成</p>${done.map(w => `<button class="drow" data-isl="detail" data-id="${esc(w.id)}">${icon('check', 'i sm')}<span>${esc(w.title)}</span><time>${esc(w.finished || '')}</time></button>`).join('')}` : ''}
      ${held ? '<div class="isl-held">' + icon('pause', 'i xs') + '有输入在重启后暂停，需要你确认继续</div>' : ''}
      <div class="isl-actions">
        ${held ? `<button class="ibtn-soft" data-act="resume-held">继续待恢复输入</button>` : ''}
        ${active.length ? `<button class="ibtn-soft" data-act="pause-all">全部工作挂起</button>` : ''}
        <button class="ibtn-soft" data-act="all-works">查看全部工作</button>
      </div>
      <p class="isl-foot">补充、挂起、恢复或取消，都在主交流里交代。</p></div>`;
  }

  function renderIsland(mode, data) {
    if (mode) { island.mode = mode; island.dataset.mode = mode; island.data = data || null; }
    if (island.mode === 'detail') {
      const html = N.workDetailHTML(island.data?.id);
      if (!html) { island.mode = 'compact'; island.dataset.mode = 'compact'; island.removeAttribute('aria-labelledby'); islandInner.innerHTML = compactHTML(); return; }
      islandInner.innerHTML = html;
      island.setAttribute('aria-labelledby', 'det-title');
      N.decorate(islandInner);
      return;
    }
    island.removeAttribute('aria-labelledby');
    if (island.mode === 'overview') { islandInner.innerHTML = overviewHTML(); return; }
    if (island.mode === 'toast') return;
    islandInner.innerHTML = compactHTML();
  }

  function morphIsland(mode, data = null) {
    if (mode !== 'toast') clearTimeout(toastTimer);
    const prev = island.mode;
    const from = island.getBoundingClientRect();
    const ghost = islandInner.cloneNode(true);
    ghost.removeAttribute('id');
    ghost.className = 'island-inner island-ghost';
    ghost.style.width = from.width + 'px';
    ghost.setAttribute('aria-hidden', 'true'); ghost.inert = true;
    $$('[id]', ghost).forEach(n => n.removeAttribute('id'));
    islandAnim?.cancel(); $$('.island-ghost', island).forEach(g => g.remove());
    island.style.width = island.style.height = '';
    islandInner.style.width = islandInner.style.marginLeft = '';
    island.mode = mode; island.data = data; island.dataset.mode = mode;
    islandInner.innerHTML = '';
    renderIsland();
    setScrim(island.mode === 'overview' || island.mode === 'detail');
    const to = island.getBoundingClientRect();
    if (N.REDUCED) return;
    island.appendChild(ghost);
    island.style.willChange = 'width, height';
    islandInner.style.willChange = 'opacity, transform, filter';
    islandInner.style.width = to.width + 'px';
    islandInner.style.marginLeft = `calc((100% - ${to.width}px) / 2)`;
    islandAnim = island.animate([{ width: from.width + 'px', height: from.height + 'px' }, { width: to.width + 'px', height: to.height + 'px' }],
      { duration: SP.island.d * N.motionScale(), easing: SP.island.e });
    ghost.animate([{ opacity: 1, filter: 'blur(0px)', transform: 'translateX(-50%)' }, { opacity: 0, filter: 'blur(6px)', transform: 'translateX(-50%) scale(.92)' }],
      { duration: 170 * N.motionScale(), easing: 'ease-in', fill: 'forwards' });
    islandInner.animate([{ opacity: 0, filter: 'blur(8px)', transform: 'scale(.93)' }, { opacity: 1, filter: 'blur(0px)', transform: 'none' }],
      { duration: 440 * N.motionScale(), delay: (prev === mode ? 0 : 70) * N.motionScale(), easing: N.EASE_OUT, fill: 'backwards' });
    if (mode === 'detail' || mode === 'overview') {
      $$('.det-scroll > *, .wrow, .drow, .isl-sec, .kv-row, .isl-steps li', islandInner).forEach((n, i) => {
        n.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
          { duration: 380, delay: 60 + Math.min(i, 10) * 18, easing: N.EASE_OUT, fill: 'backwards' });
      });
    }
    const clean = () => {
      islandInner.style.width = islandInner.style.marginLeft = '';
      islandInner.style.willChange = '';
      island.style.willChange = '';
      ghost.remove(); islandAnim = null;
    };
    islandAnim.onfinish = clean;
    islandAnim.oncancel = () => ghost.remove();
  }

  function updateCompact() {
    if (island.mode !== 'compact') return;
    const btns = $$('.isl-slots .slot', islandInner), list = slotList();
    if (btns.length !== 3) { renderIsland(); return; }
    const from = island.getBoundingClientRect().width;
    list.forEach((w, i) => {
      const b = btns[i];
      b.dataset.state = w ? w.state : 'empty';
      if (w) { b.dataset.isl = 'detail'; b.dataset.id = w.id; b.setAttribute('aria-label', `${w.title} · ${N.stateLabel(w)}`); }
      else { b.dataset.isl = 'overview'; delete b.dataset.id; b.setAttribute('aria-label', '空闲执行位'); }
    });
    const status = $('#isl-status');
    if (status) status.innerHTML = statusText();
    $('#isl-orb')?.classList.toggle('busy', state.replying);
    const to = island.getBoundingClientRect().width;
    if (from !== to && !N.REDUCED) island.animate([{ width: from + 'px' }, { width: to + 'px' }], { duration: SP.island.d, easing: SP.island.e });
  }

  let overviewSig = '', detailSig = '';
  function refreshIsland() {
    updateMood();
    if (island.mode === 'compact') { updateCompact(); return; }
    if (island.mode === 'overview') {
      const sig = JSON.stringify([state.works, state.queue, state.held]);
      if (sig !== overviewSig) { overviewSig = sig; morphIsland('overview'); }
      return;
    }
    if (island.mode === 'detail') {
      const w = state.works.find(x => x.id === island.data?.id);
      if (!w) { morphIsland('compact'); return; }
      const sig = JSON.stringify(w);
      if (sig !== detailSig) { detailSig = sig; morphIsland('detail', { id: w.id }); }
    }
  }

  function pulseSlot(id) {
    const b = $(`.isl-slots .slot[data-id="${id}"]`);
    if (!b) return;
    b.classList.remove('absorb'); void b.offsetWidth; b.classList.add('absorb');
  }

  /* ---------------------------------------------------------------- 通知 */
  function notify(text, tone = 'info', ms = 2600) {
    $('#live').textContent = text;
    if (island.mode === 'compact' || island.mode === 'toast') {
      clearTimeout(toastTimer);
      const ico = tone === 'cinnabar' ? '<b style="font-size:13px">决</b>' : tone === 'jade' ? icon('check', 'i sm') : '<span class="orb-mini" style="--s:16px"></span>';
      island.mode = 'toast'; island.dataset.mode = 'toast';
      setScrim(false);
      islandInner.innerHTML = `<div class="isl-toast" data-tone="${tone}"><span class="t-ico">${ico}</span><span>${esc(text)}</span></div>`;
      island.animate([{ height: island.getBoundingClientRect().height + 'px' }, { height: '46px' }], { duration: SP.island.d * N.motionScale(), easing: SP.island.e });
      toastTimer = setTimeout(() => { if (island.mode === 'toast') morphIsland('compact'); }, ms);
    } else {
      const t = $('#toast');
      t.textContent = text; t.classList.add('show');
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => t.classList.remove('show'), ms);
    }
  }

  /* ---------------------------------------------------------------- 导航 */
  const dock = $('#dock'), dockInk = $('#dock-ink');
  const dockAxis = () => getComputedStyle(dock).flexDirection === 'row' ? 'x' : 'y';
  const VIEWS = ['chat', 'work', 'context', 'skills', 'plugins', 'settings'];
  const enterFlags = {};
  function takeEnter(name) { const v = !!enterFlags[name]; enterFlags[name] = false; return v; }
  function markEnter(name) { enterFlags[name] = true; }
  function syncDock(instant) {
    N.moveInk(dockInk, $(`.dock-btn[data-view="${state.view}"]`), dockAxis(), instant);
    const waiting = state.works.filter(w => w.state === 'waiting_user').length + state.requests.length;
    const badge = $('#dock-work-badge');
    badge.hidden = !waiting;
    badge.textContent = '';
  }
  function renderView(name) {
    if (name === 'chat') { N.renderChat(); N.syncTargets(true); }
    if (name === 'work') N.renderWork();
    if (name === 'context') N.renderContext();
    if (name === 'skills') N.renderSkills();
    if (name === 'plugins') N.renderPlugins();
    if (name === 'settings') N.renderSettings();
  }
  let viewAnim = null;
  function go(name, opts = {}) {
    closeNav();
    if (!VIEWS.includes(name)) return;
    if (island.mode !== 'compact' && !opts.keepIsland) morphIsland('compact');
    if (name === state.view) {
      if (!opts.silent) renderView(name);
      syncDock(true);
      return;
    }
    const cur = $('#view-' + state.view), next = $('#view-' + name);
    state.view = name;
    $$('.dock-btn').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    syncDock();
    N.closeMenu?.();
    viewAnim?.finish();
    const swap = () => {
      $$('.view').forEach(v => { v.hidden = v !== next; });
      enterFlags[name] = true;
      renderView(name);
      if (name === 'chat') N.syncTargets(true);
      anim(next, [{ opacity: 0, transform: 'translateY(16px)', filter: 'blur(5px)' }, { opacity: 1, transform: 'none', filter: 'blur(0px)' }],
        { duration: 600, easing: SP.soft.e });
      N.stagger(next);
    };
    if (N.REDUCED || !cur) {
      swap();
      N.save();
      return;
    }
    // 旧页先退场，新页再逐组件登场；不做交叉淡入，避免两页重影
    cur.style.zIndex = '1';
    next.style.zIndex = '2';
    const out = anim(cur, [{ opacity: 1, transform: 'none', filter: 'blur(0px)' }, { opacity: 0, transform: 'translateY(-8px) scale(.992)', filter: 'blur(4px)' }],
      { duration: 160, easing: 'ease-in', fill: 'forwards' });
    out.onfinish = () => {
      cur.hidden = true;
      cur.style.zIndex = ''; next.style.zIndex = '';
      out.cancel();
      viewAnim = null;
      swap();
    };
    viewAnim = out;
    N.save();
  }

  function stagger(root, step = 34) {
    const items = $$('[data-s]', root);
    items.forEach((n, i) => {
      const head = n.classList.contains('page-head');
      const row = n.matches('.work-item,.ctx-owner-row,.ctx-role,.ctx-msg,.plug-item,.preq,.provider-row,.mcp-row,.qrow,.ctx-metrics div');
      const from = head ? 'translateY(-8px)' : row ? 'translateX(-8px)' : 'translateY(16px) scale(.99)';
      const dur = head ? 420 : row ? 300 : 400;
      anim(n, [{ opacity: 0, transform: from }, { opacity: 1, transform: 'none' }],
        { duration: dur, delay: 34 + i * step, easing: SP.soft.e, fill: 'backwards' });
    });
  }

  /* 只编排某个面板的直接子元素：标签页切换、插件详情切换用 */
  function cascade(root, step = 30, dur = 380) {
    if (!root || N.REDUCED) return;
    [...root.children].filter(n => n.nodeType === 1 && !n.hidden).forEach((n, i) => {
      anim(n, [{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }],
        { duration: dur, delay: 20 + i * step, easing: N.EASE_OUT, fill: 'backwards' });
    });
  }

  function openNav() { document.body.classList.add('nav-open'); $('#menu-btn').setAttribute('aria-expanded', 'true'); requestAnimationFrame(() => syncDock(true)); }
  function closeNav() { document.body.classList.remove('nav-open'); $('#menu-btn').setAttribute('aria-expanded', 'false'); }

  /* ---------------------------------------------------------------- 液滴 */
  function flyDroplet(fromEl, toEl) {
    return new Promise(res => {
      const a = fromEl.getBoundingClientRect(), b = toEl.getBoundingClientRect();
      const x0 = a.left + a.width / 2, y0 = a.top + a.height / 2, x1 = b.left + b.width / 2, y1 = b.top + b.height / 2;
      const cx = x0 + (x1 - x0) * 0.25, cy = Math.min(y0, y1) + (Math.max(y0, y1) - Math.min(y0, y1)) * 0.15 - 60;
      const d = document.createElement('i');
      d.className = 'droplet';
      document.body.append(d);
      const NSTEP = 24, kf = [];
      for (let i = 0; i <= NSTEP; i++) {
        const t = i / NSTEP, u = 1 - t;
        const x = u * u * x0 + 2 * u * t * cx + t * t * x1;
        const y = u * u * y0 + 2 * u * t * cy + t * t * y1;
        const dx = 2 * u * (cx - x0) + 2 * t * (x1 - cx);
        const dy = 2 * u * (cy - y0) + 2 * t * (y1 - cy);
        const ang = Math.atan2(dy, dx) * 180 / Math.PI;
        const stretch = 1 + Math.sin(t * Math.PI) * 0.9, sc = 1 - t * 0.35;
        kf.push({ offset: t, transform: `translate(${x}px,${y}px) rotate(${ang}deg) scale(${stretch * sc},${sc / Math.sqrt(stretch)})` });
      }
      const an = d.animate(kf, { duration: N.REDUCED ? 1 : 820 * N.motionScale(), easing: 'cubic-bezier(.55,0,.25,.09)' });
      an.onfinish = () => { d.remove(); res(); };
    });
  }

  /* ---------------------------------------------------------------- 主题与动效 */
  let revealing = false;
  function reveal(apply, anchor) {
    if (!document.startViewTransition || N.REDUCED) { apply(); return; }
    if (revealing) { apply(); return; }
    revealing = true;
    const r = anchor?.getBoundingClientRect();
    const x = r ? r.left + r.width / 2 : innerWidth / 2;
    const y = r ? r.top + r.height / 2 : 42;
    const rad = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    const vt = document.startViewTransition(apply);
    vt.finished.finally(() => { revealing = false; });
    vt.ready.then(() => {
      document.documentElement.animate(
        { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${rad}px at ${x}px ${y}px)`] },
        { duration: 660 * N.motionScale(), easing: N.EASE_OUT, pseudoElement: '::view-transition-new(root)' },
      );
    });
  }
  function applyTheme(next) {
    document.documentElement.dataset.theme = next;
    state.theme = next;
    N.save();
    N.syncSettingsUi?.();
  }
  function setTheme(next, anchor) {
    if (next === state.theme) return;
    reveal(() => applyTheme(next), anchor);
  }
  function toggleTheme(anchor) {
    setTheme(document.documentElement.dataset.theme === 'night' ? 'day' : 'night', anchor || $('#theme-btn'));
  }
  function applyPalette(id) {
    document.documentElement.dataset.palette = id;
    state.palette = id;
    N.save();
    N.syncSettingsUi?.();
  }
  function setPalette(id, anchor) {
    if (!N.THEMES?.some(t => t.id === id) || state.palette === id) return;
    reveal(() => applyPalette(id), anchor);
  }
  function setMotion(id, silent = false) {
    if (!N.MOTION_SCALES[id]) return;
    N.setMotionScale(id);
    document.documentElement.dataset.motion = id;
    document.documentElement.style.setProperty('--motion', N.MOTION_SCALES[id]);
    state.motion = id;
    if (!silent) {
      N.save();
      N.syncSettingsUi?.(false);
      N.notify('动效速度：' + ({ fast: '快', normal: '适中', slow: '慢' }[id] || id), 'jade');
      N.playMotionDemo?.();
    }
  }

  /* ---------------------------------------------------------------- 场景 */
  const SCENES = [
    ['conversation', '交流'], ['empty', '初始'], ['running', '运行'], ['decision', '待决定'],
    ['approval', '审批'], ['unknown', '待核对'], ['queue', '队列'], ['error', '连接异常'], ['done', '完成'],
  ];
  function resetData() {
    N.clearTimers();
    if (state.stream) state.stream.cancel = true;
    const fresh = N.seed();
    for (const k of ['items', 'works', 'requests', 'queue', 'replying', 'held', 'connectionError', 'target', 'files', 'historyUndo', 'undo', 'lastWork', 'usage', 'context', 'turns']) {
      state[k] = fresh[k];
    }
    state.stream = null;
  }
  function scenario(name) {
    resetData();
    state.scene = name;
    $('#scene-label').textContent = SCENES.find(s => s[0] === name)?.[1] || '交流';
    if (name === 'empty') {
      state.items = []; state.requests = []; state.queue = []; state.works = [];
    }
    if (name === 'running') {
      state.replying = true;
      state.stream = { cancel: false, static: true };
      state.items.push({ key: N.uid('t'), role: 'thinking', thinking: '正在核对主交流的输入舱状态：发送、停止、队列与附件。', live: true, at: N.nowHM() });
      state.items.push({ key: N.uid('a'), role: 'assistant', live: true, at: N.nowHM(), text: '我先核对输入舱的四种状态：空闲、可发送、正在回应、排队。\n\n停止只影响当前回应，' });
    }
    if (name === 'decision') {
      state.requests = [{
        id: 'r-detail', token: 'r-detail', kind: 'question', status: 'pending', owner: '灵', workId: 'w-detail',
        payload: { questions: [{ question: '工作详情默认在侧栏展开，还是使用全屏页面？', options: ['侧栏展开', '全屏阅读'] }] },
      }];
    }
    if (name === 'approval') {
      state.requests = [{
        id: 'r-approve', token: 'r-approve', kind: 'approval', status: 'pending', owner: '灵', workId: 'w-front',
        payload: { tool: 'write_file', arguments: { path: 'nexus/web/tasks.js', bytes: 4210, mode: 'overwrite' } },
      }];
    }
    if (name === 'unknown') {
      state.requests = [{
        id: 'r-unknown', token: 'r-unknown', kind: 'approval', status: 'unknown', owner: '灵', workId: 'w-front',
        payload: { tool: 'run_command', arguments: { command: 'python -m pytest tests -q', cwd: 'H:/文档/LingNexus/Nexus' } },
      }];
      state.works[0].state = 'paused';
      state.works[0].wait_reason = '上次操作结果待核对。';
    }
    if (name === 'queue') {
      state.queue = [
        { id: 'q1', text: '另外把插件页的字段也核对一遍。', status: 'queued' },
        { id: 'q2', text: '顺手看看上下文页还缺什么。', status: 'pending' },
        { id: 'q3', text: '这条先挂起，等我确认。', status: 'held' },
      ];
      state.held = true;
      state.works[1].state = 'paused';
      state.requests = [];
    }
    if (name === 'error') {
      state.connectionError = '连接暂时中断，稍后自动重试';
      state.queue = [{ id: 'q1', text: '核对工作详情的字段。', status: 'failed', error: '上游连接中断' }];
      state.items.push({ key: N.uid('n'), role: 'notice', text: '连接中断：这次输入没有送达。已执行的操作不会自动重放。', at: N.nowHM() });
    }
    if (name === 'done') {
      state.works[0].state = 'completed'; state.works[0].finished = '14:52';
      state.works[0].steps = state.works[0].steps.map(s => ({ ...s, status: 'completed', verification: 'verified' }));
      state.works[0].work_summary = '六组入口与交互状态已核对，结构文档已写入 docs/当前阶段/。';
      state.works = state.works.filter(w => w.id !== 'w-sub');
      state.items.push({ key: N.uid('r'), role: 'assistant', src: '后台回报', at: '14:52', elapsed: 14,
        text: '「前端结构梳理」已经完成。\n\n六组入口与交互状态已核对，结构文档已写入 `docs/当前阶段/`。需要我继续做插件页的字段对照吗？' });
    }
    if (name !== 'running') { state.replying = false; state.stream = null; }
    island.mode = 'compact'; island.dataset.mode = 'compact'; islandInner.innerHTML = '';
    renderIsland(); setScrim(false);
    N.renderChat(); N.reqSig(); N.renderRequests(); N.renderQueue(); N.updateStats(); N.syncTargets(true);
    syncDock(true); updateMood();
    if (state.view !== 'chat') go('chat', { silent: true });
    N.save();
    N.notify('已切换到「' + (SCENES.find(s => s[0] === name)?.[1] || name) + '」场景', 'jade', 1600);
  }

  /* ---------------------------------------------------------------- 事件 */
  dock.addEventListener('click', e => {
    const b = e.target.closest('.dock-btn');
    if (b) go(b.dataset.view);
  });
  document.addEventListener('click', e => {
    const v = e.target.closest('[data-view]');
    if (v && !v.closest('.dock')) go(v.dataset.view);
  });
  $('#menu-btn').addEventListener('click', () => document.body.classList.contains('nav-open') ? closeNav() : openNav());

  island.addEventListener('click', e => {
    const t = e.target.closest('[data-isl]');
    if (!t) return;
    const act = t.dataset.isl;
    if (act === 'overview') morphIsland('overview');
    else if (act === 'compact') morphIsland('compact');
    else if (act === 'detail') morphIsland('detail', { id: t.dataset.id });
    else if (act === 'supplement') {
      const id = t.dataset.id;
      morphIsland('compact');
      go('chat', { silent: true });
      N.setTarget(id);
      N.flashComposer();
    }
  });
  island.addEventListener('pointerenter', () => { if (island.mode === 'toast') clearTimeout(toastTimer); });
  island.addEventListener('pointerleave', () => {
    if (island.mode === 'toast') toastTimer = setTimeout(() => island.mode === 'toast' && morphIsland('compact'), 1200);
  });
  document.addEventListener('pointerdown', e => {
    if ((island.mode === 'overview' || island.mode === 'detail') && !island.contains(e.target) &&
        !e.target.closest('dialog,.menu,[data-keep-island]')) morphIsland('compact');
  });

  document.addEventListener('click', e => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    if (t.dataset.act === 'pause-all') N.pauseAll();
    if (t.dataset.act === 'all-works') go('work');
    if (t.dataset.act === 'resume-held') {
      state.held = false;
      state.queue.forEach(q => { if (q.status === 'held') q.status = 'queued'; });
      state.works.forEach(w => { if (w.state === 'paused' && w.wait_reason?.includes('重启')) { w.state = 'running'; w.wait_reason = ''; } });
      N.renderAll(); N.notify('已恢复待处理输入', 'jade'); N.save();
    }
  });

  $('#theme-btn').addEventListener('click', e => toggleTheme(e.currentTarget));
  $('#preview-more').addEventListener('click', e => {
    const items = SCENES.map(([k, l]) => ({ label: l, on: state.scene === k, onPick: () => scenario(k) }));
    items.push({ sep: true });
    items.push({ label: '切换昼夜', icon: 'moon', onPick: () => toggleTheme($('#preview-more')) });
    items.push({ label: '重置预览数据', icon: 'refresh', onPick: () => $('#reset-btn').click() });
    N.menu(e.currentTarget, items, { title: '预览' });
  });
  scrim.addEventListener('click', () => morphIsland('compact'));
  $('#scene-btn').addEventListener('click', e => {
    N.menu(e.currentTarget, SCENES.map(([k, l]) => ({ label: l, on: state.scene === k, onPick: () => scenario(k) })), { title: '预览场景' });
  });
  $('#reset-btn').addEventListener('click', async () => {
    const ok = await N.confirmEdit({
      title: '重置预览数据？',
      description: '会清除本地保存的消息、工作、设置与编辑内容，恢复到初始演示状态。',
      submitLabel: '重置预览',
    });
    if (!ok) return;
    N.clearSaved();
    location.reload();
  });

  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      if ($('#menu') && !$('#menu').hidden) { N.closeMenu(); return; }
      if (island.mode === 'detail' || island.mode === 'overview') { morphIsland('compact'); return; }
      closeNav();
      const usage = $('#usage-panel');
      if (usage && !usage.hidden) N.toggleUsage(null);
    }
    if ((e.metaKey || e.ctrlKey) && /^[1-6]$/.test(e.key)) {
      e.preventDefault();
      go(VIEWS[+e.key - 1]);
    }
    if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '') && !document.querySelector('dialog[open]')) {
      e.preventDefault();
      go('chat', { silent: true });
      $('#input').focus();
    }
  });
  let rz = 0;
  addEventListener('resize', () => {
    cancelAnimationFrame(rz);
    rz = requestAnimationFrame(() => {
      syncDock(true);
      N.syncTargets(true);
      const tabs = document.querySelector('.tabs');
      if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x', true);
      const perm = document.querySelector('.perm');
      if (perm) N.moveInk($('.tab-ink', perm), $('button.active', perm), 'x', true);
    });
  });

  /* ---------------------------------------------------------------- 汇总渲染 */
  function renderAll() {
    updateMood(); syncDock();
    refreshIsland();
    N.renderRequests(); N.renderQueue(); N.updateStats(); N.syncTargets();
    if (state.view === 'work') N.renderWork();
  }

  N.renderIsland = renderIsland;
  N.renderView = renderView;
  N.takeEnter = takeEnter;
  N.markEnter = markEnter;
  N.refreshIsland = refreshIsland;
  N.morphIsland = morphIsland;
  N.pulseSlot = pulseSlot;
  N.notify = notify;
  N.go = go;
  N.stagger = stagger;
  N.cascade = cascade;
  N.flyDroplet = flyDroplet;
  N.renderAll = renderAll;
  N.syncDock = syncDock;
  N.scenario = scenario;
  N.setTheme = setTheme; N.toggleTheme = toggleTheme; N.setPalette = setPalette; N.applyPalette = applyPalette; N.setMotion = setMotion;
  N.applyTheme = applyTheme;
  N.renderStatus = () => { if (island.mode === 'compact') { const s = $('#isl-status'); if (s) s.innerHTML = statusText(); } };
  N.SCENES = SCENES;
})(window.N);
