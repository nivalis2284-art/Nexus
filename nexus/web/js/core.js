/* ==========================================================================
   Nexus · 灵 — 预览内核
   工具函数、弹簧曲线、演示数据、Markdown 渲染、持久化。
   不连接任何后端；所有状态都在浏览器内。
   ========================================================================== */
'use strict';

window.N = window.N || {};

(function (N) {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = s => String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const icon = (n, cls = 'i') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const wait = ms => new Promise(r => setTimeout(r, ms));
  const uid = (p = 'id') => p + '-' + Math.random().toString(36).slice(2, 9);
  const clone = v => JSON.parse(JSON.stringify(v));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const EASE_OUT = 'cubic-bezier(.22,1,.36,1)';
  const rmq = matchMedia('(prefers-reduced-motion: reduce)');
  let REDUCED = rmq.matches;
  /* 五档速度：1.0x 是基准，0.5x 最快，1.5x 最慢。 */
  const MOTION_LEVELS = ['0.5', '0.75', '1', '1.25', '1.5'];
  const MOTION_LABELS = { '0.5': '快', '0.75': '较快', '1': '标准', '1.25': '较慢', '1.5': '慢' };
  const MOTION_SCALES = { '0.5': .68, '0.75': .84, '1': 1, '1.25': 1.16, '1.5': 1.32 };
  const MOTION_ALIAS = { fast: '0.5', normal: '0.75', slow: '1' };
  const motionKey = id => (MOTION_ALIAS[id] || (MOTION_SCALES[id] ? id : '1'));
  let motionId = '1';
  const motionScale = () => MOTION_SCALES[motionId] || 1;
  /* 统一时长阶梯：微交互 / 小元素 / 卡片与折叠 / 页面与弹窗 */
  const tokenStyle = getComputedStyle(document.documentElement);
  const numberToken = name => parseFloat(tokenStyle.getPropertyValue(name));
  const DUR = Object.fromEntries(['press', 'fast', 'micro', 'base', 'content', 'move', 'enter', 'exit', 'fade', 'text'].map(name => [name, numberToken('--duration-' + name)]));
  const EASE_ENTER = tokenStyle.getPropertyValue('--ease-enter').trim();
  const EASE_MOVE = tokenStyle.getPropertyValue('--ease-move').trim();
  const EASE_EXIT = tokenStyle.getPropertyValue('--ease-exit').trim();
  const EASE_PRESS = tokenStyle.getPropertyValue('--ease-press').trim() || 'cubic-bezier(.2,0,0,1)';
  const MOTION = {
    stagger: numberToken('--enter-stagger'),
    contentStagger: numberToken('--content-stagger'),
    floatStagger: numberToken('--float-stagger') || 16,
    window: numberToken('--enter-window'),
    contentWindow: numberToken('--content-window'),
    floatWindow: numberToken('--float-window') || 64,
    offset: numberToken('--enter-offset'),
    contentOffset: numberToken('--content-offset'),
    floatOffset: numberToken('--float-offset'),
    exitOffset: numberToken('--exit-offset') || -4,
    enterScale: numberToken('--enter-scale') || .985,
    contentScale: numberToken('--content-scale') || .99,
    floatScale: numberToken('--float-scale') || .96,
    theme: numberToken('--duration-theme'),
  };
  rmq.addEventListener?.('change', e => { REDUCED = e.matches; buildSprings(); });

  /* ---------------------------------------------------------------- 阻尼谐振子弹簧 */
  const LINEAR_OK = CSS.supports('animation-timing-function', 'linear(0, 0.5, 1)');
  function spring({ stiffness = 200, damping = 20, mass = 1 } = {}) {
    const dt = 1 / 600; let x = 0, v = 0, t = 0; const pts = [[0, 0]];
    while (t < 2.4) {
      const a = (-stiffness * (x - 1) - damping * v) / mass;
      v += a * dt; x += v * dt; t += dt; pts.push([t, x]);
      if (Math.abs(x - 1) < 0.0006 && Math.abs(v) < 0.006) break;
    }
    const dur = t, COUNT = 56, vals = [];
    for (let i = 0, j = 0; i <= COUNT; i++) {
      const tt = (i / COUNT) * dur;
      while (j < pts.length - 1 && pts[j + 1][0] < tt) j++;
      vals.push(+pts[j][1].toFixed(4));
    }
    vals[vals.length - 1] = 1;
    return { e: LINEAR_OK ? `linear(${vals.join(',')})` : 'cubic-bezier(.3,1.25,.5,1)', d: Math.round(dur * 1000) };
  }
  const SP = {};
  function buildSprings() {
    const bouncy = spring({ stiffness: 320, damping: 19 });
    const snappy = spring({ stiffness: 460, damping: 34 });
    const soft = spring({ stiffness: 170, damping: 24 });
    const island = spring({ stiffness: 340, damping: 27 });
    const base = {
      bouncy,
      snappy,
      soft,
      island,
      tap: bouncy,
      settle: soft,
      morph: island,
      rebound: bouncy,
      pop: bouncy,
    };
    for (const k in base) SP[k] = { e: base[k].e, d: base[k].d };
    if (REDUCED) for (const k in SP) SP[k] = { e: 'ease', d: 1 };
    const root = document.documentElement.style;
    for (const k of ['bouncy', 'snappy', 'soft', 'island', 'tap', 'settle', 'morph', 'rebound']) {
      root.setProperty(`--sp-${k}`, SP[k].e);
      root.setProperty(`--sp-${k}-d`, Math.round(SP[k].d * motionScale()) + 'ms');
    }
  }
  buildSprings();
  function setMotionScale(id) {
    const key = motionKey(id);
    if (!MOTION_SCALES[key]) return;
    motionId = key;
    document.documentElement.style.setProperty('--motion', String(MOTION_SCALES[key]));
    buildSprings();
  }
  const anim = (el, kf, o = {}) => {
    if (REDUCED) return el.animate(kf, { easing: EASE_MOVE, ...o, duration: 1, delay: 0 });
    const s = motionScale();
    return el.animate(kf, { easing: EASE_MOVE, ...o, duration: (o.duration || 0) * s, delay: (o.delay || 0) * s });
  };

  function visibleItems(elements, viewport) {
    const bounds = viewport.getBoundingClientRect();
    return elements.filter(el => {
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0 && rect.bottom > Math.max(0, bounds.top) && rect.top < Math.min(innerHeight, bounds.bottom)
        && rect.right > Math.max(0, bounds.left) && rect.left < Math.min(innerWidth, bounds.right);
    });
  }

  function cancelMotion(root) {
    for (const el of [root, ...root.querySelectorAll('*')]) {
      el._enterMotion?.cancel(); el._exitMotion?.cancel();
      el._enterMotion = el._exitMotion = null;
    }
  }

  function enterItems(elements, viewport, { profile = 'page', delay = 0, duration } = {}) {
    const visible = visibleItems(elements, viewport);
    const page = profile === 'page', floating = profile === 'float';
    return visible.map((el, i) => {
      const running = [el._enterMotion, el._exitMotion].some(a => a && a.playState !== 'finished' && a.playState !== 'idle');
      const current = getComputedStyle(el);
      const start = running ? { opacity: current.opacity, transform: current.transform } : null;
      el._enterMotion?.cancel(); el._exitMotion?.cancel(); el._exitMotion = null;
      if (REDUCED) { el._enterMotion = null; return null; }
      const offset = floating ? MOTION.floatOffset : page ? MOTION.offset : MOTION.contentOffset;
      const fromTransform = page ? `translateY(${offset}px) scale(${MOTION.enterScale})` : `translateY(${offset}px)`;
      const step = floating ? MOTION.floatStagger : page ? MOTION.stagger : MOTION.contentStagger;
      const win = floating ? MOTION.floatWindow : page ? MOTION.window : MOTION.contentWindow;
      const baseDelay = delay || (page ? 40 : floating ? 30 : 0);
      const wait = baseDelay + Math.min(i * step, win);
      const from = start || { opacity: 0, transform: fromTransform };
      const to = { opacity: 1, transform: 'none' };
      const motion = anim(el, [from, to], {
        duration: duration ?? (floating ? DUR.base : SP.soft.d),
        delay: wait,
        easing: floating ? EASE_OUT : SP.soft.e,
        fill: 'backwards',
      });
      el._enterMotion = motion;
      const clear = () => { if (el._enterMotion === motion) el._enterMotion = null; };
      motion.onfinish = motion.oncancel = clear;
      return motion;
    }).filter(Boolean);
  }

  function exitItems(elements, viewport) {
    return visibleItems(elements, viewport).map(el => {
      const current = getComputedStyle(el);
      const from = { opacity: current.opacity, transform: current.transform };
      el._enterMotion?.cancel(); el._exitMotion?.cancel(); el._enterMotion = null;
      const motion = anim(el, [from, { opacity: 0, transform: `${from.transform === 'none' ? '' : from.transform} translateY(${MOTION.exitOffset}px) scale(.99)` }],
        { duration: 150, easing: 'ease-in', fill: 'forwards' });
      el._exitMotion = motion;
      return motion;
    });
  }

  /* 液态指示器：水滴前缘先抵达，后缘滞后跟上，产生黏连张力 */
  function moveInk(ink, target, axis = 'x', instant = false) {
    if (!ink || !target) return;
    const P = axis === 'x' ? 'translateX' : 'translateY';
    const S = axis === 'x' ? 'width' : 'height';
    const O = axis === 'x' ? 'height' : 'width';
    const pos = axis === 'x' ? target.offsetLeft : target.offsetTop;
    const size = axis === 'x' ? target.offsetWidth : target.offsetHeight;
    if (!size) return;
    const previousAxis = ink._axis;
    if (previousAxis === axis && ink.style.transform === `${P}(${pos}px)` && ink.style[S] === size + 'px') return;
    const current = getComputedStyle(ink);
    const matrix = current.transform === 'none' ? null : new DOMMatrixReadOnly(current.transform);
    const p0 = previousAxis === axis ? (matrix ? (axis === 'x' ? matrix.m41 : matrix.m42) : 0) : null;
    const s0 = parseFloat(current[S]);
    ink.getAnimations().forEach(a => a.cancel());
    ink.style.transform = `${P}(${pos}px)`; ink.style[S] = size + 'px'; ink.style[O] = '';
    ink._axis = axis;
    if (instant || p0 == null || REDUCED || (p0 === pos && s0 === size)) return;
    const a0 = p0, b0 = p0 + s0, a1 = pos, b1 = pos + size;
    const goFwd = a1 >= a0;
    const midStart = goFwd ? a0 + (a1 - a0) * 0.12 : a1;
    const midEnd   = goFwd ? b1 : b0 + (b1 - b0) * 0.12;
    anim(ink, [
      { transform: `${P}(${a0}px)`, [S]: s0 + 'px', easing: 'cubic-bezier(.45,0,.2,1)' },
      { offset: 0.34, transform: `${P}(${midStart}px)`, [S]: Math.max(size, midEnd - midStart) + 'px', easing: SP.bouncy.e },
      { transform: `${P}(${pos}px)`, [S]: size + 'px' },
    ], { duration: Math.max(520, SP.bouncy.d * 0.9), easing: 'linear' });
  }

  /* ---------------------------------------------------------------- 格式 */
  const nowHM = () => new Date().toTimeString().slice(0, 5);
  function fmtSize(size) {
    if (typeof size !== 'number') return '';
    return size >= 1024 * 1024 ? (size / (1024 * 1024)).toFixed(1) + ' MiB' : Math.ceil(size / 1024) + ' KiB';
  }
  const fmtNum = n => Number(n || 0).toLocaleString('en-US');
  function shortPath(path) {
    const parts = String(path || '').split(/[\\/]/).filter(Boolean);
    return parts.length > 2 ? '…/' + parts.slice(-2).join('/') : String(path || '');
  }
  function approx(value) {
    if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
    if (value >= 1e6) return '~' + (value / 1e6).toFixed(2) + 'M';
    if (value >= 1e3) return '~' + (value / 1e3).toFixed(1) + 'K';
    return '~' + value;
  }
  function duration(seconds) {
    if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return '';
    if (seconds < 60) return (Math.round(seconds * 10) / 10) + ' 秒';
    return Math.floor(seconds / 60) + ' 分 ' + Math.round(seconds % 60) + ' 秒';
  }
  function countUp(el, to, dur = 700) {
    if (!el) return;
    if (REDUCED) { el.textContent = String(to); return; }
    el.textContent = '0';
    const start = performance.now();
    const tick = now => {
      const t = clamp((now - start) / dur, 0, 1);
      el.textContent = String(Math.round(to * (1 - Math.pow(1 - t, 3))));
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function firstLine(value) {
    return String(value || '').split(/\r?\n/).find(line => line.trim()) || '';
  }
  function truncate(text, n) {
    const s = String(text || '');
    return s.length > n ? s.slice(0, n) + '…' : s;
  }

  /* ---------------------------------------------------------------- 演示图片 */
  function demoImage(kind = 'chart') {
    const chart = `<rect x="18" y="120" width="26" height="58" rx="6" fill="#bfe9dd"/>
      <rect x="54" y="96" width="26" height="82" rx="6" fill="#7bdcc2"/>
      <rect x="90" y="112" width="26" height="66" rx="6" fill="#bfe9dd"/>
      <rect x="126" y="72" width="26" height="106" rx="6" fill="#00b48f"/>
      <rect x="162" y="90" width="26" height="88" rx="6" fill="#7bdcc2"/>`;
    const ui = `<rect x="18" y="22" width="90" height="10" rx="5" fill="#cfd8d5"/>
      <rect x="18" y="44" width="188" height="150" rx="12" fill="#ffffff" stroke="#e2e8e5"/>
      <rect x="34" y="62" width="60" height="8" rx="4" fill="#b9d8cf"/>
      <rect x="34" y="80" width="120" height="8" rx="4" fill="#e5ecea"/>
      <rect x="34" y="96" width="94" height="8" rx="4" fill="#e5ecea"/>
      <rect x="34" y="120" width="74" height="26" rx="13" fill="#00b48f" opacity=".85"/>`;
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="224" height="212" viewBox="0 0 224 212">
      <defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f3f7f5"/><stop offset="1" stop-color="#e6f2ee"/></linearGradient></defs>
      <rect width="224" height="212" rx="16" fill="url(#bg)"/>${kind === 'ui' ? ui : chart}</svg>`;
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  }

  /* ---------------------------------------------------------------- Markdown */
  let mdRenderer = null;
  function renderer() {
    if (mdRenderer || typeof marked === 'undefined') return mdRenderer;
    const r = new marked.Renderer();
    r.link = function (token) {
      const href = String(token.href || '');
      const text = token.tokens ? this.parser.parseInline(token.tokens) : esc(token.text || href);
      const title = token.title ? ` title="${esc(token.title)}"` : '';
      if (/^(https?:|mailto:|tel:)/i.test(href)) {
        return `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer"${title}>${text}</a>`;
      }
      if (href.charAt(0) === '#' || /^(javascript|vbscript|data):/i.test(href)) return text;
      return `<a href="#" class="file-link" data-file="${esc(href.replace(/^file:\/\//, ''))}"${title}>${text}</a>`;
    };
    mdRenderer = r;
    return r;
  }
  function md(text) {
    if (typeof marked === 'undefined') return `<p>${esc(text).replace(/\n/g, '<br>')}</p>`;
    const html = marked.parse(String(text || ''), { gfm: true, breaks: true, renderer: renderer() });
    return typeof DOMPurify === 'undefined' ? html : DOMPurify.sanitize(html, {
      ADD_ATTR: ['target', 'rel', 'data-file'],
      FORBID_TAGS: ['style', 'iframe', 'object', 'embed', 'form', 'input', 'button', 'textarea', 'select', 'link', 'meta', 'base'],
      FORBID_ATTR: ['style'],
    });
  }
  function decorate(root, { highlight = true } = {}) {
    [...(root.matches?.('a[data-file]') ? [root] : []), ...$$('a[data-file]', root)].forEach(a => {
      if (a._fileBound) return;
      a._fileBound = true;
      a.addEventListener('click', e => {
        e.preventDefault();
        N.openFile(a.dataset.file);
      });
    });
    [...(root.matches?.('pre') ? [root] : []), ...$$('pre', root)].forEach(pre => {
      const code = pre.querySelector('code');
      if (!code) return;
      const lang = [...code.classList].find(c => c.startsWith('language-'))?.slice(9) || '';
      if (highlight && !code.dataset.highlighted && lang && typeof hljs !== 'undefined' && hljs.getLanguage(lang)) {
        try { hljs.highlightElement(code); } catch (_) {}
      }
      const existing = pre.closest('.code-block');
      if (existing) { $('.code-lang', existing).textContent = lang || '代码'; return; }
      const block = document.createElement('div');
      block.className = 'code-block';
      block.innerHTML = `<div class="code-head"><span class="code-lang">${esc(lang || '代码')}</span><button type="button" class="code-copy">复制</button></div>`;
      pre.replaceWith(block);
      block.appendChild(pre);
      $('.code-copy', block).addEventListener('click', async e => {
        try { await navigator.clipboard.writeText(code.textContent); e.target.textContent = '已复制'; }
        catch { e.target.textContent = '复制失败'; }
        setTimeout(() => { e.target.textContent = '复制'; }, 1200);
      });
    });
  }

  /* ---------------------------------------------------------------- 状态标签 */
  const STATE_LABEL = {
    pending: '待执行', queued: '排队中', running: '执行中', pausing: '正在挂起',
    paused: '已挂起', waiting_user: '等待你处理', completed: '已完成', cancelled: '已取消',
  };
  const STATE_DETAIL = {
    pending: '尚未开始，等待调度执行。',
    queued: '已加入执行队列，等待当前工作结束。',
    running: '正在执行当前步骤。',
    pausing: '已请求挂起，正在停止当前执行。',
    paused: '已挂起；不会因补充信息而自动继续。',
    waiting_user: '等待你处理；完成确认后继续保存的工作。',
    completed: '工作已完成。',
    cancelled: '工作已取消；如需继续请在对话中提出。',
  };
  const stateLabel = w => (w.state === 'waiting_user'
    ? (w.pending_interactions?.some(i => i.kind === 'approval') ? '等待你批准操作'
      : w.pending_interactions?.some(i => i.kind === 'question') ? '等待你回答问题' : '等待你处理')
    : STATE_LABEL[w.state] || w.state);
  function statusDetail(w) {
    const parts = [w.state === 'waiting_user' && w.pending_interactions?.length
      ? stateLabel(w) : STATE_DETAIL[w.state] || w.state];
    if (w.wait_reason && !(w.state === 'waiting_user' && w.pending_interactions?.length)) parts.push(w.wait_reason);
    if (w.blocked) parts.push('受阻：' + w.blocked);
    if (w.next_step && w.state !== 'completed') parts.push('下一步：' + w.next_step);
    if (w.pending_interactions?.length) parts.push(`有 ${w.pending_interactions.length} 项待处理。`);
    return parts.join(' ');
  }
  const progressText = w => w.work_summary || w.progress_text || '暂无进展说明';

  /* ---------------------------------------------------------------- 主题 */
  const THEMES = [
    {
      id: 'jade',
      name: '灵玉',
      sub: '琢玉含光 · 鎏金相映',
      tag: '温玉 · 鎏金',
      desc: '温润玉色，鎏金环绕，光泽流转。',
      day: { p: '#078c70', a: '#d99416' },
      night: { p: '#28d8b2', a: '#f5b83d' },
    },
    {
      id: 'cool',
      name: '星枢',
      sub: '群星层叠 · 星轨微芒',
      tag: '星枢 · 群星',
      desc: '清冷星空，群星环绕，微光交映。',
      day: { p: '#3568f0', a: '#855cf2' },
      night: { p: '#82a8ff', a: '#c0a6ff' },
    },
  ];


  /* ---------------------------------------------------------------- 初始状态 */
  /* 界面偏好留在浏览器；业务状态一律来自后端快照，避免本地假数据盖住真实记录。 */
  function seed() {
    return {
      version: 2,
      view: 'chat',
      theme: 'night',
      palette: 'cool',
      motion: '1',
      scene: 'conversation',
      target: null,
      files: [],
      replying: false,
      running: false,
      queue: [],
      held: false,
      connectionError: '',
      connected: false,
      sessionId: '',
      historyVersion: '',
      liveRevision: 0,
      live: {},
      baseItems: [],
      liveItems: [],
      pending: [],
      turns: 0,
      usage: { input: 0, cached: 0, created: 0, output: 0, total_input: 0, total: 0, has_usage: false, complete: false, estimated: false },
      context: { system: 0, tools: 0, messages: 0, window: 0 },
      items: [], works: [], requests: [], providers: [],
      current: { provider: '', model: '', effort: '' },
      effort: { levels: [], labels: {}, current: '', mapping: null },
      profile: null,
      instructions: '',
      permission: 'ask',
      workspace: '',
      projects: [], project: null, defaultProject: null,
      contextWindow: 0,
      mcp: [], skills: [], plugins: [], modelRequests: [],
      pluginRoot: '', pluginError: '',
      contextData: { owner_instructions: [], roles: [], sections: [], summary: null, messages: [], version: '', running: false },
      workGroups: { paused: true, ended: true, archived: false },
      canUndo: false, undo: null, historyUndo: false, lastWork: null, logs: [],
    };
  }

  /* ---------------------------------------------------------------- 偏好持久化 */
  /* 只保存界面偏好。交流、工作、配置都以服务端为准，刷新即重新读取。 */
  const KEY = 'nexus-ui-prefs-v1';
  const PREFS = ['view', 'theme', 'palette', 'motion', 'scene', 'target', 'workGroups'];
  let saveTimer = null;
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try {
        const s = N.state;
        const value = {};
        for (const key of PREFS) value[key] = s[key];
        localStorage.setItem(KEY, JSON.stringify(value));
      } catch (_) {}
    }, 200);
  }
  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return null;
      const value = JSON.parse(raw);
      return value && typeof value === 'object' ? value : null;
    } catch (_) { return null; }
  }
  function clearSaved() {
    try { localStorage.removeItem(KEY); } catch (_) {}
  }


  N.$ = $; N.$$ = $$; N.esc = esc; N.icon = icon; N.wait = wait; N.uid = uid;
  N.clone = clone; N.clamp = clamp; N.EASE_OUT = EASE_OUT;
  N.EASE_ENTER = EASE_ENTER; N.EASE_MOVE = EASE_MOVE; N.EASE_EXIT = EASE_EXIT; N.EASE_PRESS = EASE_PRESS;
  N.SP = SP; N.anim = anim; N.moveInk = moveInk; N.rebuildSprings = buildSprings;
  N.MOTION = MOTION; N.enterItems = enterItems; N.exitItems = exitItems; N.cancelMotion = cancelMotion;
  N.motionScale = motionScale; N.setMotionScale = setMotionScale; N.MOTION_SCALES = MOTION_SCALES;
  N.MOTION_LEVELS = MOTION_LEVELS; N.MOTION_LABELS = MOTION_LABELS; N.MOTION_ALIAS = MOTION_ALIAS; N.DUR = DUR;
  N.THEMES = THEMES;
  Object.defineProperty(N, 'REDUCED', { get: () => REDUCED });
  N.nowHM = nowHM; N.fmtSize = fmtSize; N.fmtNum = fmtNum; N.shortPath = shortPath;
  N.duration = duration;
  N.approx = approx; N.firstLine = firstLine; N.countUp = countUp; N.truncate = truncate; N.demoImage = demoImage;
  N.md = md; N.decorate = decorate;
  N.STATE_LABEL = STATE_LABEL; N.stateLabel = stateLabel; N.statusDetail = statusDetail; N.progressText = progressText;
  N.seed = seed; N.save = save; N.load = load; N.clearSaved = clearSaved;
  N.state = Object.assign(seed(), load() || {});
  N.timers = new Set();
  N.later = (fn, ms) => { const t = setTimeout(() => { N.timers.delete(t); fn(); }, ms); N.timers.add(t); return t; };
  N.clearTimers = () => { N.timers.forEach(clearTimeout); N.timers.clear(); };
})(window.N);
