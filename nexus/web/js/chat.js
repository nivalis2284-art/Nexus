/* ==========================================================================
   主交流：记录、处理过程、请求、输入舱、用量
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, anim, SP, EASE_OUT, state } = N;
  const thread = $('#thread');
  const conv = $('#conversation');
  const input = $('#input');
  const composer = $('#composer');

  const MAX_SLOTS = 3;
  const activeWorks = () => state.works.filter(w => ['running', 'waiting_user', 'queued', 'pending', 'pausing'].includes(w.state));
  const byId = id => state.works.find(w => w.id === id);
  const nearBottom = () => conv.scrollHeight - conv.scrollTop - conv.clientHeight < 160;
  function scrollEnd(smooth) {
    followEnabled = true;
    stopFollow();
    conv.scrollTo({ top: conv.scrollHeight, behavior: smooth && !N.REDUCED ? 'smooth' : 'auto' });
    setJumpLatest(false);
  }
  conv.addEventListener('scroll', () => {
    if (nearBottom()) { followEnabled = true; setJumpLatest(false); }
  }, { passive: true });

  /* ---------------------------------------------------------------- 处理过程 */
  function groupItems(items) {
    const out = []; let phase = null;
    const flush = () => { if (phase) { out.push(phase); phase = null; } };
    for (const item of items) {
      if (item.role === 'user' || item.role === 'notice') { flush(); out.push({ type: 'msg', key: 'm:' + item.key, item }); continue; }
      if (item.role === 'thinking' || item.role === 'tool') {
        if (!phase) phase = { type: 'phase', key: 'p:' + item.key, steps: [], live: false, elapsed: null };
        if (item.live) phase.live = true;
        if (typeof item.elapsed === 'number') phase.elapsed = item.elapsed;
        phase.steps.push(item);
        continue;
      }
      if (item.role === 'assistant' && (item.text || item.live)) {
        if (phase && typeof item.elapsed === 'number') phase.elapsed = item.elapsed;
        flush(); out.push({ type: 'msg', key: 'm:' + item.key, item });
      }
    }
    flush();
    return out;
  }

  const head = (item, busy) => `<header class="ling-head"><span class="orb-mini${busy ? ' busy' : ''}"></span><b>灵</b>${item.src ? `<span class="src">· ${esc(item.src)}</span>` : ''}<time>${esc(item.at || '')}</time></header>`;
  const tools = item => `<footer class="msg-tools">
    <button data-act="copy" data-key="${esc(item.key)}" aria-label="复制">${icon('copy')}</button>
    ${item.role === 'user' && !item.live ? `<button data-act="resend" data-key="${esc(item.key)}" aria-label="编辑并重新发送">${icon('edit')}</button>` : ''}
    <button data-act="del" data-key="${esc(item.key)}" aria-label="删除这条消息">${icon('trash')}</button></footer>`;

  function fileLinks(files) {
    if (!files?.length) return '';
    return `<div class="msg-files">${files.map(f => f.kind === 'image'
      ? `<button class="file-link" data-act="open-image" data-url="${esc(f.url || '')}" data-name="${esc(f.name)}">${icon('image', 'i xs')}<span>${esc(f.name)}</span><small class="muted">${N.fmtSize(f.size)}</small></button>`
      : `<button class="file-link" data-act="open-attach" data-name="${esc(f.name)}">${icon('file', 'i xs')}<span>${esc(f.name)}</span><small class="muted">${N.fmtSize(f.size)}</small></button>`).join('')}</div>`;
  }
  function imageGrid(images) {
    if (!images?.length) return '';
    return `<div class="msg-images">${images.map(img => `<img src="${esc(img)}" alt="发送的图片" loading="lazy">`).join('')}</div>`;
  }

  /* ---------------------------------------------------------------- 滚动跟随 */
  let followRAF = 0;
  let followEnabled = true;
  function followScroll() {
    if (!followEnabled || followRAF || N.REDUCED) return;
    const tick = () => {
      if (!followEnabled) { followRAF = 0; return; }
      const max = conv.scrollHeight - conv.clientHeight;
      const d = max - conv.scrollTop;
      if (d > 0.5) conv.scrollTop += Math.min(d, Math.max(1.5, d * 0.3));
      followRAF = d > 1 ? requestAnimationFrame(tick) : 0;
    };
    followRAF = requestAnimationFrame(tick);
  }
  function stopFollow() { cancelAnimationFrame(followRAF); followRAF = 0; }

  /* 顶部请求区显隐时，交流区用 FLIP 滑移，避免整块跳变 */
  let glideBefore = null, glideScheduled = false;
  function beginLayoutChange() {
    if (glideBefore === null) glideBefore = conv.offsetTop;
  }
  function endLayoutChange() {
    if (glideScheduled) return;
    glideScheduled = true;
    requestAnimationFrame(() => {
      const from = glideBefore;
      glideBefore = null; glideScheduled = false;
      if (from === null || N.REDUCED) return;
      const dy = from - conv.offsetTop;
      if (Math.abs(dy) < 1) return;
      conv.getAnimations().forEach(a => a.cancel());
      anim(conv, [{ transform: `translateY(${dy}px)` }, { transform: 'none' }], { duration: N.DUR.move, easing: SP.soft.e });
    });
  }
  conv.addEventListener('wheel', () => { followEnabled = false; stopFollow(); }, { passive: true });
  conv.addEventListener('touchmove', () => { followEnabled = false; stopFollow(); }, { passive: true });

  const dispatchChip = w => `<button class="dispatch" data-open-work="${esc(w.id)}"><span class="ring${w.state === 'completed' ? ' done' : ''}"></span><span>${esc(w.title)}</span><span class="st">${esc(N.stateLabel(w))}</span>${icon('chev')}</button>`;
  const resultCard = w => `<button class="card lift result" data-open-work="${esc(w.id)}"><span class="ok">${icon('check')}</span><span><b>${esc(w.title)}</b><span class="rs">${esc(w.work_summary || '')}</span></span>${icon('chev', 'i go')}</button>`;

  function animateBlocks(node) {
    if (N.REDUCED) return;
    const blocks = $$('.md > *, .ling-body > .spine, .ling-body > .dispatch, .ling-body > .result', node);
    blocks.forEach((b, i) => anim(b, [{ opacity: 0, transform: 'translateY(5px)' }, { opacity: 1, transform: 'none' }],
      { duration: N.DUR.move, delay: 40 + i * 32, easing: EASE_OUT, fill: 'backwards' }));
  }

  function buildMsg(item) {
    const art = document.createElement('article');
    if (item.role === 'notice') {
      art.className = 'msg notice';
      art.textContent = item.text;
      return art;
    }
    if (item.role === 'user') {
      art.className = 'msg msg-user';
      const w = item.workId && byId(item.workId);
      art.innerHTML = `<div class="bubble"></div>
        ${fileLinks(item.attachments)}${imageGrid(item.images)}
        <div class="user-meta">${w ? `<span class="to-tag">${icon('arrow', 'i xs')}${esc(w.title)}</span>` : ''}<span>你 · ${esc(item.at || '')}</span>${tools(item)}</div>`;
      $('.bubble', art).textContent = item.text || '查看附件';
      return art;
    }
    const live = !!item.live;
    art.className = 'msg msg-ling';
    art.innerHTML = `${head(item, live)}<div class="ling-body"><div class="md${live ? ' streaming' : ''}"></div></div>${live ? '' : tools(item)}`;
    const body = $('.md', art);
    if (live) {
      body.textContent = item.text || '';
      const caret = document.createElement('i');
      caret.className = 'caret';
      body.appendChild(caret);
      body._len = (item.text || '').length;
    } else {
      body.innerHTML = N.md(item.text || '');
      N.decorate(body);
      const wrap = $('.ling-body', art);
      if (item.spine?.length) {
        const ol = document.createElement('ol');
        ol.className = 'spine';
        ol.innerHTML = item.spine.map(([t, d], i) => `<li><span class="n">${String(i + 1).padStart(2, '0')}</span><div><strong>${esc(t)}</strong><p>${esc(d)}</p></div></li>`).join('');
        wrap.appendChild(ol);
        if (!N.REDUCED) $$('li', ol).forEach((li, i) => anim(li, [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }],
          { duration: N.DUR.move, delay: 90 + i * 80, easing: EASE_OUT, fill: 'backwards' }));
      }
      const w = item.workId && byId(item.workId);
      if (w && item.card === 'result') wrap.insertAdjacentHTML('beforeend', resultCard(w));
      else if (w) wrap.insertAdjacentHTML('beforeend', dispatchChip(w));
    }
    if (item.elapsed) {
      const meta = document.createElement('div');
      meta.className = 'msg-meta';
      meta.textContent = '用时 ' + N.duration(item.elapsed);
      art.appendChild(meta);
    }
    if (item.compressed) {
      const tag = document.createElement('span');
      tag.className = 'compressed-tag';
      tag.innerHTML = icon('lock', 'i xs') + ' 已压缩 · 在上下文管理中修改摘要';
      art.appendChild(tag);
    }
    return art;
  }

  function buildThought(step) {
    const el = document.createElement('details');
    el.className = 'step';
    el.dataset.key = step.key;
    el.innerHTML = `<summary><span class="sicon">${icon('spark', 'i xxs')}</span><span class="stitle"></span><span class="spreview"></span></summary><div class="thought-text"></div>`;
    const first = String(step.thinking || '').split('\n').find(l => l.trim()) || '思考中…';
    $('.stitle', el).textContent = N.truncate(first.trim(), 120);
    $('.spreview', el).textContent = (step.thinking || '').length + ' 字';
    $('.thought-text', el).textContent = step.thinking || '';
    return el;
  }

  function toolNode(step) {
    const status = step.live ? 'running' : step.ok === false ? 'error' : 'done';
    const args = step.arguments || {};
    const output = String(step.text || '');
    const details = document.createElement('details');
    details.className = 'step tool-step ' + status;
    details.dataset.key = step.key || '';
    const label = (() => {
      if (args.command) return args.command;
      if (args.path && /read/.test(step.name || '')) return '读取 ' + args.path;
      if (args.path && /write/.test(step.name || '')) return '写入 ' + args.path;
      if (args.path) return String(args.path);
      for (const k of ['pattern', 'query', 'url', 'title', 'goal', 'name']) if (args[k]) return String(args[k]);
      return '';
    })();
    const preview = status === 'running' ? '正在执行…'
      : status === 'error' ? N.firstLine(step.error || output) || '执行失败'
        : N.firstLine(output) || '已完成';
    details.innerHTML = `<summary class="tool-summary ${status}">
        <span class="sicon">${status === 'error' ? '✕' : status === 'running' ? '◌' : '✓'}</span>
        <span class="sname">${esc(step.name || 'tool')}</span>
        ${label ? `<span class="sargs">${esc(N.truncate(label, 90))}</span>` : ''}
        <span class="spreview">${esc(N.truncate(preview, 80))}</span>
        <span class="sstate ${status === 'error' ? 'bad' : status === 'running' ? 'running' : 'good'}">${status === 'error' ? '失败' : status === 'running' ? '进行中' : '完成'}</span>
      </summary><div class="sbody"></div>`;
    const body = $('.sbody', details);
    if (step.error) {
      const err = document.createElement('div');
      err.className = 'tool-error';
      err.textContent = step.error;
      body.appendChild(err);
    }
    if (output) {
      const headEl = document.createElement('div');
      headEl.className = 'tool-output-head';
      headEl.innerHTML = `<span class="tool-output-label">输出</span><button type="button" class="tool-copy">复制</button>`;
      const pre = document.createElement('pre');
      pre.className = 'tool-output';
      pre.textContent = output;
      headEl.querySelector('.tool-copy').addEventListener('click', async e => {
        try { await navigator.clipboard.writeText(output); e.target.textContent = '已复制'; }
        catch { e.target.textContent = '复制失败'; }
        setTimeout(() => { e.target.textContent = '复制'; }, 1200);
      });
      body.append(headEl, pre);
    } else {
      const empty = document.createElement('div');
      empty.className = 'tool-empty';
      empty.textContent = status === 'running' ? '正在执行，尚无输出。' : '没有输出。';
      body.appendChild(empty);
    }
    if (Object.keys(args).length) {
      const box = document.createElement('details');
      box.className = 'tool-args';
      box.innerHTML = '<summary>参数</summary><pre></pre>';
      $('pre', box).textContent = JSON.stringify(args, null, 2);
      body.appendChild(box);
    }
    return details;
  }

  function phaseSummary(node, entry) {
    node.classList.toggle('live', entry.live);
    $('.plabel', node).textContent = entry.live ? '正在处理' : '处理过程';
    $('.pnote', node).textContent = entry.elapsed
      ? `（本轮用时 ${N.duration(entry.elapsed)}）`
      : `（${entry.steps.length} 项）`;
    let dots = $('.live-dots', node);
    if (entry.live && !dots) {
      $('summary', node).insertAdjacentHTML('beforeend', '<span class="goo-dots live-dots"><i></i><i></i><i></i></span>');
    } else if (!entry.live && dots) dots.remove();
  }

  function buildPhase(entry) {
    const details = document.createElement('details');
    details.className = 'phase' + (entry.live ? ' live' : '');
    details.dataset.key = entry.key;
    details.innerHTML = `<summary><span class="tw">${icon('chev', 'i xxs')}</span><span class="plabel"></span><span class="pnote"></span></summary><div class="pbody"></div>`;
    const body = $('.pbody', details);
    entry.steps.forEach(step => {
      const el = step.role === 'thinking' ? buildThought(step) : toolNode(step);
      el.dataset.key = step.key;
      el._sig = JSON.stringify(step);
      body.appendChild(el);
    });
    phaseSummary(details, entry);
    details.open = false;
    /* 展开处理过程时，里面的思考直接展开，不用再点一层。 */
    details.addEventListener('toggle', () => {
      if (!details.open) return;
      details.querySelectorAll('.step[data-key^="t:"]').forEach(step => { step.open = true; });
    });
    return details;
  }

  /* 流式时思考文本每来一段就变，重建节点会让高度来回跳，所以只改文字。 */
  function updateThought(el, step) {
    const title = $('.stitle', el);
    const preview = $('.spreview', el);
    const body = $('.thought-text', el);
    if (!title || !body) return false;
    const first = String(step.thinking || '').split('\n').find(line => line.trim()) || '思考中…';
    title.textContent = N.truncate(first.trim(), 120);
    if (preview) preview.textContent = (step.thinking || '').length + ' 字';
    body.textContent = step.thinking || '';
    return true;
  }

  function updatePhase(node, entry) {
    phaseSummary(node, entry);
    const body = $('.pbody', node);
    const existing = new Map([...body.children].map(n => [n.dataset.key, n]));
    entry.steps.forEach(step => {
      const sig = JSON.stringify(step);
      const old = existing.get(step.key);
      if (old && old._sig === sig) { existing.delete(step.key); return; }
      if (old && step.role === 'thinking' && updateThought(old, step)) {
        old._sig = sig;
        existing.delete(step.key);
        return;
      }
      const el = step.role === 'thinking' ? buildThought(step) : toolNode(step);
      el.dataset.key = step.key;
      el._sig = sig;
      if (old) {
        el.open = old.open;
        body.replaceChild(el, old);
      } else {
        body.appendChild(el);
        if (!N.REDUCED) anim(el, [{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: N.DUR.base, easing: EASE_OUT });
      }
      existing.delete(step.key);
    });
    existing.forEach(n => n.remove());
  }

  let renderedKeys = [];
  let renderedEntries = [];
  function syncUndoBar() {
    let bar = thread.querySelector('.history-undo');
    if (state.historyUndo && !bar) {
      bar = document.createElement('div');
      bar.className = 'history-undo';
      bar.innerHTML = '<span>已修改交流记录</span><button type="button" data-act="undo-history">撤销操作</button>';
      thread.prepend(bar);
      if (!N.REDUCED) anim(bar, [{ opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' }], { duration: N.DUR.move, easing: EASE_OUT });
    } else if (!state.historyUndo && bar) bar.remove();
  }

  function sameEntry(a, b) {
    if (!a || !b || a.type !== b.type) return false;
    if (a.type === 'phase') {
      const left = a.steps || [];
      const right = b.steps || [];
      return left.length === right.length && left.every((s, i) => s.key === right[i].key);
    }
    const x = a.item || {};
    const y = b.item || {};
    return x.role === y.role && (x.text || '').trim() === (y.text || '').trim();
  }

  /* 增量渲染：只在末尾追加新条目，已有阶段就地更新，避免整段重排 */
  function renderChat(force = false) {
    const empty = !state.items.length;
    $('#view-chat').classList.toggle('is-empty', empty);
    if (empty) {
      renderedKeys = [];
      renderedEntries = [];
      if (!thread.querySelector('.hero')) thread.innerHTML = heroHTML();
      return;
    }
    if (thread.querySelector('.hero')) thread.innerHTML = '';
    if (!thread.querySelector('.day')) {
      const day = document.createElement('div');
      day.className = 'day';
      const today = new Date();
      day.innerHTML = `<span>今天 · ${today.getMonth() + 1} 月 ${today.getDate()} 日</span>`;
      thread.prepend(day);
      if (!N.REDUCED) anim(day, [{ opacity: 0, transform: 'translateY(-4px)' }, { opacity: 1, transform: 'none' }], { duration: N.DUR.move, easing: EASE_OUT });
    }
    const entries = groupItems(state.items);
    const keys = entries.map(e => e.key);
    const extend = !force && renderedKeys.length <= keys.length && renderedKeys.every((k, i) => k === keys[i]);
    const wasEmpty = renderedKeys.length === 0;
    /* 乐观消息换成服务端记录时 key 会变，但内容没变，这时只换 key 不重画。 */
    const reuse = !force && !extend && entries.length <= renderedEntries.length
      && entries.length > 0
      && entries.every((e, i) => sameEntry(e, renderedEntries[i]));
    if (!extend && !reuse) {
      thread.replaceChildren();
      renderedKeys = [];
      renderedEntries = [];
    }
    if (reuse) {
      const nodes = [...thread.children].filter(n => n.dataset.key);
      entries.forEach((e, i) => {
        const node = nodes[i];
        if (node && node.dataset.key !== e.key) node.dataset.key = e.key;
      });
      nodes.slice(entries.length).forEach(n => n.remove());
      renderedKeys = keys;
      renderedEntries = entries;
      if (followEnabled) followScroll();
      return;
    }
    syncUndoBar();
    const start = renderedKeys.length;
    for (let i = start; i < entries.length; i++) {
      const entry = entries[i];
      const node = entry.type === 'phase' ? buildPhase(entry) : buildMsg(entry.item);
      node.dataset.key = entry.key;
      thread.appendChild(node);
      if (start > 0 && entry.type === 'msg' && entry.item.role === 'assistant') animateBlocks(node);
      if (!N.REDUCED) {
        const isUser = entry.type === 'msg' && entry.item.role === 'user';
        const delay = wasEmpty && entries.length > 1 ? Math.min(i, 8) * 55 : 0;
        anim(node, [{ opacity: 0, transform: `translateY(${isUser ? 14 : 9}px) scale(${isUser ? .96 : .995})` }, { opacity: 1, transform: 'none' }],
          { duration: isUser ? 380 : 300, delay, easing: isUser ? SP.bouncy.e : EASE_OUT });
      }
    }
    for (let i = 0; i < start; i++) {
      const entry = entries[i];
      if (entry.type !== 'phase') continue;
      const node = thread.querySelector(`[data-key="${entry.key}"]`);
      if (node) updatePhase(node, entry);
    }
    renderedKeys = keys;
    renderedEntries = entries;
    if (followEnabled) followScroll();
  }

  /* 流式文字：直接追加片段，不重排 Markdown */
  function patchLive(item, node) {
    if (!node) node = thread.querySelector(`[data-key="m:${item.key}"]`);
    if (!node) return;
    const md = $('.md', node);
    if (!md) return;
    let caret = $('.caret', md);
    if (!caret) {
      caret = document.createElement('i');
      caret.className = 'caret';
      md.appendChild(caret);
    }
    const prev = md._len || 0;
    const chunk = String(item.text || '').slice(prev);
    if (chunk) {
      const span = document.createElement('span');
      span.className = 'tok-stream';
      span.textContent = chunk;
      md.insertBefore(span, caret);
      md._len = prev + chunk.length;
    }
    if (followEnabled) followScroll();
    else setJumpLatest(true);
  }

  /* 收束：把纯文本换成完整 Markdown，交叉淡入 */
  function finalizeAssistant(item) {
    const old = thread.querySelector(`[data-key="m:${item.key}"]`);
    if (!old) { renderChat(true); return; }
    const fresh = buildMsg(item);
    fresh.dataset.key = 'm:' + item.key;
    const swap = () => {
      if (!old.isConnected) return;
      old.replaceWith(fresh);
      if (!N.REDUCED) anim(fresh, [{ opacity: 0 }, { opacity: 1 }], { duration: N.DUR.base, easing: EASE_OUT });
      animateBlocks(fresh);
    };
    if (N.REDUCED) { swap(); return; }
    anim(old, [{ opacity: 1 }, { opacity: 0 }], { duration: N.DUR.fast, easing: 'ease-in' }).onfinish = swap;
  }

  const heroHTML = () => `<div class="hero">
    <div class="orb-stage" id="orb-stage">
      <svg class="orb-hero" viewBox="-100 -100 200 200" aria-hidden="true">
        <defs><radialGradient id="og" gradientUnits="userSpaceOnUse" cx="-30" cy="-36" r="130"><stop offset="0" class="og0"/><stop offset=".2" class="og1"/><stop offset=".55" class="og2"/><stop offset="1" class="og3"/></radialGradient></defs>
        <g filter="url(#goo)" fill="url(#og)"><circle class="core" r="50"/><g class="sat s1"><circle cy="-56" r="16"/></g><g class="sat s2"><circle cy="-60" r="11"/></g><g class="sat s3"><circle cy="-52" r="13"/></g></g>
        <ellipse cx="-20" cy="-26" rx="15" ry="9" fill="#fff" opacity=".5" transform="rotate(-32 -20 -26)" style="filter:blur(3px)"/>
      </svg><i class="orb-shadow"></i>
    </div>
    <h1>有什么需要处理？</h1>
    <p>发送消息，或交代一项工作。</p>
    <div class="chips">
      <button class="chip" data-act="suggest" data-text="阅读当前项目，说明主要入口。">${icon('folder')}阅读当前项目</button>
      <button class="chip" data-act="overview">${icon('orbit')}查看后台工作进展</button>
      <button class="chip" data-view="context">${icon('layers')}管理上下文</button>
    </div></div>`;

  /* ---------------------------------------------------------------- 请求 */
  let reqSig = '';
  function renderRequests() {
    const box = $('#ling-requests');
    const works = state.works.filter(w => w.pending_interactions?.length && !state.requests.some(r => r.workId === w.id));
    const sig = JSON.stringify({ r: state.requests, w: works.map(w => [w.id, w.pending_interactions]) });
    if (sig === reqSig) return;
    reqSig = sig;
    const wasOpen = !box.hidden;
    beginLayoutChange();
    box.innerHTML = '';
    box.hidden = !state.requests.length && !works.length;
    $('#view-chat').classList.toggle('has-top', !box.hidden || !$('#ling-inputs').hidden);
    if (wasOpen !== !box.hidden) endLayoutChange();
    if (box.hidden) return;
    if (state.requests.length) {
      const sec = document.createElement('div');
      sec.className = 'req-section';
      sec.innerHTML = '<div class="req-kicker">灵在等你</div>';
      state.requests.forEach(r => sec.appendChild(requestCard(r)));
      box.appendChild(sec);
    }
    if (works.length) {
      const sec = document.createElement('div');
      sec.className = 'req-section';
      sec.innerHTML = '<div class="req-kicker">工作请求</div>';
      works.forEach(w => w.pending_interactions.forEach(i => sec.appendChild(workRequestCard(w, i))));
      box.appendChild(sec);
    }
    $$('.req-card', box).forEach((card, i) => { card.style.animationDelay = (i * 45 * N.motionScale()) + 'ms'; });
  }

  function requestCard(r) {
    const card = document.createElement('article');
    card.className = 'req-card';
    card.dataset.req = r.id;
    const approval = r.kind === 'approval';
    const p = r.payload || {};
    const unknown = r.status === 'unknown';
    card.innerHTML = `<div class="req-head"><span class="req-seal" aria-hidden="true">决</span><span class="req-owner">${esc(r.owner || '灵')}</span><span class="req-title">${approval ? '需要你确认' : '需要你回答'}</span>${unknown ? '<span class="req-state">结果待核对</span>' : ''}</div>`;
    if (approval) {
      card.insertAdjacentHTML('beforeend', `<div class="req-tool">${icon('tool', 'i xs')}${esc(p.tool || '请求确认')}</div>`);
      if (p.arguments && Object.keys(p.arguments).length) {
        const pre = document.createElement('pre');
        pre.className = 'req-args';
        pre.textContent = JSON.stringify(p.arguments, null, 2);
        card.appendChild(pre);
      }
      if (unknown) {
        card.insertAdjacentHTML('beforeend', `<p class="req-message">这次操作的结果尚未确认。系统不会自动重放或重复授权；请先核对实际影响。</p>
          <div class="req-actions"><button class="btn ghost req-reviewed" data-act="review-unknown" data-id="${esc(r.id)}">我已核对，关闭提示</button></div>`);
        return card;
      }
      card.insertAdjacentHTML('beforeend', `<div class="req-actions">
        <button class="btn req-deny" data-act="approve" data-id="${esc(r.id)}" data-allow="0">拒绝</button>
        <button class="btn req-allow" data-act="approve" data-id="${esc(r.id)}" data-allow="1">允许</button></div>`);
      return card;
    }
    const qs = p.questions || [];
    qs.forEach((q, qi) => {
      const block = document.createElement('div');
      block.innerHTML = `<p class="req-q">${esc(q.question || q.text || '需要你的回答')}</p>
        <div class="req-opts">${(q.options || []).map(o => `<button type="button" class="req-opt" data-act="pick" data-q="${qi}" data-v="${esc(o)}">${esc(o)}</button>`).join('')}</div>
        <input class="req-free" data-q="${qi}" placeholder="输入回答" aria-label="回答第 ${qi + 1} 个问题">`;
      card.appendChild(block);
    });
    const act = document.createElement('div');
    act.className = 'req-actions';
    act.innerHTML = `<button class="btn solid req-submit" data-act="answer" data-id="${esc(r.id)}">提交回答并继续</button>`;
    if (r.workId) act.insertAdjacentHTML('beforeend', `<button class="req-open" data-act="open-work" data-id="${esc(r.workId)}">查看工作</button>`);
    card.appendChild(act);
    card.addEventListener('click', e => {
      const pick = e.target.closest('[data-act="pick"]');
      if (!pick) return;
      const qi = pick.dataset.q;
      const field = card.querySelector(`.req-free[data-q="${qi}"]`);
      if (field) field.value = pick.dataset.v;
      card.querySelectorAll(`.req-opt[data-q="${qi}"]`).forEach(b => b.classList.toggle('picked', b === pick));
    });
    return card;
  }

  function workRequestCard(w, inter) {
    const card = document.createElement('article');
    card.className = 'req-card work';
    card.dataset.req = 'task:' + w.id + ':' + inter.id;
    const approval = inter.kind === 'approval';
    const p = inter.payload || {};
    card.innerHTML = `<div class="req-head"><span class="req-seal" aria-hidden="true">决</span><span class="req-owner">${esc(w.title)}</span><span class="req-title">${approval ? '需要你确认' : '需要你回答'}</span></div>`;
    if (approval) {
      card.insertAdjacentHTML('beforeend', `<div class="req-tool">${icon('tool', 'i xs')}${esc(p.tool || '请求确认')}</div>`);
      if (p.arguments && Object.keys(p.arguments).length) {
        const pre = document.createElement('pre');
        pre.className = 'req-args';
        pre.textContent = JSON.stringify(p.arguments, null, 2);
        card.appendChild(pre);
      }
      card.insertAdjacentHTML('beforeend', `<div class="req-actions">
        <button class="btn req-deny" data-act="work-approve" data-id="${esc(w.id)}" data-i="${esc(inter.id)}" data-allow="0">拒绝</button>
        <button class="btn req-allow" data-act="work-approve" data-id="${esc(w.id)}" data-i="${esc(inter.id)}" data-allow="1">允许</button>
        <button class="req-open" data-act="open-work" data-id="${esc(w.id)}">查看工作</button></div>`);
      return card;
    }
    const qs = p.questions || [];
    qs.forEach((q, qi) => {
      const block = document.createElement('div');
      block.innerHTML = `<p class="req-q">${esc(q.question || q.text || '需要你的回答')}</p>
        <div class="req-opts">${(q.options || []).map(o => `<button type="button" class="req-opt" data-act="pick" data-q="${qi}" data-v="${esc(o)}">${esc(o)}</button>`).join('')}</div>
        <input class="req-free" data-q="${qi}" placeholder="输入回答" aria-label="回答第 ${qi + 1} 个问题">`;
      card.appendChild(block);
    });
    card.insertAdjacentHTML('beforeend', `<div class="req-actions"><button class="btn solid" data-act="work-answer" data-id="${esc(w.id)}" data-i="${esc(inter.id)}">提交回答并继续</button><button class="req-open" data-act="open-work" data-id="${esc(w.id)}">查看工作</button></div>`);
    card.addEventListener('click', e => {
      const pick = e.target.closest('[data-act="pick"]');
      if (!pick) return;
      const qi = pick.dataset.q;
      const field = card.querySelector(`.req-free[data-q="${qi}"]`);
      if (field) field.value = pick.dataset.v;
      card.querySelectorAll(`.req-opt[data-q="${qi}"]`).forEach(b => b.classList.toggle('picked', b === pick));
    });
    return card;
  }

  function resolveCard(id) {
    const card = document.querySelector(`.req-card[data-req="${CSS.escape(id)}"]`);
    if (!card || N.REDUCED) { renderRequests(); return; }
    card.classList.add('resolving');
    setTimeout(() => { reqSig = ''; renderRequests(); }, 300);
  }

  async function answerRequest(id) {
    const r = state.requests.find(x => x.id === id);
    const card = document.querySelector(`.req-card[data-req="${CSS.escape(id)}"]`);
    if (!r || !card) return;
    const qs = r.payload?.questions || [];
    const answers = qs.map((_, i) => card.querySelector(`.req-free[data-q="${i}"]`)?.value.trim() || '');
    if (answers.some(a => !a)) { N.notify('请回答所有问题', 'cinnabar'); return; }
    resolveCard(id);
    try {
      await N.api.answer(r.token || r.id, answers.length === 1 ? answers[0] : answers);
      N.notify('已提交回答', 'jade');
      await N.sync.chatOnly();
      N.composeItems?.();
      renderChat(); N.renderRequests(); N.renderQueue(); N.updateStats(); N.refreshIsland?.();
    } catch (error) {
      N.notify(error?.message || '提交回答失败', 'cinnabar', 4200);
      reqSig = ''; renderRequests();
    }
  }

  async function approveRequest(id, allow) {
    const r = state.requests.find(x => x.id === id);
    if (!r) return;
    resolveCard(id);
    try {
      await N.api.approval(r.token || r.id, allow);
      N.notify(allow ? '已允许这次操作' : '已拒绝这次操作', allow ? 'jade' : 'cinnabar');
      await N.sync.chatOnly();
      N.composeItems?.();
      renderChat(); N.renderRequests(); N.updateStats(); N.refreshIsland?.();
    } catch (error) {
      N.notify(error?.message || '提交决定失败', 'cinnabar', 4200);
      reqSig = ''; renderRequests();
    }
  }

  async function reviewUnknown(id) {
    const r = state.requests.find(x => x.id === id);
    if (!r) return;
    resolveCard(id);
    try {
      await N.api.reviewUnknown(r.token || r.id);
      N.notify('已关闭提示，没有重放操作', 'jade');
      await N.sync.chatOnly();
      N.composeItems?.();
      renderChat(); N.renderRequests(); N.refreshIsland?.();
    } catch (error) {
      N.notify(error?.message || '操作失败', 'cinnabar');
    }
  }

  async function answerWorkRequest(wid, iid) {
    const w = byId(wid);
    const inter = w?.pending_interactions?.find(i => i.id === iid);
    if (!w || !inter) return;
    const card = document.querySelector(`.req-card[data-req="${CSS.escape('task:' + wid + ':' + iid)}"]`);
    const qs = inter.payload?.questions || [];
    const answers = qs.map((_, i) => card?.querySelector(`.req-free[data-q="${i}"]`)?.value.trim() || '');
    if (answers.some(a => !a)) { N.notify('请回答所有问题', 'cinnabar'); return; }
    resolveCard('task:' + wid + ':' + iid);
    try {
      await N.api.taskAnswer(wid, iid, {
        answer: answers.length === 1 ? answers[0] : answers,
        expected_version: w.control_version,
        resume: true,
      });
      N.notify('已提交回答，工作继续', 'jade');
      await N.sync.chatOnly();
      await N.sync.worksOnly();
      N.composeItems?.();
      renderChat(); N.renderRequests(); N.refreshIsland?.();
      if (state.view === 'work') N.renderWork?.();
    } catch (error) {
      N.notify(error?.message || '提交回答失败', 'cinnabar', 4200);
      reqSig = ''; renderRequests();
    }
  }

  async function approveWorkRequest(wid, iid, allow) {
    const w = byId(wid);
    const inter = w?.pending_interactions?.find(i => i.id === iid);
    if (!w || !inter) return;
    resolveCard('task:' + wid + ':' + iid);
    try {
      await N.api.taskAnswer(wid, iid, {
        answer: !!allow, expected_version: w.control_version, resume: !!allow,
      });
      N.notify(allow ? '已允许这次操作' : '已拒绝这次操作', allow ? 'jade' : 'cinnabar');
      await N.sync.chatOnly();
      await N.sync.worksOnly();
      N.composeItems?.();
      renderChat(); N.renderRequests(); N.refreshIsland?.();
      if (state.view === 'work') N.renderWork?.();
    } catch (error) {
      N.notify(error?.message || '提交决定失败', 'cinnabar', 4200);
      reqSig = ''; renderRequests();
    }
  }

  /* ---------------------------------------------------------------- 输入队列 */
  function renderQueue() {
    const box = $('#ling-inputs');
    const rows = state.queue.filter(q => ['pending', 'queued', 'running', 'held', 'interrupted', 'failed'].includes(q.status));
    const wasOpen = !box.hidden;
    beginLayoutChange();
    box.innerHTML = rows.length ? `<div class="queue-inner">${rows.slice(0, 3).map(q => {
      const label = {
        pending: '已接收，排队等待处理', queued: '已接收排队，当前输入完成后处理', running: '正在处理',
        held: '重启后暂停；需要你确认继续', interrupted: '上次处理已中断；检查已生效操作后发送新消息继续',
        failed: '处理失败' + (q.error ? '：' + q.error : ''),
      }[q.status] || q.status;
      return `<div class="qrow" data-status="${q.status}"><i class="qdot"></i><span class="qtext">${esc(q.text || '附件输入')}</span><span class="qstatus">${esc(label)}</span></div>`;
    }).join('')}${rows.length > 3 ? `<div class="qmore">另有 ${rows.length - 3} 条输入排队</div>` : ''}</div>` : '';
    $$('.qrow', box).forEach((row, i) => { row.style.animationDelay = (i * 35 * N.motionScale()) + 'ms'; });
    box.hidden = !rows.length;
    $('#view-chat').classList.toggle('has-top', !box.hidden || !$('#ling-requests').hidden);
    if (wasOpen !== !box.hidden) endLayoutChange();
  }

  /* ---------------------------------------------------------------- 统计与用量 */
  function updateStats() {
    state.turns = state.items.filter(i => i.role === 'user').length;
    const u = state.usage || {};
    $('#stat-turn').textContent = state.turns + ' 轮';
    $('#stat-tokens').textContent = (u.estimated ? '~' : '') + (u.total ? N.fmtNum(u.total) + ' tok' : '0 tok');
    const used = state.context ? (state.context.system || 0) + (state.context.tools || 0) + (state.context.messages || 0) : 0;
    const win = state.context?.window || 0;
    const pct = win ? Math.min(100, Math.round(used / win * 100)) : 0;
    /* 服务端只在请求过程中给出上下文分解；没有测量值时不要显示成 0%。 */
    const known = used > 0;
    $('#stat-context').textContent = win && known ? '上下文 ~' + pct + '%' : '上下文 —';
    $('#ctx-pct').textContent = known ? pct + '%' : '—';
    $('#ctx-gauge').setAttribute('aria-label', `上下文已用 ${pct}%，打开上下文管理`);
    $('#ctx-gauge .arc').style.setProperty('--p', pct);
    renderUsage();
  }

  function renderUsage() {
    const panel = $('#usage-panel');
    if (panel.hidden) return;
    const c = state.context || {};
    const used = (c.system || 0) + (c.tools || 0) + (c.messages || 0);
    const pct = c.window ? Math.min(100, Math.round(used / c.window * 100)) : 0;
    const u = state.usage || {};
    const cache = u.estimated ? '—' : (u.input + u.cached ? Math.round(u.cached / (u.input + u.cached) * 100) + '%' : '—');
    panel.innerHTML = `
      <div class="usage-head"><span>上下文已用 <b>${pct}%</b></span><span class="usage-total">${N.approx(used)} / ${N.approx(c.window)}</span></div>
      <div class="usage-bar"><span style="width:${pct}%"></span></div>
      <ul class="usage-list">
        <li><i class="dot d-system"></i>系统提示词<em>${N.approx(c.system)}</em></li>
        <li><i class="dot d-tools"></i>工具定义<em>${N.approx(c.tools)}</em></li>
        <li><i class="dot d-messages"></i>对话消息<em>${N.approx(c.messages)}</em></li>
      </ul>
      <div class="usage-head usage-q"><span>Token 用量</span><span class="usage-total"><b>${(u.estimated ? '~' : '') + N.fmtNum(u.total || 0)}</b> tok</span></div>
      <ul class="usage-list">
        <li>缓存命中<em>${cache}</em></li>
        <li>未缓存输入<em>${N.fmtNum(u.input || 0)} tok</em></li>
        <li>缓存读取<em>${N.fmtNum(u.cached || 0)} tok</em></li>
        <li>缓存写入<em>${N.fmtNum(u.created || 0)} tok</em></li>
        <li>输出<em>${N.fmtNum(u.output || 0)} tok</em></li>
      </ul>
      <p class="usage-note">~ 表示估算；累计用量与上下文占用分别统计。</p>
      ${u.complete === false ? '<p class="usage-note">部分消息未报告完整用量，以上为已记录部分。</p>' : ''}`;
    const anchor = panel._anchor || $('#stat-context');
    const r = anchor.getBoundingClientRect();
    const w = panel.offsetWidth, h = panel.offsetHeight;
    panel.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
    panel.style.top = Math.max(8, r.top - h - 10) + 'px';
  }

  function toggleUsage(anchor) {
    const panel = $('#usage-panel');
    if (panel.hidden) {
      panel._anchor = anchor;
      panel.hidden = false;
      renderUsage();
      if (!N.REDUCED) {
        N.anim(panel, [{ opacity: 0, transform: 'translateY(9px) scale(.93)' }, { opacity: 1, transform: 'none' }],
          { duration: N.SP.settle.d, easing: N.SP.settle.e });
      }
    } else {
      const done = () => { panel.hidden = true; panel.getAnimations().forEach(a => a.cancel()); };
      if (N.REDUCED) done();
      else N.anim(panel, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(6px) scale(.97)' }],
        { duration: N.DUR.fast, easing: 'ease-in', fill: 'forwards' }).onfinish = done;
    }
    $$('#stat-tokens,#stat-context').forEach(b => b.setAttribute('aria-expanded', String(!panel.hidden && b === anchor)));
  }

  function setJumpLatest(on) {
    const el = $('#jump-latest');
    if (!el || on === !el.hidden) return;
    if (on) {
      el.hidden = false;
      if (!N.REDUCED) {
        N.anim(el, [{ opacity: 0, transform: 'translateX(-50%) translateY(8px)' }, { opacity: 1, transform: 'translateX(-50%)' }],
          { duration: N.DUR.move, easing: N.SP.settle.e });
      }
      return;
    }
    const done = () => { el.hidden = true; el.getAnimations().forEach(a => a.cancel()); };
    if (N.REDUCED) done();
    else N.anim(el, [{ opacity: 1, transform: 'translateX(-50%)' }, { opacity: 0, transform: 'translateX(-50%) translateY(6px)' }],
      { duration: N.DUR.micro, easing: 'ease-in', fill: 'forwards' }).onfinish = done;
  }

  function setStopVisible(on) {
    const btn = $('#stop-reply');
    if (!btn || on === !btn.hidden) return;
    if (on) {
      btn.hidden = false;
      if (!N.REDUCED) {
        N.anim(btn, [{ opacity: 0, transform: 'scale(.82)' }, { opacity: 1, transform: 'none' }],
          { duration: N.DUR.move, easing: N.SP.settle.e });
      }
      return;
    }
    const done = () => { btn.hidden = true; btn.getAnimations().forEach(a => a.cancel()); };
    if (N.REDUCED) done();
    else N.anim(btn, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(.86)' }],
      { duration: N.DUR.fast, easing: 'ease-in', fill: 'forwards' }).onfinish = done;
  }

  /* ---------------------------------------------------------------- 输入舱 */
  function syncTargets(instant) {
    const box = $('#targets'), ink = $('.target-ink', box);
    const list = [{ id: null, label: '灵' }, ...activeWorks().map(w => ({ id: w.id, label: w.title, state: w.state }))];
    const sig = list.map(x => x.id + ':' + (x.state || '')).join('|');
    if (box._sig !== sig) {
      $$('.target', box).forEach(n => n.remove());
      box.insertAdjacentHTML('beforeend', list.map(t => `<button type="button" class="target${(t.id || null) === state.target ? ' active' : ''}" role="radio" aria-checked="${(t.id || null) === state.target}" data-target="${t.id || ''}">${t.id ? `<i class="wd ${t.state}"></i>${esc(t.label)}` : '<span class="orb-mini"></span>灵 · 主交流'}</button>`).join(''));
      box._sig = sig;
    }
    if (state.target && !byId(state.target)) state.target = null;
    $$('.target', box).forEach(b => {
      const on = (b.dataset.target || null) === state.target;
      b.classList.toggle('active', on);
      b.setAttribute('aria-checked', String(on));
    });
    N.moveInk(ink, $('.target.active', box), 'x', instant);
  }
  function setTarget(id, instant) {
    state.target = id || null;
    const w = state.target && byId(state.target);
    composer.classList.toggle('to-work', !!w);
    input.placeholder = w ? `补充给「${w.title}」…` : '发送消息，或交代一项工作…';
    syncTargets(instant);
    if (!instant) N.save();
  }
  function flashComposer() {
    composer.classList.remove('flash'); void composer.offsetWidth; composer.classList.add('flash');
    input.focus();
  }
  function updateSend() {
    const b = $('#send');
    const has = input.value.trim() || state.files.length;
    b.classList.toggle('busy', state.replying);
    b.classList.toggle('ready', !state.replying && !!has);
    b.classList.toggle('idle', !state.replying && !has);
    b.setAttribute('aria-label', state.replying ? '加入队列' : '发送');
    setStopVisible(!!state.replying);
  }
  function autosize() {
    const prev = input.offsetHeight;
    input.style.height = 'auto';
    const h = Math.min(input.scrollHeight, 200);
    input.style.height = prev + 'px'; void input.offsetHeight;
    input.style.transition = N.REDUCED ? 'none' : 'height .22s cubic-bezier(.22,1,.36,1)';
    input.style.height = h + 'px';
  }
  input.addEventListener('input', () => { autosize(); updateSend(); });
  input.addEventListener('keydown', e => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  composer.addEventListener('submit', e => { e.preventDefault(); send(); });
  composer.addEventListener('dragover', e => e.preventDefault());
  composer.addEventListener('drop', e => {
    if (e.dataTransfer?.files?.length) { e.preventDefault(); addFiles(e.dataTransfer.files); }
  });
  input.addEventListener('paste', e => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); addFiles(files); }
  });

  /* ---------------------------------------------------------------- 提交 */
  const blobToBase64 = blob => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || '').split(',')[1] || '');
    reader.onerror = () => reject(new Error('读取附件失败'));
    reader.readAsDataURL(blob);
  });

  function optimisticUser(text, target, files) {
    return {
      key: 'pending:' + N.uid('p'), role: 'user', text: text || '查看附件',
      at: N.nowHM(), workId: target || undefined,
      attachments: (files || []).map(f => ({ name: f.name, size: f.size, kind: f.kind, url: f.url })),
      images: (files || []).filter(f => f.kind === 'image').map(f => f.url),
    };
  }

  function appendUser(text, workId, files) {
    state.pending = [...(state.pending || []), optimisticUser(text, workId, files)];
    N.composeItems?.();
    renderChat(); scrollEnd(true); updateStats();
  }

  async function submit(text, files, target) {
    const pending = optimisticUser(text, target, files);
    state.pending = [...(state.pending || []), pending];
    N.composeItems?.();
    renderChat(); scrollEnd(true); updateStats();

    let accepted = false;
    try {
      const attachments = [];
      const images = [];
      for (const file of files) {
        const blob = file.file || (file.url ? await (await fetch(file.url)).blob() : null);
        if (!blob) continue;
        if (file.kind === 'image') {
          images.push({ mime: blob.type || 'image/png', data: await blobToBase64(blob) });
        } else {
          const saved = await N.api.upload(file.name, blob);
          if (saved?.id) attachments.push(saved.id);
        }
      }
      await N.api.input(text, { images, attachments, taskId: target || undefined });
      accepted = true;
      /* 快照可能被正在运行的回合拖慢；提交成功后即使刷新失败也不再报错。 */
      try { await N.sync.chatOnly(); } catch (_) {}
      N.composeItems?.();
      renderChat(); scrollEnd(true);
      N.renderRequests(); N.renderQueue(); N.updateStats(); N.syncTargets(true);
      N.refreshIsland?.(); N.updateSend?.();
    } catch (error) {
      if (accepted) { N.notify('已发送，但记录刷新稍后补上', 'jade', 2600); return; }
      state.pending = [];
      N.composeItems?.();
      renderChat();
      if (text && !input.value) { input.value = text; autosize(); }
      if (target) setTarget(target, true);
      N.notify(error?.message || '发送失败', 'cinnabar', 4200);
      updateSend();
    }
  }

  function send() {
    const text = input.value.trim();
    const files = [...state.files];
    if (!text && !files.length) return;
    sendRipple();
    anim(composer, [{ transform: 'translateY(-2px) scale(1)' }, { transform: 'translateY(-2px) scale(.994)' }, { transform: 'translateY(-2px) scale(1)' }],
      { duration: N.DUR.move, easing: EASE_OUT });
    if ($('#view-chat').classList.contains('is-empty')) leaveEmpty();
    const target = state.target;
    input.value = ''; state.files = []; renderFiles(); autosize(); updateSend();
    submit(text, files, target);
  }

  function respond(text, targetId) {
    const value = String(text || '').trim();
    if (!value) return;
    if ($('#view-chat').classList.contains('is-empty')) leaveEmpty();
    submit(value, [], targetId || null);
  }

  async function stopReply() {
    if (!state.replying) return;
    try {
      await N.api.stop();
      N.notify('已停止当前回应，后台工作保持独立', 'cinnabar');
    } catch (error) {
      N.notify(error?.message || '停止失败', 'cinnabar');
    }
    setTimeout(async () => {
      try {
        await N.sync.chatOnly();
        state.liveItems = [];
        N.composeItems?.();
        renderChat(); updateSend(); N.refreshIsland?.(); N.renderStatus?.();
      } catch (_) {}
    }, 260);
  }

  /* 回应由服务端事件驱动；这里保留空实现给旧调用点。 */
  async function stream() {}

  function refreshRefs(id) {
    const w = byId(id);
    if (!w) return;
    $$(`.dispatch[data-open-work="${id}"]`).forEach(card => {
      const st = $('.st', card);
      if (st) st.textContent = N.stateLabel(w);
      const ring = $('.ring', card);
      if (ring) ring.classList.toggle('done', w.state === 'completed');
    });
  }

  /* 工作完成由后台回报；这里只同步界面，不伪造状态。 */
  function finishWork(w) {
    if (!w) return;
    N.sync.worksOnly().then(() => { N.renderAll(); N.refreshRefs?.(w.id); }).catch(() => {});
  }

  async function pauseAll() {
    try {
      await N.api.pauseAll();
      N.notify('已请求挂起全部工作', 'jade');
      await N.sync.worksOnly();
      N.renderAll();
      if (state.view === 'work') N.renderWork?.();
    } catch (error) {
      N.notify(error?.message || '挂起失败', 'cinnabar');
    }
  }

  function sendRipple() {
    if (N.REDUCED) return;
    const b = $('#send');
    if (!b) return;
    const r = b.getBoundingClientRect();
    const i = document.createElement('i');
    i.className = 'send-ripple';
    i.style.left = (r.left + r.width / 2) + 'px';
    i.style.top = (r.top + r.height / 2) + 'px';
    document.body.appendChild(i);
    anim(i, [{ opacity: .75, transform: 'scale(.55)' }, { opacity: 0, transform: 'scale(2.2)' }],
      { duration: N.DUR.enter, easing: EASE_OUT }).onfinish = () => i.remove();
  }

  function send() {
    const text = input.value.trim();
    const files = [...state.files];
    if (!text && !files.length) return;
    sendRipple();
    anim(composer, [{ transform: 'translateY(-2px) scale(1)' }, { transform: 'translateY(-2px) scale(.994)' }, { transform: 'translateY(-2px) scale(1)' }],
      { duration: N.DUR.move, easing: EASE_OUT });
    if (state.replying) {
      state.queue.push({ id: N.uid('q'), text: text || '附件', status: 'queued', target: state.target });
      renderQueue();
      input.value = ''; state.files = []; renderFiles(); autosize(); updateSend();
      N.notify('已加入队列，当前回应结束后处理');
      N.save();
      return;
    }
    if ($('#view-chat').classList.contains('is-empty')) leaveEmpty();
    const target = state.target;
    appendUser(text, target, files);
    input.value = ''; state.files = []; renderFiles(); autosize(); updateSend();
    respond(text, target);
  }

  function leaveEmpty() {
    const view = $('#view-chat');
    const hero = $('.hero');
    if (hero && !N.REDUCED) {
      const r = hero.getBoundingClientRect();
      const ghost = hero.cloneNode(true);
      Object.assign(ghost.style, { position: 'fixed', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', margin: 0, zIndex: 2, pointerEvents: 'none' });
      $$('[id]', ghost).forEach(n => n.removeAttribute('id'));
      document.body.appendChild(ghost);
      anim(ghost, [{ opacity: 1, transform: 'none', filter: 'blur(0px)' }, { opacity: 0, transform: 'translateY(-26px) scale(.94)', filter: 'blur(8px)' }],
        { duration: N.DUR.enter, easing: EASE_OUT }).onfinish = () => ghost.remove();
    }
    view.classList.remove('is-empty');
    thread.innerHTML = '';
  }

  /* ---------------------------------------------------------------- 附件 */
  let seq = 0;
  const MAX_FILES = 8;
  function addFiles(list) {
    for (const file of [...list]) {
      if (state.files.length >= MAX_FILES) { N.notify('每次最多 8 个附件', 'cinnabar'); break; }
      const isImage = /^image\//.test(file.type);
      if (isImage && file.size > 5 * 1024 * 1024) { N.notify('图片超过 5 MiB：' + file.name, 'cinnabar'); continue; }
      if (!isImage && file.size > 25 * 1024 * 1024) { N.notify('文件超过 25 MiB：' + file.name, 'cinnabar'); continue; }
      state.files.push({
        id: 'f' + (++seq), name: file.name, size: file.size, kind: isImage ? 'image' : 'file',
        url: isImage ? URL.createObjectURL(file) : '', file,
      });
    }
    renderFiles(); updateSend(); N.save();
  }
  function renderFiles() {
    const box = $('#files');
    box.hidden = !state.files.length;
    box.innerHTML = state.files.map((f, i) => `<span class="file">
      ${f.kind === 'image' ? `<img class="thumb" src="${esc(f.url)}" alt="">` : icon('file', 'i xs')}
      <span>${esc(f.name)}</span><small class="fmeta">${N.fmtSize(f.size)}</small>
      <button type="button" data-act="rm-file" data-i="${i}" aria-label="移除附件 ${esc(f.name)}">${icon('close', 'i xs')}</button></span>`).join('');
  }
  $('#attach-btn').addEventListener('click', () => $('#file-input').click());
  $('#file-input').addEventListener('change', e => { addFiles(e.target.files); e.target.value = ''; });

  /* ---------------------------------------------------------------- 模型与强度 */
  const FAMILY = { anthropic_messages: 'Anthropic', openai_chat_completions: 'Chat', openai_responses: 'Responses' };
  function providerOf(id) { return state.providers.find(p => p.id === id) || state.providers[0]; }
  function currentModel() {
    const p = providerOf(state.current.provider);
    return (p?.models || []).find(m => m.id === state.current.model) || p?.models?.[0];
  }
  function renderModelLabel() {
    const m = currentModel();
    const p = providerOf(state.current.provider);
    $('#model-label').textContent = m ? m.id : '选择模型';
    $('#model-label').title = p ? `${p.name} · ${p.family}` : '';
  }
  function renderEffortLabel() {
    const lv = state.current.effort;
    $('#effort-label').textContent = lv ? '思考 · ' + (state.effort.labels[lv] || lv) : '思考 · 默认';
    $('#effort-bars').dataset.level = lv ? String(state.effort.levels.indexOf(lv) + 1) : '0';
  }
  $('#model-btn').addEventListener('click', e => {
    const items = [];
    for (const p of state.providers) {
      items.push({ group: `${p.name} · ${FAMILY[p.family] || p.family}` });
      for (const m of p.models || []) {
        items.push({
          label: m.id, on: p.id === state.current.provider && m.id === state.current.model,
          sub: m.context_window_tokens ? Math.round(m.context_window_tokens / 1000) + 'K' : '',
          onPick: async () => {
            try {
              await N.api.select({ provider: p.id, model: m.id, effort: state.current.effort || undefined });
              await N.sync.settingsOnly();
              renderModelLabel(); renderEffortLabel(); N.renderSettings?.(); N.notify(`已选择 ${m.id}`, 'jade');
            } catch (error) {
              N.notify(error?.message || '切换模型失败', 'cinnabar', 4000);
            }
          },
        });
      }
    }
    if (!items.length) items.push({ label: '还没有配置提供商', onPick: () => N.go('settings') });
    N.menu(e.currentTarget, items, { title: '主交流模型' });
  });
  $('#effort-btn').addEventListener('click', e => {
    const pick = async level => {
      try {
        await N.api.select({ provider: state.current.provider, model: state.current.model, effort: level || undefined });
        await N.sync.settingsOnly();
        renderEffortLabel(); N.notify(level ? '思考强度：' + (state.effort.labels[level] || level) : '使用提供商默认强度', 'jade');
      } catch (error) {
        N.notify(error?.message || '切换思考强度失败', 'cinnabar', 4000);
      }
    };
    const items = [{ group: '思考强度' }, { label: '提供商默认', sub: '不发字段', on: !state.current.effort,
      onPick: () => pick(null) }];
    for (const lv of state.effort.levels) {
      items.push({
        label: `${state.effort.labels[lv] || lv} · ${lv}`, on: state.current.effort === lv,
        onPick: () => pick(lv),
      });
    }
    N.menu(e.currentTarget, items, { title: '思考强度' });
  });
  $('#ctx-gauge').addEventListener('click', () => N.go('context'));
  $('#stat-tokens').addEventListener('click', e => toggleUsage(e.currentTarget));
  $('#stat-context').addEventListener('click', e => toggleUsage(e.currentTarget));
  document.addEventListener('pointerdown', e => {
    const panel = $('#usage-panel');
    if (!panel.hidden && !panel.contains(e.target) && !e.target.closest('#stat-tokens,#stat-context')) toggleUsage(null);
  });
  $('#stop-reply').addEventListener('click', stopReply);
  $('#jump-latest').addEventListener('click', () => scrollEnd(true));

  /* ---------------------------------------------------------------- 消息操作 */
  function itemByKey(key) { return state.items.find(i => i.key === key); }

  async function freshHistory() {
    const value = await N.api.ling();
    N.sync.chat(value);
    N.composeItems?.();
    renderChat();
    return value;
  }

  async function copyItem(key) {
    const item = itemByKey(key);
    try { await navigator.clipboard.writeText(item?.text || ''); N.notify('已复制', 'jade'); }
    catch { N.notify('浏览器未允许复制，可手动选中文字', 'cinnabar'); }
  }

  function removeNode(node) {
    if (!node || N.REDUCED) { renderChat(); return; }
    const h = node.offsetHeight;
    node.style.overflow = 'hidden';
    N.anim(node, [{ opacity: 1, height: h + 'px' }, { opacity: 0, height: '0px', transform: 'scale(.97)' }],
      { duration: N.DUR.move, easing: N.EASE_OUT }).onfinish = () => renderChat();
  }

  async function deleteItem(key) {
    const item = itemByKey(key);
    if (!item || typeof item.index !== 'number') { N.notify('这条记录还不能在这里删除', 'cinnabar'); return; }
    const node = thread.querySelector(`[data-key="m:${key}"]`);
    removeNode(node);
    try {
      await N.api.history({ operation: 'delete', version: state.historyVersion, indices: [item.index] });
      state.historyUndo = true;
      await freshHistory();
      N.notify('已删除这条消息，可撤销');
    } catch (error) {
      N.notify(error?.message || '删除失败', 'cinnabar', 4200);
      await freshHistory().catch(() => {});
    }
  }

  async function resend(key) {
    const item = itemByKey(key);
    if (!item) return;
    if (typeof item.index !== 'number') { N.notify('这条记录不能重新发送', 'cinnabar'); return; }
    const later = state.items.filter(i => i.role === 'user' && state.items.indexOf(i) > state.items.indexOf(item)).length;
    const value = await N.ask({
      title: '编辑并重新发送',
      text: item.text || '',
      description: `从这条消息重新继续，将替换后续 ${later} 条交流消息。保留本条附件与工作归属；已执行的工具和后台工作不会回滚。`,
      submitLabel: '重新发送',
    });
    if (value === null) return;
    try {
      await N.api.history({
        operation: 'resend', version: state.historyVersion,
        indices: [item.index], text: String(value).trim(), request_id: N.uid('req'),
      });
      state.historyUndo = true;
      await freshHistory();
      N.notify('已重新发送', 'jade');
    } catch (error) {
      N.notify(error?.message || '重新发送失败', 'cinnabar', 4200);
    }
  }

  async function undoHistory() {
    try {
      await N.api.history({ operation: 'undo', version: state.historyVersion });
      state.historyUndo = false;
      await freshHistory();
      N.notify('已撤销历史修改', 'jade');
    } catch (error) {
      N.notify(error?.message || '撤销失败', 'cinnabar', 4200);
    }
  }

  /* ---------------------------------------------------------------- 全局委托 */
  document.addEventListener('click', e => {
    const t = e.target.closest('[data-act]');
    if (!t) return;
    const act = t.dataset.act;
    const key = t.dataset.key;
    if (act === 'copy') return copyItem(key);
    if (act === 'del') return deleteItem(key);
    if (act === 'resend') return resend(key);
    if (act === 'undo-history') return undoHistory();
    if (act === 'rm-file') {
      const i = +t.dataset.i;
      const chip = t.closest('.file');
      const done = () => { state.files.splice(i, 1); renderFiles(); updateSend(); N.save(); };
      if (chip && !N.REDUCED) N.anim(chip, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(.6)' }], { duration: N.DUR.micro, easing: 'ease-in' }).onfinish = done;
      else done();
      return;
    }
    if (act === 'open-attach') return N.openFile(t.dataset.name);
    if (act === 'open-image') return N.openImage(t.dataset.url, t.dataset.name);
    if (act === 'suggest') {
      input.value = t.dataset.text || t.textContent.trim();
      autosize(); updateSend(); flashComposer(); return;
    }
    if (act === 'overview') return N.morphIsland?.('overview');
    if (act === 'answer') return answerRequest(t.dataset.id);
    if (act === 'approve') return approveRequest(t.dataset.id, t.dataset.allow === '1');
    if (act === 'review-unknown') return reviewUnknown(t.dataset.id);
    if (act === 'work-answer') return answerWorkRequest(t.dataset.id, t.dataset.i);
    if (act === 'work-approve') return approveWorkRequest(t.dataset.id, t.dataset.i, t.dataset.allow === '1');
    if (act === 'open-work') { N.morphIsland?.('detail', { id: t.dataset.id }); return; }
    if (act === 'stop') return stopReply();
  });
  document.addEventListener('click', e => {
    const tg = e.target.closest('[data-target]');
    if (!tg) return;
    setTarget(tg.dataset.target || null);
    input.focus();
  });

  /* ---------------------------------------------------------------- 导出 */
  N.renderChat = renderChat;
  N.renderRequests = renderRequests;
  N.renderQueue = renderQueue;
  N.updateStats = updateStats;
  N.syncTargets = syncTargets;
  N.setTarget = setTarget;
  N.updateSend = updateSend;
  N.flashComposer = flashComposer;
  N.send = send;
  N.stopReply = stopReply;
  N.stream = stream;
  N.respond = respond;
  N.appendUser = appendUser;
  N.pauseAll = pauseAll;
  N.finishWork = finishWork;
  N.refreshRefs = refreshRefs;
  N.renderModelLabel = renderModelLabel;
  N.renderEffortLabel = renderEffortLabel;
  N.currentModel = currentModel;
  N.providerOf = providerOf;
  N.scrollEnd = scrollEnd;
  N.toggleUsage = toggleUsage;
  N.filesRender = renderFiles;
  N.reqSig = () => { reqSig = ''; };
  N.patchLive = patchLive;
  N.finalizeAssistant = finalizeAssistant;
  N.FAMILY = FAMILY;
})(window.N);
