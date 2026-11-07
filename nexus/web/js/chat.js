/* ==========================================================================
   主交流：记录、处理过程、请求、输入舱、用量
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, esc, icon, anim, SP, EASE_ENTER: EASE_OUT, state } = N;
  const thread = $('#thread');
  const conv = $('#conversation');
  const input = $('#input');
  const composer = $('#composer');
  let sendLayout = null;

  const MAX_SLOTS = 3;
  const activeWorks = () => state.works.filter(w => ['running', 'waiting_user', 'queued', 'pending', 'pausing'].includes(w.state));
  const byId = id => state.works.find(w => w.id === id);
  function scrollEnd(smooth) {
    followEnabled = true;
    setJumpLatest(false);
    if (smooth && !N.REDUCED) { followScroll(); return; }
    stopFollow();
    conv.scrollTop = conv.scrollHeight;
  }

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

  const head = (item, busy) => `<header class="ling-head"><span class="orb-mini${busy ? ' busy' : ''}"></span><b>助手</b>${item.src ? `<span class="src">· ${esc(item.src)}</span>` : ''}<time>${esc(item.at || '')}</time></header>`;
  const tools = item => `<footer class="msg-tools">
    <button data-act="copy" data-key="${esc(item.key)}" aria-label="复制">${icon('copy')}</button>
    ${item.role === 'user' && !item.live ? `<button data-act="resend" data-key="${esc(item.key)}" aria-label="编辑并重新发送">${icon('edit')}</button>` : ''}
    <button data-act="del" data-key="${esc(item.key)}" aria-label="删除这条消息">${icon('trash')}</button></footer>`;

  function fileLinks(files) {
    if (!files?.length) return '';
    return `<div class="msg-files">${files.map(f => f.kind === 'image'
      ? `<button class="file-link" data-act="open-image" data-url="${esc(f.url || '')}" data-name="${esc(f.name)}">${icon('image', 'i xs')}<span>${esc(f.name)}</span><small class="muted">${N.fmtSize(f.size)}</small></button>`
      : `<a class="file-link" href="${esc(f.url || '')}" download="${esc(f.name)}">${icon('file', 'i xs')}<span>${esc(f.name)}</span><small class="muted">${N.fmtSize(f.size)}</small></a>`).join('')}</div>`;
  }
  function imageGrid(images) {
    if (!images?.length) return '';
    return `<div class="msg-images">${images.map(img => `<img src="${esc(img)}" alt="发送的图片" loading="lazy">`).join('')}</div>`;
  }

  /* ---------------------------------------------------------------- 滚动跟随 */
  let followRAF = 0;
  let followEnabled = true;
  let readingAnchor = null, manualDown = false, lastScrollTop = 0, touchY = 0;
  function captureReadingAnchor() {
    if (followEnabled || state.backgroundOnly || !conv.getBoundingClientRect) return;
    const top = conv.getBoundingClientRect().top + 76;
    const node = [...thread.children].find(n => n.dataset.key && n.getBoundingClientRect().bottom > top);
    if (node) readingAnchor = { node, offset: node.getBoundingClientRect().top - conv.getBoundingClientRect().top };
  }
  function followScroll() {
    if (state.backgroundOnly || state.view && state.view !== 'chat') return;
    if (N.REDUCED && followEnabled) { conv.scrollTop = conv.scrollHeight; return; }
    if (followRAF) return;
    let last = 0;
    const tick = now => {
      followRAF = 0;
      if (state.backgroundOnly) return;
      if (!followEnabled) {
        if (readingAnchor?.node.isConnected) {
          const offset = readingAnchor.node.getBoundingClientRect().top - conv.getBoundingClientRect().top;
          conv.scrollTop += offset - readingAnchor.offset;
        }
        setJumpLatest(conv.scrollHeight - conv.clientHeight - conv.scrollTop > 8);
        lastScrollTop = conv.scrollTop;
        return;
      }
      const max = conv.scrollHeight - conv.clientHeight;
      const d = max - conv.scrollTop;
      const dt = last ? Math.min(50, now - last) : 16;
      last = now;
      if (d > 0.5) conv.scrollTop += Math.min(d, Math.max(.5, d * (1 - Math.exp(-dt / 55))));
      lastScrollTop = conv.scrollTop;
      followRAF = d > 1 ? requestAnimationFrame(tick) : 0;
    };
    followRAF = requestAnimationFrame(tick);
  }
  function stopFollow() { cancelAnimationFrame(followRAF); followRAF = 0; }

  function beginLayoutChange() { captureReadingAnchor(); }
  function endLayoutChange() { followScroll(); }
  function pauseFollow() {
    followEnabled = false; manualDown = false; stopFollow(); captureReadingAnchor();
  }
  conv.addEventListener('wheel', e => { if (e.deltaY < 0) pauseFollow(); else manualDown = true; }, { passive: true });
  conv.addEventListener('touchstart', e => { touchY = e.touches[0].clientY; }, { passive: true });
  conv.addEventListener('touchmove', e => {
    const y = e.touches[0].clientY;
    if (y > touchY) pauseFollow(); else manualDown = true;
    touchY = y;
  }, { passive: true });
  conv.addEventListener('pointerdown', () => { manualDown = true; }, { passive: true });
  conv.addEventListener('pointermove', e => { if (e.buttons & 1) manualDown = true; }, { passive: true });
  conv.addEventListener('scroll', () => {
    if (manualDown && conv.scrollTop < lastScrollTop - .5) pauseFollow();
    if (!followEnabled) {
      captureReadingAnchor();
      if (manualDown && conv.scrollTop >= lastScrollTop && conv.scrollHeight - conv.clientHeight - conv.scrollTop <= 8) {
        followEnabled = true; readingAnchor = null; manualDown = false;
      }
    }
    lastScrollTop = conv.scrollTop;
    setJumpLatest(!followEnabled && conv.scrollHeight - conv.clientHeight - conv.scrollTop > 8);
  }, { passive: true });
  document.addEventListener('keydown', e => {
    if (state.view !== 'chat' || /INPUT|TEXTAREA|SELECT/.test(e.target?.tagName || '')) return;
    if (['ArrowUp', 'PageUp', 'Home'].includes(e.key)) pauseFollow();
    if (['ArrowDown', 'PageDown', 'End'].includes(e.key)) manualDown = true;
  });
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(endLayoutChange);
    [thread, conv, $('#composer-wrap'), $('#chat-top')].forEach(el => observer.observe(el));
  }

  const dispatchChip = w => `<button class="dispatch" data-open-work="${esc(w.id)}"><span class="ring${w.state === 'completed' ? ' done' : ''}"></span><span>${esc(w.title)}</span><span class="st">${esc(N.stateLabel(w))}</span>${icon('chev')}</button>`;
  const resultCard = w => `<button class="card lift result" data-open-work="${esc(w.id)}"><span class="ok">${icon('check')}</span><span><b>${esc(w.title)}</b><span class="rs">${esc(w.work_summary || '')}</span></span>${icon('chev', 'i go')}</button>`;

  function patchMarkdownNode(old, source, live) {
    if (source.nodeType === 3) {
      if (!source.textContent.trim() && !old?._textLeaf) {
        if (old?.nodeType === 3) { old.textContent = source.textContent; return old; }
        return source.cloneNode();
      }
      const node = old?._textLeaf ? old : document.createElement('span');
      node._textLeaf = true;
      const before = node.textContent, text = source.textContent;
      if (before === text) return node;
      if (live && !N.REDUCED && text.startsWith(before)) {
        const piece = document.createElement('span');
        piece.className = 'tok-stream'; piece.textContent = text.slice(before.length);
        node.appendChild(piece);
        setTimeout(() => {
          if (!piece.isConnected) return;
          piece.replaceWith(document.createTextNode(piece.textContent)); node.normalize();
        }, 420 * N.motionScale());
      } else node.textContent = text;
      return node;
    }
    if (source.nodeType !== 1) return source.cloneNode(true);
    const wrapper = source.tagName === 'PRE' && old?.classList?.contains('code-block') ? old : null;
    const target = wrapper ? $('pre', wrapper) : old;
    const node = target?.nodeType === 1 && target.tagName === source.tagName ? target : source.cloneNode(false);
    for (const a of [...node.attributes]) if (!source.hasAttribute(a.name) && a.name !== 'data-highlighted') node.removeAttribute(a.name);
    for (const a of [...source.attributes]) if (node.getAttribute(a.name) !== a.value) node.setAttribute(a.name, a.value);
    if (source.tagName === 'CODE' && source.parentElement?.tagName === 'PRE') {
      if (node.textContent !== source.textContent) {
        node.textContent = source.textContent; delete node.dataset.highlighted;
      }
    } else {
      [...source.childNodes].forEach((child, i) => {
        const current = node.childNodes[i];
        const next = patchMarkdownNode(current, child, live);
        if (next !== current) node.insertBefore(next, current || null);
        if (current && next !== current) current.remove();
      });
      while (node.childNodes.length > source.childNodes.length) node.lastChild.remove();
    }
    return wrapper || node;
  }

  function renderMarkdown(body, text, live) {
    const template = document.createElement('template');
    template.innerHTML = N.md(text);
    let fence = null;
    for (const line of text.split('\n')) {
      const hit = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
      if (!hit) continue;
      if (!fence) fence = hit[1];
      else if (hit[1][0] === fence[0] && hit[1].length >= fence.length && !hit[2].trim()) fence = null;
    }
    const blocks = [...template.content.children];
    blocks.forEach((source, i) => {
      const old = body.children[i], html = source.outerHTML;
      if (old?._sourceHTML === html) return;
      const node = patchMarkdownNode(old, source, live);
      node._sourceHTML = html;
      if (node !== old) { body.insertBefore(node, old || null); old?.remove(); }
      N.decorate(node, { highlight: !live || !fence || i < blocks.length - 1 });
      body.children[i]._sourceHTML = html;
    });
    while (body.children.length > blocks.length) body.lastElementChild.remove();
    if (!live) N.decorate(body);
  }

  const streams = new Map();
  let streamRAF = 0;
  const graphemes = typeof Intl !== 'undefined' && Intl.Segmenter ? new Intl.Segmenter(undefined, { granularity: 'grapheme' }) : null;
  function advanceStream(now) {
    streamRAF = 0;
    if (state.backgroundOnly) { streams.clear(); return; }
    let pending = false;
    beginLayoutChange();
    for (const [body, s] of streams) {
      if (!body.isConnected) { streams.delete(body); continue; }
      if (!s.final && now - s.last < 1000 / 30) { pending = true; continue; }
      const remaining = s.target.slice(s.shown.length);
      const dt = Math.min(50, s.last ? now - s.last : 34);
      let take = s.final || N.REDUCED ? remaining.length : Math.max(1, Math.ceil(remaining.length * dt / Math.max(dt, s.deadline - now)));
      if (graphemes && take < remaining.length) {
        for (const part of graphemes.segment(remaining)) {
          if (part.index + part.segment.length >= take) { take = part.index + part.segment.length; break; }
        }
      }
      s.shown += remaining.slice(0, take); s.last = now;
      renderMarkdown(body, s.shown, !s.final);
      if (s.final) {
        body.classList.remove('streaming'); $('.caret', body.parentElement)?.remove();
        streams.delete(body);
      } else if (s.shown !== s.target) pending = true;
      else streams.delete(body);
    }
    endLayoutChange();
    if (pending) streamRAF = requestAnimationFrame(advanceStream);
  }

  function queueText(body, text, final) {
    const s = body._stream || (body._stream = { shown: '', target: '', last: 0, final: false });
    if (!text.startsWith(s.shown)) s.shown = '';
    if (s.target === text && s.final === final && !streams.has(body) && s.shown === text) return;
    if (s.target !== text) s.deadline = performance.now() + 120;
    s.target = text; s.final = final;
    streams.set(body, s);
    if (N.REDUCED && body.isConnected) advanceStream(performance.now());
    else if (!streamRAF) streamRAF = requestAnimationFrame(advanceStream);
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
        <div class="msg-foot"><div class="user-meta"><span>${w ? `<span class="to-tag">${icon('arrow', 'i xs')}${esc(w.title)}</span>` : ''}你 · ${esc(item.at || '')}</span></div>${tools(item)}</div>`;
      $('.bubble', art).textContent = item.text || '查看附件';
      return art;
    }
    const live = !!item.live;
    art.className = 'msg msg-ling';
    art.innerHTML = `${head(item, live)}<div class="ling-body"><div class="md${live ? ' streaming' : ''}"></div>${live ? '<i class="caret" aria-hidden="true"></i>' : ''}</div><div class="msg-foot"><div class="msg-meta">${item.elapsed ? `用时 ${N.duration(item.elapsed)}` : ''}</div>${tools(item)}</div>`;
    art.classList.toggle('is-streaming', live);
    const body = $('.md', art);
    if (live) {
      queueText(body, item.text || '', false);
    } else {
      body.innerHTML = N.md(item.text || '');
      N.decorate(body);
      const wrap = $('.ling-body', art);
      if (item.spine?.length) {
        const ol = document.createElement('ol');
        ol.className = 'spine';
        ol.innerHTML = item.spine.map(([t, d], i) => `<li><span class="n">${String(i + 1).padStart(2, '0')}</span><div><strong>${esc(t)}</strong><p>${esc(d)}</p></div></li>`).join('');
        wrap.appendChild(ol);
      }
      const w = item.workId && byId(item.workId);
      if (w && item.card === 'result') wrap.insertAdjacentHTML('beforeend', resultCard(w));
      else if (w) wrap.insertAdjacentHTML('beforeend', dispatchChip(w));
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
        <span class="sicon">${status === 'error' ? icon('close', 'i xxs') : status === 'running' ? '<i class="spin-ring" aria-hidden="true"></i>' : icon('check', 'i xxs')}</span>
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
    const names = entry.steps
      .map(s => s.role === 'thinking' ? '思考' : (s.name || '工具'))
      .filter((v, i, a) => i === 0 || v !== a[i - 1])
      .slice(0, 3);
    const trail = $('.ptrail', node);
    if (trail) trail.textContent = names.length ? names.join(' → ') : '';
    $('.pnote', node).textContent = entry.elapsed
      ? `${N.duration(entry.elapsed)}`
      : `${entry.steps.length} 步`;
    let dots = $('.live-dots', node);
    if (entry.live && !dots) {
      $('summary', node).insertAdjacentHTML('beforeend', '<span class="goo-dots live-dots"><i></i><i></i><i></i></span>');
    } else if (!entry.live && dots) dots.remove();
  }

  function buildPhase(entry) {
    const details = document.createElement('details');
    details.className = 'phase' + (entry.live ? ' live' : '');
    details.dataset.key = entry.key;
    details.innerHTML = `<summary><span class="tw">${icon('chev', 'i xxs')}</span><span class="plabel"></span><span class="ptrail"></span><span class="pnote"></span></summary><div class="pbody"></div>`;
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
        const args = $('.tool-args', el), previousArgs = $('.tool-args', old);
        if (args && previousArgs) args.open = previousArgs.open;
        body.replaceChild(el, old);
      } else {
        body.appendChild(el);
        if (!N.REDUCED) N.enterItems([el], conv, { profile: 'content' });
      }
      existing.delete(step.key);
    });
    existing.forEach(n => n.remove());
  }

  function syncUndoBar() {
    let bar = thread.querySelector('.history-undo');
    if (state.historyUndo && !bar) {
      bar = document.createElement('div');
      bar.className = 'history-undo';
      bar.innerHTML = '<span>已修改对话记录</span><button type="button" data-act="undo-history">撤销操作</button>';
      thread.prepend(bar);
      if (!N.REDUCED) anim(bar, [{ opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' }], { duration: N.DUR.move, easing: EASE_OUT });
    } else if (!state.historyUndo && bar) bar.remove();
  }

  function updateMessage(node, item) {
    if (item.role === 'assistant') {
      const body = $('.md', node);
      if (item.live || body._stream) queueText(body, item.text || '', !item.live);
      else if (node._text !== item.text) renderMarkdown(body, item.text || '', false);
      node.classList.toggle('is-streaming', !!item.live);
      $('.orb-mini', node)?.classList.toggle('busy', !!item.live);
      const time = $('time', node);
      if (time) time.textContent = item.at || '';
      const meta = $('.msg-meta', node);
      if (meta) meta.textContent = item.elapsed ? '用时 ' + N.duration(item.elapsed) : '';
    } else if (item.role === 'user') {
      const fresh = buildMsg(item);
      const children = [...fresh.children];
      children.forEach((child, i) => {
        const old = node.children[i];
        if (old?.outerHTML === child.outerHTML) return;
        if (old?.className === 'bubble' && child.className === 'bubble' && old.textContent === child.textContent) return;
        if (old) old.replaceWith(child); else node.appendChild(child);
      });
      while (node.children.length > children.length) node.lastElementChild.remove();
    } else node.textContent = item.text;
    node._text = item.text;
    if (item.compressed && !$('.compressed-tag', node)) {
      node.insertAdjacentHTML('beforeend', `<span class="compressed-tag">${icon('lock', 'i xs')} 已压缩 · 在上下文管理中修改摘要</span>`);
    } else if (!item.compressed) $('.compressed-tag', node)?.remove();
  }

  function enterChat() {
    if (N.REDUCED || state.backgroundOnly) return;
    const rect = conv.getBoundingClientRect();
    const visible = [...thread.children].filter(n => {
      const r = n.getBoundingClientRect(); return r.bottom > rect.top + 76 && r.top < rect.bottom;
    });
    const hero = $('.hero', thread);
    const messages = hero ? $$('.hero-kicker, h1, .hero-sub, .chips', hero) : visible;
    const requests = $$('.req-card, .qrow', $('#view-chat'));
    requests.forEach(el => { el.style.animation = 'none'; });
    N.enterItems([...requests, ...messages, $('#composer-wrap')], $('#view-chat'));
  }

  /* 身份稳定的节点就地更新，快照与实时输出共享同一条记录。 */
  function renderChat(force = false) {
    if (state.backgroundOnly) return;
    const entering = N.takeEnter?.('chat');
    beginLayoutChange();
    const empty = !state.items.length;
    $('#view-chat').classList.toggle('is-empty', empty);
    if (empty) {
      if (!thread.querySelector('.hero')) thread.innerHTML = heroHTML();
      N.renderProjectButton?.();
      if (entering) requestAnimationFrame(enterChat);
      return;
    }
    if (thread.querySelector('.hero')) thread.innerHTML = '';
    if (!thread.querySelector('.day')) {
      const day = document.createElement('div');
      day.className = 'day';
      const today = new Date();
      day.innerHTML = `<span>今天 · ${today.getMonth() + 1} 月 ${today.getDate()} 日</span>`;
      thread.prepend(day);
    }
    const entries = groupItems(state.items);
    const existing = new Map([...thread.children].filter(n => n.dataset.key).map(n => [n.dataset.key, n]));
    syncUndoBar();
    let previous = thread.querySelector('.day');
    for (const entry of entries) {
      let node = existing.get(entry.key);
      const before = node?._entry;
      const unchanged = before && (entry.type === 'msg' ? before.item === entry.item
        : before.live === entry.live && before.elapsed === entry.elapsed && before.steps.length === entry.steps.length && before.steps.every((step, i) => step === entry.steps[i]));
      const sig = unchanged ? node._sig : JSON.stringify(entry);
      if (!node) {
        node = entry.type === 'phase' ? buildPhase(entry) : buildMsg(entry.item);
        node.dataset.key = entry.key;
        node._text = entry.item?.text;
        if (!N.REDUCED && !entering && !force && !sendLayout) anim(node,
          [{ opacity: 0, transform: 'translateY(14px)', filter: 'blur(6px)' }, { opacity: 1, transform: 'none', filter: 'blur(0px)' }],
          { duration: SP.soft.d, easing: SP.soft.e });
      } else if (node._sig !== sig) {
        if (entry.type === 'phase') updatePhase(node, entry);
        else updateMessage(node, entry.item);
      }
      node._sig = sig;
      node._entry = entry;
      if (previous?.nextElementSibling !== node) thread.insertBefore(node, previous?.nextElementSibling || null);
      previous = node;
      existing.delete(entry.key);
    }
    existing.forEach(node => node.remove());
    endLayoutChange();
    if (entering) requestAnimationFrame(enterChat);
  }

  /* 流式文字：直接追加片段，不重排 Markdown */
  function patchLive(item, node) {
    if (!node) node = thread.querySelector(`[data-key="m:${item.key}"]`);
    if (!node) return;
    const md = $('.md', node);
    if (!md) return;
    queueText(md, String(item.text || ''), false);
    endLayoutChange();
  }

  /* 收束：原地将流式文本升级为排版后的 Markdown，不经过 opacity:0 黑场闪烁 */
  function finalizeAssistant(item) {
    const old = thread.querySelector(`[data-key="m:${item.key}"]`);
    if (!old) { renderChat(true); return; }
    updateMessage(old, { ...item, live: false });
  }

  const heroHTML = () => `<div class="hero">
    <div class="orb-anchor" id="pivot-grip" role="img" aria-label="灵·枢"></div>
    <p class="hero-kicker">灵 · 枢</p>
    <h1>有什么需要处理？</h1>
    <p class="hero-sub">直接对话，也可以让多项工作在后台同时进行。</p>
    <div class="chips">
      <button class="chip" type="button" data-act="projects" aria-haspopup="dialog">${icon('folder')}<span data-project-name>${esc(state.project?.name || 'Work')}</span></button>
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
    beginLayoutChange();
    const groups = [
      ['ling', '待处理请求', state.requests.map(r => ({ id: r.id, value: r, owner: r.owner || '助手', build: () => requestCard(r) }))],
      ['work', '工作请求', works.flatMap(w => w.pending_interactions.map(inter => ({ id: 'task:' + w.id + ':' + inter.id, value: inter, owner: w.title, build: () => workRequestCard(w, inter) })))],
    ];
    let added = 0;
    for (const [key, title, requests] of groups) {
      let section = box.querySelector(`[data-request-group="${key}"]`);
      if (!requests.length) { section?.remove(); continue; }
      if (!section) {
        section = document.createElement('div');
        section.className = 'req-section'; section.dataset.requestGroup = key;
        section.innerHTML = `<div class="req-kicker">${title}</div>`;
        box.appendChild(section);
      }
      const existing = new Map($$('.req-card', section).map(card => [card.dataset.req, card]));
      requests.forEach((request, index) => {
        const id = String(request.id);
        const signature = JSON.stringify([request.value.kind, request.value.status, request.value.payload]);
        let card = existing.get(id);
        if (!card || card._requestSig !== signature) {
          const fresh = request.build();
          fresh._requestSig = signature;
          if (card) {
            // 同一组问题刷新状态时，保留尚未提交的回答与焦点。
            const sameQuestions = card._questions === JSON.stringify(request.value.payload?.questions);
            const focused = document.activeElement;
            if (sameQuestions) $$('.req-free', card).forEach(field => {
              const next = fresh.querySelector(`.req-free[data-q="${field.dataset.q}"]`);
              if (!next) return;
              next.value = field.value;
              $$('.req-opt', fresh).filter(button => button.dataset.q === field.dataset.q).forEach(button => button.classList.toggle('picked', button.dataset.v === field.value));
            });
            card.replaceWith(fresh);
            if (sameQuestions && focused?.matches('.req-free') && card.contains(focused)) {
              const field = fresh.querySelector(`.req-free[data-q="${focused.dataset.q}"]`);
              field?.focus({ preventScroll: true });
              field?.setSelectionRange(focused.selectionStart, focused.selectionEnd);
            }
            fresh.style.animation = 'none';
          } else fresh.style.animationDelay = (Math.min(added++ * N.MOTION.contentStagger, N.MOTION.contentWindow) * N.motionScale()) + 'ms';
          card = fresh;
          card._questions = JSON.stringify(request.value.payload?.questions);
        }
        const owner = $('.req-owner', card);
        if (owner) owner.textContent = request.owner;
        const slot = section.children[index + 1];
        if (slot !== card) section.insertBefore(card, slot || null);
        existing.delete(id);
      });
      existing.forEach(card => card.remove());
    }
    box.hidden = !state.requests.length && !works.length;
    $('#view-chat').classList.toggle('has-top', !box.hidden || !$('#ling-inputs').hidden);
    endLayoutChange();
  }

  function requestCard(r) {
    const card = document.createElement('article');
    card.className = 'req-card';
    card.dataset.req = r.id;
    const approval = r.kind === 'approval';
    const p = r.payload || {};
    const unknown = r.status === 'unknown';
    card.innerHTML = `<div class="req-head"><span class="orb-mini req-orb" aria-hidden="true"></span><span class="req-owner">${esc(r.owner || '助手')}</span><span class="req-title">${approval ? '需要你确认' : '需要你回答'}</span>${unknown ? '<span class="req-state">结果待核对</span>' : ''}</div>`;
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
    card.innerHTML = `<div class="req-head"><span class="orb-mini req-orb" aria-hidden="true"></span><span class="req-owner">${esc(w.title)}</span><span class="req-title">${approval ? '需要你确认' : '需要你回答'}</span></div>`;
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
    const bounds = card.getBoundingClientRect(), ghost = card.cloneNode(true);
    ghost.inert = true; ghost.setAttribute('aria-hidden', 'true');
    $$('[id]', ghost).forEach(el => el.removeAttribute('id'));
    Object.assign(ghost.style, { position: 'fixed', left: bounds.x + 'px', top: bounds.y + 'px', width: bounds.width + 'px', height: bounds.height + 'px', zIndex: 32, pointerEvents: 'none', animation: 'none' });
    document.body.appendChild(ghost);
    card.style.visibility = 'hidden';
    anim(ghost, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(-10px) scale(.96)', filter: 'blur(4px)' }],
      { duration: 200, easing: 'ease-in' }).onfinish = () => {
        ghost.remove(); card.style.visibility = ''; reqSig = ''; renderRequests();
      };
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
  let queueSig = '';
  function renderQueue() {
    const box = $('#ling-inputs');
    const rows = state.queue.filter(q => ['held', 'interrupted', 'failed'].includes(q.status));
    const signature = JSON.stringify(rows);
    if (signature === queueSig) return;
    queueSig = signature;
    beginLayoutChange();
    let list = $('.queue-inner', box);
    if (rows.length && !list) {
      list = document.createElement('div'); list.className = 'queue-inner'; box.appendChild(list);
    }
    if (list) {
      const existing = new Map($$('.qrow', list).map(row => [row.dataset.id, row]));
      rows.slice(0, 3).forEach((q, index) => {
        const id = String(q.id);
        let row = existing.get(id);
        if (!row) {
          row = document.createElement('div'); row.className = 'qrow'; row.dataset.id = id;
          row.innerHTML = `<i class="qdot"></i><span class="qtext"></span><span class="qstatus"></span><button class="qclose" type="button" data-act="dismiss-input" aria-label="关闭输入失败提示">${icon('close', 'i xs')}</button>`;
          row.style.animationDelay = (index * N.MOTION.contentStagger * N.motionScale()) + 'ms';
        }
        row.dataset.status = q.status;
        const close = $('.qclose', row);
        close.hidden = !['failed', 'interrupted'].includes(q.status);
        close.dataset.id = id;
        $('.qtext', row).textContent = q.text || '附件输入';
        $('.qstatus', row).textContent = {
          held: '重启后暂停；需要你确认继续', interrupted: '上次处理已中断；检查已生效操作后发送新消息继续',
          failed: '处理失败' + (q.error ? '：' + q.error : ''),
        }[q.status] || q.status;
        if (list.children[index] !== row) list.insertBefore(row, list.children[index] || null);
        existing.delete(id);
      });
      existing.forEach(row => row.remove());
      let more = $('.qmore', list);
      if (rows.length > 3) {
        if (!more) { more = document.createElement('div'); more.className = 'qmore'; list.appendChild(more); }
        more.textContent = `另有 ${rows.length - 3} 条输入提示`;
      } else more?.remove();
      if (!rows.length) list.remove();
    }
    box.hidden = !rows.length;
    $('#view-chat').classList.toggle('has-top', !box.hidden || !$('#ling-requests').hidden);
    endLayoutChange();
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
    const statCtx = $('#stat-context');
    if (statCtx) statCtx.textContent = win && known ? '上下文 ~' + pct + '%' : '上下文 —';
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
    const anchor = panel._anchor || $('#stat-tokens');
    const r = anchor.getBoundingClientRect();
    const w = panel.offsetWidth, h = panel.offsetHeight;
    panel.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
    panel.style.top = Math.max(8, r.top - h - 10) + 'px';
  }

  function toggleUsage(anchor) {
    const panel = $('#usage-panel');
    if (!anchor && (panel.hidden || panel._closing)) return;
    const opening = !!anchor && (panel.hidden || panel._closing);
    const from = panel.hidden ? { opacity: 0, transform: 'translateY(8px) scale(.94)' }
      : { opacity: getComputedStyle(panel).opacity, transform: getComputedStyle(panel).transform };
    panel._usageMotion?.cancel();
    panel._closing = !opening;
    if (opening) {
      panel._anchor = anchor;
      panel.hidden = false;
      renderUsage();
      if (!N.REDUCED) {
        panel._usageMotion = N.anim(panel, [from, { opacity: 1, transform: 'none' }],
          { duration: N.SP.bouncy.d, easing: N.SP.bouncy.e, fill: 'backwards' });
      }
    } else {
      const done = () => { panel.hidden = true; panel._closing = false; panel.getAnimations().forEach(a => a.cancel()); };
      if (N.REDUCED) done();
      else {
        const motion = N.anim(panel, [from, { opacity: 0, transform: 'translateY(6px) scale(.94)' }],
          { duration: 140, easing: 'ease-in', fill: 'forwards' });
        panel._usageMotion = motion;
        motion.onfinish = () => { if (panel._usageMotion === motion) done(); };
      }
    }
    $$('#stat-tokens').forEach(b => b.setAttribute('aria-expanded', String(opening && b === anchor)));
  }

  function setJumpLatest(on) {
    const el = $('#jump-latest');
    if (!el || el._wanted === on) return;
    el._wanted = on;
    if (el._motion) { el._motion.onfinish = null; el._motion.cancel(); }
    if (on) {
      el.hidden = false;
      if (!N.REDUCED) {
        el._motion = N.anim(el, [{ opacity: 0, transform: 'translateX(-50%) translateY(8px) scale(.94)' }, { opacity: 1, transform: 'translateX(-50%)' }],
          { duration: N.SP.bouncy.d, easing: N.SP.bouncy.e });
      }
      return;
    }
    const done = () => { if (!el._wanted) el.hidden = true; };
    if (N.REDUCED) done();
    else (el._motion = N.anim(el, [{ opacity: 1, transform: 'translateX(-50%)' }, { opacity: 0, transform: 'translateX(-50%) translateY(6px) scale(.94)' }],
      { duration: 140, easing: 'ease-in', fill: 'forwards' })).onfinish = done;
  }

  /* ---------------------------------------------------------------- 输入舱 */
  function syncTargets(instant) {
    const box = $('#targets'), ink = $('.target-ink', box);
    const list = [{ id: null, label: '对话' }, ...activeWorks().map(w => ({ id: w.id, label: w.title, state: w.state }))];
    const sig = list.map(x => x.id + ':' + (x.state || '')).join('|');
    if (box._sig !== sig) {
      $$('.target', box).forEach(n => n.remove());
      box.insertAdjacentHTML('beforeend', list.map(t => `<button type="button" class="target${(t.id || null) === state.target ? ' active' : ''}" role="radio" aria-checked="${(t.id || null) === state.target}" data-target="${t.id || ''}">${t.id ? `<i class="wd ${t.state}"></i>${esc(t.label)}` : '<span class="orb-mini"></span>对话'}</button>`).join(''));
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
    const has = !!(input.value.trim() || state.files.length);
    const stopping = state.replying && !has;
    b.classList.toggle('busy', stopping);
    b.classList.toggle('running', !!state.replying);
    b.classList.toggle('ready', has);
    b.classList.toggle('idle', !state.replying && !has);
    const label = stopping ? '停止当前回复' : state.replying ? '加入队列' : '发送';
    b.setAttribute('aria-label', label);
    b.title = label;
  }
  let inputMeasure = null;
  function autosize() {
    if (!inputMeasure) {
      inputMeasure = input.cloneNode();
      inputMeasure.removeAttribute('id'); inputMeasure.removeAttribute('name');
      inputMeasure.setAttribute('aria-hidden', 'true'); inputMeasure.tabIndex = -1;
      inputMeasure.placeholder = ''; inputMeasure.readOnly = true;
      inputMeasure.style.cssText = 'position:fixed;left:-9999px;top:0;visibility:hidden;height:0;min-height:0;max-height:none;overflow:hidden;pointer-events:none';
      document.body.appendChild(inputMeasure);
    }
    const style = getComputedStyle(input);
    for (const property of ['font', 'line-height', 'letter-spacing', 'padding', 'border-width', 'box-sizing', 'text-indent', 'word-spacing', 'overflow-wrap', 'word-break', 'tab-size']) {
      inputMeasure.style.setProperty(property, style.getPropertyValue(property));
    }
    inputMeasure.style.width = input.getBoundingClientRect().width + 'px';
    inputMeasure.value = input.value;
    // 在布局外测量，避免 height:auto 每打一个字都撑缩会话区并截断滚动位置。
    const h = Math.min(200, Math.ceil(Math.max(parseFloat(style.minHeight) || 0, inputMeasure.scrollHeight) / 4) * 4);
    if (input.style.height !== h + 'px') {
      beginLayoutChange(); input.style.height = h + 'px'; endLayoutChange();
    }
  }
  input.addEventListener('input', () => { $('#composer-wrap')?._enterMotion?.finish(); autosize(); updateSend(); });
  input.addEventListener('keydown', e => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
  });
  composer.addEventListener('submit', e => {
    e.preventDefault();
    if (state.replying && !input.value.trim() && !state.files.length) stopReply();
    else send();
  });
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

  function optimisticUser(text, target, files, requestId = N.uid('req')) {
    return {
      key: 'u:' + requestId, requestId, role: 'user', text: text || '查看附件',
      afterKey: state.items.at(-1)?.key || null, duringReply: !!state.replying,
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

  // shortcut: retry identity lasts for this page; persist drafts when reload recovery is required.
  let retryDraft = null;
  async function submit(text, files, target) {
    const draft = retryDraft && retryDraft.text === text && retryDraft.target === target
      && retryDraft.files.length === files.length && files.every((file, i) => file === retryDraft.files[i])
      ? retryDraft : { text, files, target, requestId: N.uid('req'), attachments: [], images: [], prepared: 0 };
    const pending = optimisticUser(text, target, files, draft.requestId);
    state.pending = [...(state.pending || []), pending];
    N.composeItems?.();
    renderChat(); scrollEnd(!sendLayout); updateStats();
    finishSendLayout(pending.key);

    let accepted = false;
    try {
      const { attachments, images } = draft;
      for (; draft.prepared < files.length; draft.prepared++) {
        const file = files[draft.prepared];
        const blob = file.file || (file.url ? await (await fetch(file.url)).blob() : null);
        if (!blob) continue;
        if (file.kind === 'image') {
          images.push({ mime: blob.type || 'image/png', data: await blobToBase64(blob) });
        } else {
          const saved = await N.api.upload(file.name, blob);
          if (saved?.id) attachments.push(saved.id);
        }
      }
      await N.api.input(text, { images, attachments, taskId: target || undefined, requestId: draft.requestId });
      accepted = true;
      if (retryDraft === draft) retryDraft = null;
      /* 快照可能被正在运行的回合拖慢；提交成功后即使刷新失败也不再报错。 */
      try { await N.sync.chatOnly(); } catch (_) {}
      N.composeItems?.();
      renderChat();
      N.renderRequests(); N.renderQueue(); N.updateStats(); N.syncTargets(true);
      N.refreshIsland?.(); N.updateSend?.();
    } catch (error) {
      if (accepted) { N.notify('消息已发送，记录暂未刷新。', 'jade', 2600); return; }
      retryDraft = draft;
      state.pending = state.pending.filter(item => item !== pending);
      state.files = [...files.filter(file => !state.files.includes(file)), ...state.files];
      renderFiles();
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
    startSendLayout(text);
    sendRipple();
    const sendBtn = $('#send');
    if (sendBtn && !N.REDUCED) {
      anim(sendBtn, [{ transform: 'scale(.91)' }, { transform: 'scale(1)' }], { duration: N.DUR.micro, easing: SP.tap.e });
    }
    if ($('#view-chat').classList.contains('is-empty')) leaveEmpty();
    const target = state.target;
    input.value = ''; state.files = []; renderFiles(); autosize(); updateSend();
    submit(text, files, target);
  }

  function respond(text, targetId) {
    const value = String(text || '').trim();
    if (!value) return;
    startSendLayout('');
    if ($('#view-chat').classList.contains('is-empty')) leaveEmpty();
    submit(value, [], targetId || null);
  }

  async function stopReply() {
    if (!state.replying) return;
    try {
      await N.api.stop();
      N.notify('已停止回复，后台工作继续。', 'cinnabar');
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
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    N.emitPulse?.('send', { x: cx, y: cy });
    const i = document.createElement('i');
    i.className = 'send-ripple';
    i.style.left = cx + 'px';
    i.style.top = cy + 'px';
    document.body.appendChild(i);
    anim(i, [{ opacity: .75, transform: 'scale(.55)' }, { opacity: 0, transform: 'scale(2.2)' }],
      { duration: N.DUR.enter, easing: EASE_OUT }).onfinish = () => i.remove();
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
      anim(ghost, [{ opacity: 1, transform: 'none', filter: 'blur(0px)' }, { opacity: 0, transform: 'translateY(-30px) scale(.92)', filter: 'blur(10px)' }],
        { duration: 420, easing: EASE_OUT, fill: 'forwards' }).onfinish = () => ghost.remove();
    }
    view.classList.remove('is-empty');
    thread.innerHTML = '';
  }

  function startSendLayout(text) {
    if (N.REDUCED) return;
    const wrap = $('#composer-wrap');
    const bounds = conv.getBoundingClientRect();
    const visible = [...thread.children].filter(n => {
      const r = n.getBoundingClientRect(); return n.dataset.key && r.bottom > bounds.top && r.top < bounds.bottom;
    });
    sendLayout = { positions: [wrap, ...visible].map(node => [node, node.getBoundingClientRect()]), text };
  }

  function finishSendLayout(key) {
    const before = sendLayout;
    sendLayout = null;
    if (!before) return;
    const msgNode = thread.querySelector(`[data-key="m:${key}"]`);
    const bubble = msgNode?.querySelector('.bubble') || msgNode;
    const positions = before.positions.map(([node, rect]) => [node, rect, node.getBoundingClientRect()]);
    for (const [node, from, to] of positions) {
      if (!node.isConnected || Math.abs(from.y - to.y) < .5 || node !== $('#composer-wrap') && Math.abs(from.y - to.y) > conv.clientHeight) continue;
      node._sendMotion?.cancel();
      node._sendMotion = anim(node, [{ transform: `translateY(${from.y - to.y}px)` }, { transform: 'none' }], { duration: SP.soft.d, easing: SP.soft.e });
    }
    if (!bubble) return;
    bubble.style.transformOrigin = '100% 100%';
    anim(bubble, [
      { opacity: 0, transform: 'translateY(18px) scale(.9)' },
      { opacity: 1, transform: 'none' },
    ], { duration: SP.bouncy.d, easing: SP.bouncy.e, fill: 'backwards' }).onfinish = () => {
      bubble.style.transformOrigin = '';
    };
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
    beginLayoutChange();
    const box = $('#files');
    box.hidden = !state.files.length;
    box.innerHTML = state.files.map((f, i) => `<span class="file">
      ${f.kind === 'image' ? `<img class="thumb" src="${esc(f.url)}" alt="">` : icon('file', 'i xs')}
      <span>${esc(f.name)}</span><small class="fmeta">${N.fmtSize(f.size)}</small>
      <button type="button" data-act="rm-file" data-i="${i}" aria-label="移除附件 ${esc(f.name)}">${icon('close', 'i xs')}</button></span>`).join('');
    endLayoutChange();
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
    N.menu(e.currentTarget, items, { title: '选择模型' });
  });
  $('#effort-btn').addEventListener('click', e => {
    const pick = async level => {
      try {
        await N.api.select({ provider: state.current.provider, model: state.current.model, effort: level });
        await N.sync.settingsOnly();
        renderEffortLabel(); N.notify(level ? '思考强度：' + (state.effort.labels[level] || level) : '使用提供商默认强度', 'jade');
      } catch (error) {
        N.notify(error?.message || '切换思考强度失败', 'cinnabar', 4000);
      }
    };
    const items = [{ group: '思考强度' }, { label: '提供商默认', sub: '使用服务端默认设置', on: !state.current.effort,
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
  $('#stat-context')?.addEventListener('click', e => toggleUsage(e.currentTarget));
  document.addEventListener('pointerdown', e => {
    const panel = $('#usage-panel');
    if (!panel.hidden && !panel.contains(e.target) && !e.target.closest('#stat-tokens,#stat-context')) toggleUsage(null);
  });
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
      { duration: 340, easing: EASE_OUT }).onfinish = () => { node.style.overflow = ''; renderChat(); };
  }

  async function deleteItem(key) {
    const item = itemByKey(key);
    if (!item || typeof item.index !== 'number') { N.notify('消息尚未保存完成，暂无法删除', 'cinnabar'); return; }
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
    if (typeof item.index !== 'number') { N.notify('消息尚未保存完成，暂无法重新发送', 'cinnabar'); return; }
    const later = state.items.filter(i => i.role === 'user' && state.items.indexOf(i) > state.items.indexOf(item)).length;
    await N.ask({
      title: '编辑并重新发送',
      text: item.text || '',
      description: later > 0 ? `从此处重新发送，将替换后续 ${later} 条消息。本条附件与工作归属会保留；已执行的工具和后台工作不会回滚。` : '修改后将重新生成本轮回复。本条附件与工作归属会保留；已执行的工具和后台工作不会回滚。',
      submitLabel: '重新发送',
    }, async value => {
      await N.api.history({
        operation: 'resend', version: state.historyVersion,
        indices: [item.index], text: String(value).trim(), request_id: N.uid('req'),
      });
      state.historyUndo = true;
      await freshHistory();
      N.notify('已重新发送', 'jade');
    });
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
    if (act === 'dismiss-input') {
      t.disabled = true;
      N.api.dismissInput(Number(t.dataset.id)).then(() => {
        state.queue = state.queue.filter(q => String(q.id) !== t.dataset.id);
        renderQueue(); N.refreshIsland?.();
      }).catch(error => {
        t.disabled = false;
        N.notify(error?.message || '关闭提示失败', 'cinnabar', 4200);
      });
      return;
    }
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
    if (act === 'open-work') return N.openWork(t.dataset.id);
  });
  document.addEventListener('click', e => {
    const tg = e.target.closest('[data-target]');
    if (!tg) return;
    setTarget(tg.dataset.target || null);
    input.focus();
  });

  /* ---------------------------------------------------------------- 导出 */
  N.fileLinks = fileLinks;
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
  N.enterChat = enterChat;
  N.pauseChatDisplay = () => { captureReadingAnchor(); stopFollow(); cancelAnimationFrame(streamRAF); streamRAF = 0; streams.clear(); };
  N.resumeChatDisplay = () => {
    renderChat(true);
    $$('.md', thread).forEach(body => {
      const item = state.items.find(i => 'm:' + i.key === body.closest('[data-key]')?.dataset.key);
      if (item && body._stream) {
        Object.assign(body._stream, { shown: item.text || '', target: item.text || '', final: !item.live });
        streams.delete(body);
        renderMarkdown(body, item.text || '', false);
        body.classList.toggle('streaming', !!item.live);
        if (!item.live) $('.caret', body.parentElement)?.remove();
      }
    });
    followScroll();
  };
  N.toggleUsage = toggleUsage;
  N.filesRender = renderFiles;
  N.reqSig = () => { reqSig = ''; };
  N.patchLive = patchLive;
  N.finalizeAssistant = finalizeAssistant;
  N.FAMILY = FAMILY;
})(window.N);
