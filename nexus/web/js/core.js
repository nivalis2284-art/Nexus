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
  const MOTION_SCALES = { '0.5': .675, '0.75': 1.0125, '1': 1.35, '1.25': 1.6875, '1.5': 2.025 };
  const MOTION_ALIAS = { fast: '0.5', normal: '0.75', slow: '1' };
  const motionKey = id => (MOTION_ALIAS[id] || (MOTION_SCALES[id] ? id : '1'));
  let motionId = '1';
  const motionScale = () => MOTION_SCALES[motionId] || 1.35;
  /* 统一时长阶梯：微交互 / 小元素 / 卡片与折叠 / 页面与弹窗 */
  const DUR = { fast: 96, micro: 160, base: 220, move: 320, enter: 480 };
  rmq.addEventListener?.('change', e => { REDUCED = e.matches; buildSprings(); });

  /* ---------------------------------------------------------------- 弹簧 */
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
    const base = {
      bouncy: spring({ stiffness: 320, damping: 19 }),
      snappy: spring({ stiffness: 460, damping: 34 }),
      soft: spring({ stiffness: 170, damping: 24 }),
      island: spring({ stiffness: 340, damping: 27 }),
      pop: spring({ stiffness: 520, damping: 30 }),
      /* 轻微过冲后一次落定：阻尼比约 0.62，回弹幅度不到 9%，不来回震荡。 */
      settle: spring({ stiffness: 460, damping: 26 }),
    };
    for (const k in base) SP[k] = { e: base[k].e, d: base[k].d };
    if (REDUCED) for (const k in SP) SP[k] = { e: 'ease', d: 1 };
    const root = document.documentElement.style;
    for (const k of ['bouncy', 'snappy', 'soft', 'settle']) {
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
    if (REDUCED) return el.animate(kf, { ...o, duration: 1, delay: 0 });
    const s = motionScale();
    return el.animate(kf, { ...o, duration: (o.duration || 0) * s, delay: (o.delay || 0) * s });
  };

  /* 液态指示器：前缘先行、后缘追随 */
  function moveInk(ink, target, axis = 'x', instant = false) {
    if (!ink || !target) return;
    const P = axis === 'x' ? 'translateX' : 'translateY';
    const S = axis === 'x' ? 'width' : 'height';
    const O = axis === 'x' ? 'height' : 'width';
    const pos = axis === 'x' ? target.offsetLeft : target.offsetTop;
    const size = axis === 'x' ? target.offsetWidth : target.offsetHeight;
    const p0 = ink._axis === axis ? ink._pos : null, s0 = ink._size;
    ink.style.transform = `${P}(${pos}px)`; ink.style[S] = size + 'px'; ink.style[O] = '';
    ink._pos = pos; ink._size = size; ink._axis = axis;
    if (instant || p0 == null || REDUCED || (p0 === pos && s0 === size)) return;
    ink.getAnimations().forEach(a => a.cancel());
    const fwd = pos > p0, lead = fwd ? pos + size : pos, a0 = p0, a1 = p0 + s0;
    const midStart = fwd ? a0 + (pos - a0) * 0.12 : lead;
    const midEnd = fwd ? lead : a1 + (pos + size - a1) * 0.12;
    ink.animate([
      { transform: `${P}(${p0}px)`, [S]: s0 + 'px', easing: 'cubic-bezier(.45,0,.2,1)' },
      { offset: 0.34, transform: `${P}(${midStart}px)`, [S]: (midEnd - midStart) + 'px', easing: SP.bouncy.e },
      { transform: `${P}(${pos}px)`, [S]: size + 'px' },
    ], { duration: Math.max(260, SP.snappy.d * motionScale()) });
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
    const start = performance.now();
    const tick = now => {
      const t = Math.min(1, (now - start) / dur);
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
  const FILE_MAP = {
    'README.md': `# Nexus

Nexus 是一个持续存在的本地代理：主交流负责理解与交代，后台工作独立推进，
上下文与扩展配置为它们提供运行条件。

## 目录

- \`nexus/web/\` 网页前端
- \`nexus/runtime/\` 运行与调度
- \`nexus/task/\` 工作状态与记录
- \`docs/\` 需求、研究与验收

## 运行

\`\`\`powershell
.venv\\Scripts\\python.exe -m nexus
\`\`\`

打开 http://127.0.0.1:8766/ 即可。
`,
    'nexus/web/index.html': `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>Nexus</title>
</head>
<body>
  <aside id="sidebar">…</aside>
  <main id="main">
    <header id="topbar">…</header>
    <div id="scroller"><div id="transcript"></div></div>
    <form id="composer">…</form>
  </main>
</body>
</html>
`,
    'nexus/web/app.js': `// One page, no framework. State is one object; the transcript rebuilds from it.
const state = { providers: [], current: {}, items: [], running: false };

async function refresh() {
  const value = await api("/api/state");
  Object.assign(state, value);
  renderModelLabel();
}

function renderTranscript(options) {
  const box = $("transcript");
  const following = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
  if (!state.items.length) box.replaceChildren(welcome());
  else reconcileTranscript(box, state.items, state.workspacePath);
  updateStats();
}
`,
    'nexus/web/tasks.js': `const taskView = { tasks: [], current: null, request: 0 };

async function openTask(id) {
  const value = await api("/api/tasks/" + encodeURIComponent(id));
  taskView.current = value;
  $("task-panel").hidden = false;
  $("task-title").textContent = value.title;
  renderTaskSummary(value);
  renderTaskSteps(value.steps || []);
  renderTaskMessages(value.messages || [], value.live);
}
`,
  };
  function fileText(path) {
    if (FILE_MAP[path]) return FILE_MAP[path];
    return `# ${path}\n\n（预览数据）\n\n这个文件没有真实内容。正式前端会通过 /api/workspace-file 读取工作区。\n`;
  }

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
  function decorate(root) {
    $$('a[data-file]', root).forEach(a => {
      a.addEventListener('click', e => {
        e.preventDefault();
        N.openFile(a.dataset.file);
      });
    });
    $$('pre', root).forEach(pre => {
      if (pre.closest('.code-block')) return;
      const code = pre.querySelector('code');
      if (!code) return;
      const lang = [...code.classList].find(c => c.startsWith('language-'))?.slice(9) || '';
      if (lang && typeof hljs !== 'undefined' && hljs.getLanguage(lang)) {
        try { hljs.highlightElement(code); } catch (_) {}
      }
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
    pending: '尚未开始；点击开始后进入执行队列。',
    queued: '已加入执行队列，等待当前工作结束。',
    running: '正在执行当前步骤。',
    pausing: '已请求挂起，正在停止当前执行。',
    paused: '已挂起；不会因补充信息而自动继续。',
    waiting_user: '等待你处理；完成确认后继续保存的工作。',
    completed: '任务已完成。',
    cancelled: '任务已取消；重新启用后才能再次开始。',
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
    if (w.pending_interactions?.length) parts.push(`有 ${w.pending_interactions.length} 项待你处理。`);
    return parts.join(' ');
  }
  const progressText = w => w.work_summary || w.progress_text || '暂无进展说明';

  /* ---------------------------------------------------------------- 主题 */
  const THEMES = [
    { id: 'jade', name: '灵青 · 朱砂', desc: '暖色 · 灵青主色 + 朱砂强调', day: { p: '#00a884', a: '#ff5a36' }, night: { p: '#19d3a8', a: '#ff7a5c' } },
    { id: 'cool', name: '浅蓝 · 淡紫', desc: '冷色 · 浅蓝主色 + 淡紫强调', day: { p: '#4f7cf7', a: '#a78bfa' }, night: { p: '#8fb0ff', a: '#c4b0ff' } },
  ];


  /* ---------------------------------------------------------------- 初始状态 */
  /* 界面偏好留在浏览器；业务状态一律来自后端快照，避免本地假数据盖住真实记录。 */
  function seed() {
    return {
      version: 2,
      view: 'chat',
      theme: 'day',
      palette: 'jade',
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
  N.SP = SP; N.anim = anim; N.moveInk = moveInk;
  N.motionScale = motionScale; N.setMotionScale = setMotionScale; N.MOTION_SCALES = MOTION_SCALES;
  N.MOTION_LEVELS = MOTION_LEVELS; N.MOTION_ALIAS = MOTION_ALIAS; N.DUR = DUR;
  N.THEMES = THEMES;
  Object.defineProperty(N, 'REDUCED', { get: () => REDUCED });
  N.nowHM = nowHM; N.fmtSize = fmtSize; N.fmtNum = fmtNum; N.shortPath = shortPath;
  N.duration = duration;
  N.approx = approx; N.firstLine = firstLine; N.countUp = countUp; N.truncate = truncate; N.demoImage = demoImage;
  N.md = md; N.decorate = decorate; N.fileText = fileText;
  N.STATE_LABEL = STATE_LABEL; N.stateLabel = stateLabel; N.statusDetail = statusDetail; N.progressText = progressText;
  N.seed = seed; N.save = save; N.load = load; N.clearSaved = clearSaved;
  N.state = Object.assign(seed(), load() || {});
  N.timers = new Set();
  N.later = (fn, ms) => { const t = setTimeout(() => { N.timers.delete(t); fn(); }, ms); N.timers.add(t); return t; };
  N.clearTimers = () => { N.timers.forEach(clearTimeout); N.timers.clear(); };
})(window.N);
