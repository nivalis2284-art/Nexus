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
  const MOTION_SCALES = { fast: .72, normal: 1, slow: 1.35 };
  let motionId = 'normal';
  const motionScale = () => MOTION_SCALES[motionId] || 1;
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
    };
    for (const k in base) SP[k] = { e: base[k].e, d: base[k].d };
    if (REDUCED) for (const k in SP) SP[k] = { e: 'ease', d: 1 };
    const root = document.documentElement.style;
    for (const k of ['bouncy', 'snappy', 'soft']) {
      root.setProperty(`--sp-${k}`, SP[k].e);
      root.setProperty(`--sp-${k}-d`, Math.round(SP[k].d * motionScale()) + 'ms');
    }
  }
  buildSprings();
  function setMotionScale(id) {
    if (!MOTION_SCALES[id]) return;
    motionId = id;
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


  /* ---------------------------------------------------------------- 种子数据 */
  function seed() {
    const works = [
      {
        id: 'w-front', title: '前端结构梳理', state: 'running',
        goal: '核对主交流、后台工作与上下文之间的关系，整理成可继续开发的界面结构。',
        constraints: '只读取项目文件，不修改正式前端。',
        work_summary: '主交流、工作详情、上下文与扩展管理的入口已核对，正在整理可复用的交互状态。',
        next_step: '把交互状态整理成文档，并标注仍需确认的取舍。',
        model: 'deepseek-v4.1-flash', effort: 'high', path: 'nexus/web/', started: '14:38',
        progress_text: '已核对 6 组主要入口，正在整理交互状态。',
        steps: [
          { content: '阅读页面结构与样式', status: 'completed', verification: 'verified' },
          { content: '核对交互入口与工作状态', status: 'completed', verification: 'verified' },
          { content: '整理结构文档与待确认项', status: 'in_progress', verification: 'unknown' },
        ],
        evidence: [{ summary: 'nexus/web/index.html · 4 个主区域' }, { summary: 'app.js · 状态与渲染入口' }],
        children: [{ task_id: 'w-sub', title: '核对上下文页字段', state: 'running' }],
        pending_interactions: [],
        records: [
          { key: 'fr1', role: 'user', text: '帮我梳理 Nexus 的前端结构，重点看主交流、后台工作和上下文之间的关系。', at: '14:38' },
          { key: 'fr2', role: 'thinking', thinking: '先读入口与样式，再核对状态字段。不要把正式前端当成可修改对象。', at: '14:38' },
          { key: 'fr3', role: 'tool', name: 'read_file', arguments: { path: 'nexus/web/index.html' }, text: '读取 467 行，识别出 sidebar、topbar、ling-bar、transcript、composer 与五个对话框。', ok: true, at: '14:39' },
          { key: 'fr4', role: 'tool', name: 'read_file', arguments: { path: 'nexus/web/app.js' }, text: '读取 1091 行；状态集中在 state 对象，transcript 按键同步。', ok: true, at: '14:39' },
          { key: 'fr5', role: 'assistant', text: '已核对主交流、工作详情、上下文和扩展管理的入口。\n\n工作详情保留只读记录，控制仍从主交流发起；上下文页区分可编辑指令与自动组装段。', elapsed: 46, at: '14:40' },
        ],
      },
      {
        id: 'w-detail', title: '工作详情交互方案', state: 'waiting_user',
        goal: '确定工作详情在桌面端的默认展示方式。',
        constraints: '不改变工作状态机；只决定界面承载。',
        work_summary: '侧栏适合快速查看进度；内容较多时可使用全屏详情。需要你决定默认采用哪种展示方式。',
        wait_reason: '需要确认默认展示方式。',
        model: 'deepseek-v4.1-flash', effort: 'high', path: 'nexus/web/', started: '14:36',
        progress_text: '等待你决定默认展示方式。',
        steps: [
          { content: '梳理工作详情字段', status: 'completed', verification: 'verified' },
          { content: '比较侧栏与全屏阅读', status: 'completed', verification: 'verified' },
          { content: '确认默认打开方式', status: 'blocked', verification: 'unknown' },
        ],
        evidence: [{ summary: 'tasks.js · 12 个展示字段' }],
        children: [],
        pending_interactions: [{
          id: 'i-detail-1', kind: 'question',
          payload: {
            questions: [{
              question: '工作详情默认在侧栏展开，还是使用全屏页面？',
              options: ['侧栏展开', '全屏阅读'],
            }],
          },
        }],
        records: [
          { key: 'dr1', role: 'user', text: '工作详情现在放在侧栏，内容多了会不会太挤？', at: '14:36' },
          { key: 'dr2', role: 'assistant', text: '侧栏适合快速查看进度；内容较多时可使用全屏详情。两种都保留，只差一个默认值。', elapsed: 21, at: '14:36' },
          { key: 'dr3', role: 'notice', text: '需要你回答问题后才能继续。', at: '14:37' },
        ],
      },
      {
        id: 'w-entry', title: '项目入口检查', state: 'completed',
        goal: '核对主交流、后台工作、上下文、技能、插件和设置六组入口。',
        constraints: '只读检查。',
        work_summary: '已核对六组入口；工作控制继续通过主交流完成。',
        model: 'deepseek-v4.1-flash', effort: 'medium', path: 'nexus/', started: '14:20', finished: '14:32',
        progress_text: '六组入口均已核对。',
        steps: [
          { content: '读取项目说明', status: 'completed', verification: 'verified' },
          { content: '核对主交流与管理入口', status: 'completed', verification: 'verified' },
          { content: '确认后台工作控制边界', status: 'completed', verification: 'verified' },
        ],
        evidence: [{ summary: 'README.md' }, { summary: 'nexus/web/*.js · 6 个入口' }],
        children: [],
        pending_interactions: [],
        records: [
          { key: 'er1', role: 'user', text: '检查一下主要入口是否齐全。', at: '14:20' },
          { key: 'er2', role: 'assistant', text: '已核对主交流、后台工作、上下文、技能、插件和设置。工作控制继续通过主交流完成。', elapsed: 32, at: '14:32' },
        ],
      },
      {
        id: 'w-plugin', title: '插件页信息架构', state: 'paused',
        goal: '整理插件列表、详情、设置与请求记录的层级。',
        constraints: '不改插件协议。',
        work_summary: '已完成列表与详情的字段对照，剩余设置表单与请求记录。',
        wait_reason: '已挂起；需要时手动继续。',
        next_step: '核对设置 schema 的四种字段类型。',
        model: 'claude-sonnet-4.6', effort: 'high', path: 'nexus/web/plugins.js', started: '13:52',
        progress_text: '已完成字段对照，等待恢复。',
        steps: [
          { content: '对照列表与详情字段', status: 'completed', verification: 'verified' },
          { content: '整理设置表单类型', status: 'pending', verification: '' },
          { content: '核对请求记录筛选', status: 'pending', verification: '' },
        ],
        evidence: [],
        children: [],
        pending_interactions: [],
        records: [
          { key: 'pr1', role: 'user', text: '先把插件页的字段对齐，别急着做视觉。', at: '13:52' },
          { key: 'pr2', role: 'assistant', text: '列表已对齐：名称、状态、版本、标识、在途调用。详情还差设置项与请求记录。', elapsed: 64, at: '13:58' },
        ],
      },
      {
        id: 'w-sub', title: '核对上下文页字段', state: 'running', parent_id: 'w-front', parent_kind: 'task',
        goal: '核对上下文页的所有者指令、角色、自动组装段与历史字段。',
        constraints: '只读。',
        work_summary: '已核对所有者指令与三个角色，正在核对自动组装段。',
        next_step: '核对未压缩消息的多选与撤销。',
        model: 'deepseek-v4.1-flash', effort: 'medium', path: 'nexus/web/context.js', started: '14:41',
        progress_text: '已核对指令与角色，正在核对组装段。',
        steps: [
          { content: '核对所有者指令与角色', status: 'completed', verification: 'verified' },
          { content: '核对自动组装段', status: 'in_progress', verification: 'unknown' },
        ],
        evidence: [{ summary: 'context.js · 5 个 section' }],
        children: [], pending_interactions: [], records: [],
      },
    ];

    const items = [
      { key: 'm0', role: 'assistant', src: '后台回报', at: '14:32', workId: 'w-entry', card: 'result', text: '「项目入口检查」已经完成。' },
      { key: 'm1', role: 'user', text: '帮我梳理 Nexus 的前端结构，整理主交流、后台工作和上下文之间的关系。', at: '14:38' },
      { key: 'm2', role: 'thinking', thinking: '先确认页面入口，再看状态字段。结构梳理不需要改代码。', at: '14:38' },
      { key: 'm3', role: 'tool', name: 'read_file', arguments: { path: 'nexus/web/index.html' }, text: '读取 467 行。识别出 sidebar、topbar、ling-bar、requests、transcript、composer 与五个对话框。', ok: true, at: '14:38' },
      { key: 'm4', role: 'tool', name: 'glob', arguments: { pattern: 'nexus/web/**/*.js' }, text: 'app.js\nling.js\ncontext.js\ntasks.js\nskills.js\nplugins.js\nmcp.js\npresentation.js\nediting.js', ok: true, at: '14:39' },
      { key: 'm5', role: 'assistant', elapsed: 58, at: '14:39', workId: 'w-front',
        spine: [
          ['主交流', '承接消息、派发工作，处理需要你决定的事项。'],
          ['后台工作', '保留进度、过程与结果；补充和控制仍从主交流交代。'],
          ['上下文与扩展', '统一管理指令、历史、技能与插件，按当前请求组装。'],
        ],
        text: `## 前端结构已梳理

当前界面围绕**一个持续入口**展开。后台工作独立推进，上下文与扩展配置为它们提供运行条件。

| 区域 | 承载 | 是否可编辑 |
| --- | --- | --- |
| 所有者指令 | 长期偏好 | 可编辑 |
| 角色规则 | 主交流 / 工作 / 子代理 | 可编辑 |
| 自动组装 | 环境、工具、权限 | 只读 |

界面把「谁在说」「说到哪一步」和「需要你决定什么」分开表达，避免把执行细节混进交流。` },
      { key: 'm6', role: 'user', text: '顺便看看上下文管理页还缺什么。', at: '14:44' },
      { key: 'm7', role: 'assistant', elapsed: 22, at: '14:44', text: `上下文管理页已经覆盖所有者指令、角色规则和历史摘要。还缺三处细节：

- 历史条目的多选删除与撤销；
- 运行中禁止编辑历史的提示；
- 自动组装内容的来源说明。

我先把这三处列成清单，你确认后再交给后台工作。` },
    ];

    const requests = [
      {
        id: 'r-detail', token: 'r-detail', kind: 'question', status: 'pending', owner: '灵', workId: 'w-detail',
        payload: { questions: [{ question: '工作详情默认在侧栏展开，还是使用全屏页面？', options: ['侧栏展开', '全屏阅读'] }] },
      },
    ];

    const providers = [
      {
        id: 'deepseek', name: 'DeepSeek', family: 'openai_chat_completions', base_url: 'https://api.deepseek.com',
        has_key: true, models: [
          { id: 'deepseek-v4.1-flash', context_window_tokens: 128000 },
          { id: 'deepseek-v4.1', context_window_tokens: 256000 },
        ],
      },
      {
        id: 'anthropic', name: 'Anthropic', family: 'anthropic_messages', base_url: 'https://api.anthropic.com',
        has_key: true, models: [{ id: 'claude-sonnet-4.6', context_window_tokens: 200000 }],
      },
      {
        id: 'local', name: '本地网关', family: 'openai_responses', base_url: 'http://127.0.0.1:8080/v1',
        has_key: false, models: [{ id: 'qwen3-max', context_window_tokens: 131072 }],
      },
    ];

    const mcp = [
      { name: 'filesystem', transport: 'stdio', command: 'npx', args: ['-y', '@modelcontextprotocol/server-filesystem', 'H:\\文档\\LingNexus\\Nexus'], env: {}, enabled: true, connected: true, tool_count: 12, error: '', has_token: false },
      { name: '团队检索', transport: 'http', url: 'https://mcp.example.com/search', enabled: true, connected: false, tool_count: null, error: '连接超时（8s）', has_token: true },
      { name: 'nexus-core', transport: 'stdio', command: 'python', args: ['-m', 'nexus.mcp.core'], env: {}, enabled: true, connected: true, tool_count: 8, error: '', managed: true },
    ];

    const skills = [
      { name: 'design-taste-frontend', source: 'personal', description: '前端视觉方向、页面布局和浏览器检查。', body: '# 视觉方向\n\n先确定画面语言，再落到组件；动效服务于理解。', effective: true, read_only: false },
      { name: 'impeccable', source: 'personal', description: '界面审视、排版、交互与视觉细节。', body: '逐项检查层级、间距、对比与动效。', effective: false, read_only: false },
      { name: 'impeccable', source: 'project', description: '本项目对界面审视的补充约定：中文文案优先，动效克制。', body: '项目内同名技能覆盖个人技能。', effective: true, read_only: false },
      { name: '项目开发约定', source: 'project', description: '当前项目的开发边界和协作方式。', body: '不立假想敌；先完成最小可用闭环。', effective: true, read_only: false },
      { name: 'pdf', source: 'builtin', description: '读取、拆分与生成 PDF。内置技能只读。', body: '内置技能内容不可编辑。', effective: true, read_only: true },
      { name: 'workbuddy-docx', source: 'builtin', description: '生成与美化 Word 文档。', body: '内置技能内容不可编辑。', effective: true, read_only: true },
    ];

    const plugins = [
      {
        id: 'file-tools', name: '项目文件工具', version: '1.4.0', enabled: true, status: 'loaded',
        description: '读取文件、检索目录与执行项目内的命令。',
        capabilities: [{ title: '读取文件', description: '读取工作区内的文本与图片' }, { title: '检索', description: '按模式查找文件' }, { title: '命令', description: '执行项目命令' }],
        pages: [{ id: 'overview', title: '使用概览', path: 'pages/overview.html' }],
        settings_schema: {
          properties: {
            allow_write: { type: 'boolean', title: '允许写入文件', description: '关闭后只保留读取能力。', default: false },
            root: { type: 'string', title: '默认根目录', description: '相对工作目录解析。', default: 'nexus/' },
            max_files: { type: 'integer', title: '单次最多文件数', minimum: 1, maximum: 500, default: 80 },
            mode: { type: 'string', title: '检索模式', enum: ['快速', '完整'], default: '快速' },
          },
        },
        settings: { allow_write: false, root: 'nexus/', max_files: 80, mode: '快速' },
        active_calls: 1, progress: '', error: '', root: 'C:/Users/34375/.nexus/plugins',
      },
      {
        id: 'search-plus', name: '检索增强', version: '0.9.2', enabled: true, status: 'draining',
        description: '为检索补充语义排序；停用后会等待在途调用收尾。',
        capabilities: ['语义排序', '结果去重'],
        pages: [],
        settings_schema: { properties: { top_k: { type: 'integer', title: '返回条数', minimum: 1, maximum: 50, default: 8 } } },
        settings: { top_k: 8 }, active_calls: 2, progress: '正在等待 2 个在途调用收尾…', error: '', root: 'C:/Users/34375/.nexus/plugins',
      },
      {
        id: 'demo-pack', name: '扩展能力示例', version: '0.3.2', enabled: false, status: 'unloaded',
        description: '演示插件配置、状态与功能页入口。',
        capabilities: [{ title: '示例能力' }],
        pages: [{ id: 'panel', title: '示例面板', path: 'pages/panel.html' }],
        settings_schema: { properties: {} },
        settings: {}, active_calls: 0, progress: '', error: '', root: 'C:/Users/34375/.nexus/plugins',
      },
    ];

    const modelRequests = [
      { id: 1284, created_at: '2026-10-28T14:44:12', kind: 'ling', owner_id: 'ling-main', model: 'deepseek-v4.1-flash', status: 'ok', protocol: 'openai_chat_completions',
        plugins: [{ name: '项目文件工具', status: 'used', reason: '本次需要读取工作区文件' }, { name: '检索增强', status: 'skipped', reason: '未触发检索' }],
        materials: { workspace: 'H:/文档/LingNexus/Nexus', skills: ['项目开发约定'], tools: 12 },
        body: { model: 'deepseek-v4.1-flash', messages: 18, stream: true, reasoning_effort: 'high' } },
      { id: 1283, created_at: '2026-10-28T14:41:03', kind: 'task', owner_id: 'w-front', model: 'deepseek-v4.1-flash', status: 'ok', protocol: 'openai_chat_completions',
        plugins: [{ name: '项目文件工具', status: 'used', reason: '核对页面入口' }],
        materials: { task: '前端结构梳理', step: '整理结构文档与待确认项' },
        body: { model: 'deepseek-v4.1-flash', messages: 26, stream: true } },
      { id: 1282, created_at: '2026-10-28T14:36:44', kind: 'subagent', owner_id: 'w-sub', model: 'deepseek-v4.1-flash', status: 'ok', protocol: 'openai_chat_completions',
        plugins: [{ name: '检索增强', status: 'not_applicable', reason: '只读核对，无需检索' }],
        materials: { parent: '前端结构梳理' },
        body: { model: 'deepseek-v4.1-flash', messages: 9, stream: true } },
      { id: 1281, created_at: '2026-10-28T14:31:08', kind: 'ling', owner_id: 'ling-main', model: 'deepseek-v4.1-flash', status: 'error', protocol: 'openai_chat_completions',
        plugins: [], materials: {},
        error: '上游返回 429：请求过于频繁，请稍后重试。',
        body: { model: 'deepseek-v4.1-flash', messages: 12, stream: true } },
      { id: 1280, created_at: '2026-10-28T13:58:20', kind: 'task', owner_id: 'w-plugin', model: 'claude-sonnet-4.6', status: 'ok', protocol: 'anthropic_messages',
        plugins: [{ name: '项目文件工具', status: 'used', reason: '读取插件中心源码' }],
        materials: { task: '插件页信息架构', step: '对照列表与详情字段' },
        body: { model: 'claude-sonnet-4.6', max_tokens: 8192, stream: true, output_config: { effort: 'high' } } },
      { id: 1279, created_at: '2026-10-28T13:52:01', kind: 'ling', owner_id: 'ling-main', model: 'deepseek-v4.1-flash', status: 'ok', protocol: 'openai_chat_completions',
        plugins: [{ name: '检索增强', status: 'failed', error: '索引不可用', reason: '本次需要检索历史记录' }],
        materials: {}, body: { model: 'deepseek-v4.1-flash', messages: 6, stream: true } },
    ];

    const contextData = {
      running: false,
      owner_instructions: [
        '当前任务以明确需求为准，不提前扩展功能。',
        '先完成最小可用闭环；新增防御措施必须对应具体问题。',
        '改前端时保留既有画面语言与动效，不做静态贴图。',
      ],
      roles: [
        { id: 'main', title: '主交流 · 灵', text: '理解用户意图，承接交流；派发工作，并解释结果和需要决定的事项。\n\n当前明确要求优先于一般偏好；遇到无法从请求与规则判断的冲突时，先问所有者，不要猜。' },
        { id: 'work', title: '后台工作', text: '围绕目标独立推进，保留过程、证据与结果。\n\n先读再改；用专用文件工具；改代码后运行项目自己的检查并报告。完成后停止，只汇报实际做过的事。' },
        { id: 'subagent', title: '子代理', text: '完成有明确边界的子任务，向父工作报告。\n\n不扩大范围；遇到需要所有者决定的事项，交回父工作。' },
      ],
      sections: [
        { id: 'env', title: '环境', text: 'Platform: win32\nDate: 2026-10-28\nWorkspace: H:/文档/LingNexus/Nexus\nShell: Git Bash' },
        { id: 'project', title: '项目指令', text: '# AGENTS.md\n\n- 不立假想敌。\n- 先完成最小可用闭环。\n- 没有需求，不提前投资技术。\n- 如实验证和汇报。' },
        { id: 'skills', title: '技能', text: 'design-taste-frontend · 前端视觉方向、页面布局和浏览器检查。\n项目开发约定 · 当前项目的开发边界和协作方式。' },
        { id: 'tools', title: '工具', text: 'read_file · write_file · edit_file · glob · grep · run_command · work · subagent\n（共 12 个工具，按权限过滤）' },
        { id: 'permission', title: '权限', text: 'permission = ask\n写文件和运行命令前询问所有者。' },
      ],
      summary: {
        through: 17, index: 0,
        text: '此前交流确认：先做独立前端预览，不改正式前端；保留主交流、后台工作与上下文三层结构；视觉与动效以用户提供的预览版为准。',
      },
      messages: [
        { index: 0, role: 'user', text: '我希望重构前端，但先要一个独立预览。', at: '2026-10-28T10:02:00' },
        { index: 1, role: 'assistant', text: '可以先做独立预览，不改正式前端。预览需要覆盖所有页面与配置项。', at: '2026-10-28T10:02:20' },
        { index: 2, role: 'user', text: '保留主交流入口和独立后台工作。', at: '2026-10-28T10:04:00' },
        { index: 3, role: 'assistant', text: '明白。工作详情保持只读，控制仍从主交流发起。', at: '2026-10-28T10:04:18' },
        { index: 4, role: 'user', text: '上下文页要能编辑指令、角色和摘要。', at: '2026-10-28T10:06:00' },
        { index: 5, role: 'assistant', text: '会区分可编辑部分与自动组装的只读段。', at: '2026-10-28T10:06:24' },
        { index: 6, role: 'user', text: '插件页要能看到设置和请求记录。', at: '2026-10-28T10:08:00' },
        { index: 7, role: 'assistant', text: '插件详情包含能力、功能页、设置；请求记录单独一栏。', at: '2026-10-28T10:08:30' },
        { index: 8, role: 'tool', name: 'read_file', text: '读取 nexus/web/index.html', at: '2026-10-28T10:09:00' },
        { index: 9, role: 'user', text: '设置里要有多个提供商和 MCP。', at: '2026-10-28T10:10:00' },
        { index: 10, role: 'assistant', text: '提供商会按协议区分，模型可单独设置上下文窗口。', at: '2026-10-28T10:10:26' },
        { index: 11, role: 'user', text: '好，先做出来我再确认效果。', at: '2026-10-28T10:12:00' },
      ],
    };

    return {
      version: 1,
      view: 'chat',
      theme: 'day',
      palette: 'jade',
      motion: 'normal',
      scene: 'conversation',
      target: null,
      files: [],
      replying: false,
      queue: [{ id: 'q-1', text: '另外把插件页的字段也核对一遍。', status: 'queued' }],
      held: false,
      connectionError: '',
      turns: 3,
      usage: { input: 18240, output: 6120, cached: 12800, created: 2400, total: 24360, estimated: false, has_usage: true, complete: true },
      context: { system: 9200, tools: 5400, messages: 9600, window: 128000 },
      items, works, requests, providers,
      current: { provider: 'deepseek', model: 'deepseek-v4.1-flash', effort: 'high' },
      effort: { levels: ['low', 'medium', 'high', 'xhigh', 'max'], labels: { low: '低', medium: '中', high: '高', xhigh: '很高', max: '最高' }, current: 'high' },
      instructions: '先说明关键取舍，再执行；遇到不确定的外部影响时先停下确认。',
      permission: 'ask',
      workspace: 'H:/文档/LingNexus/Nexus',
      mcp, skills, plugins, modelRequests, contextData,
      canUndo: false,
      undo: null,
      historyUndo: false,
      lastWork: null,
      logs: [],
    };
  }

  /* ---------------------------------------------------------------- 持久化 */
  const KEY = 'nexus-preview-v1';
  let saveTimer = null;
  function save() {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try {
        const s = N.state;
        localStorage.setItem(KEY, JSON.stringify({
          view: s.view, theme: s.theme, palette: s.palette, motion: s.motion, target: s.target, turns: s.turns,
          usage: s.usage, context: s.context, items: s.items, works: s.works,
          requests: s.requests, providers: s.providers, current: s.current,
          effort: s.effort, instructions: s.instructions, permission: s.permission,
          workspace: s.workspace, mcp: s.mcp, skills: s.skills, plugins: s.plugins,
          modelRequests: s.modelRequests, contextData: s.contextData,
          canUndo: s.canUndo, undo: s.undo, historyUndo: s.historyUndo, lastWork: s.lastWork,
          scene: s.scene, queue: s.queue, held: s.held,
        }));
      } catch (_) {}
    }, 240);
  }
  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return null;
      const value = JSON.parse(raw);
      if (!value || value.version === 0) return null;
      if (Array.isArray(value.items)) value.items.forEach(i => { if (i.live) i.live = false; });
      value.replying = false;
      return value;
    } catch (_) { return null; }
  }
  function clearSaved() {
    try { localStorage.removeItem(KEY); } catch (_) {}
  }

  N.$ = $; N.$$ = $$; N.esc = esc; N.icon = icon; N.wait = wait; N.uid = uid;
  N.clone = clone; N.clamp = clamp; N.EASE_OUT = EASE_OUT;
  N.SP = SP; N.anim = anim; N.moveInk = moveInk;
  N.motionScale = motionScale; N.setMotionScale = setMotionScale; N.MOTION_SCALES = MOTION_SCALES;
  N.THEMES = THEMES;
  Object.defineProperty(N, 'REDUCED', { get: () => REDUCED });
  N.nowHM = nowHM; N.fmtSize = fmtSize; N.fmtNum = fmtNum; N.shortPath = shortPath;
  N.approx = approx; N.firstLine = firstLine; N.countUp = countUp; N.truncate = truncate; N.demoImage = demoImage;
  N.md = md; N.decorate = decorate; N.fileText = fileText;
  N.STATE_LABEL = STATE_LABEL; N.stateLabel = stateLabel; N.statusDetail = statusDetail; N.progressText = progressText;
  N.seed = seed; N.save = save; N.load = load; N.clearSaved = clearSaved;
  N.state = Object.assign(seed(), load() || {});
  N.timers = new Set();
  N.later = (fn, ms) => { const t = setTimeout(() => { N.timers.delete(t); fn(); }, ms); N.timers.add(t); return t; };
  N.clearTimers = () => { N.timers.forEach(clearTimeout); N.timers.clear(); };
})(window.N);
