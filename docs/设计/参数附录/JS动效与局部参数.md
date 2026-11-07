# JavaScript 动效与局部参数位置

由 `更新参数索引.py` 从独立资源生成。数值是现状记录，不是每项都允许自由修改；语义与约束见《参数与动效》。

| 名称／属性 | 值 | 条件／选择器 | 来源 |
|---|---|---|---|
| `动效／局部常量` | `const MAX_SLOTS = 3;` | `chat` | 预览资源/js/chat.js:14 |
| `动效／局部常量` | `let followRAF = 0;` | `chat` | 预览资源/js/chat.js:65 |
| `动效／局部常量` | `let readingAnchor = null, manualDown = false, lastScrollTop = 0, touchY = 0;` | `chat` | 预览资源/js/chat.js:67 |
| `动效／局部常量` | `const top = conv.getBoundingClientRect().top + 76;` | `chat` | 预览资源/js/chat.js:70 |
| `动效／局部常量` | `let last = 0;` | `chat` | 预览资源/js/chat.js:78 |
| `动效／局部常量` | `const dt = last ? Math.min(50, now - last) : 16;` | `chat` | 预览资源/js/chat.js:93 |
| `动效／局部常量` | `const y = e.touches[0].clientY;` | `chat` | 预览资源/js/chat.js:111 |
| `动效／局部常量` | `setTimeout(() => {` | `chat` | 预览资源/js/chat.js:155 |
| `动效／局部常量` | `const node = target?.nodeType === 1 && target.tagName === source.tagName ? target : source.cloneNode(false);` | `chat` | 预览资源/js/chat.js:165 |
| `动效／局部常量` | `const hit = /^ {0,3}('{3,}\|~{3,})(.*)$/.exec(line);` | `chat` | 预览资源/js/chat.js:189 |
| `动效／局部常量` | `let streamRAF = 0;` | `chat` | 预览资源/js/chat.js:209 |
| `动效／局部常量` | `const dt = Math.min(50, s.last ? now - s.last : 34);` | `chat` | 预览资源/js/chat.js:220 |
| `动效／局部常量` | `let take = s.final \|\| N.REDUCED ? remaining.length : Math.max(1, Math.ceil(remaining.length * dt / Math.max(dt, s.deadline - now)));` | `chat` | 预览资源/js/chat.js:221 |
| `动效／局部常量` | `const s = body._stream \|\| (body._stream = { shown: '', target: '', last: 0, final: false });` | `chat` | 预览资源/js/chat.js:240 |
| `动效／局部常量` | `setTimeout(() => { e.target.textContent = '复制'; }, 1200);` | `chat` | 预览资源/js/chat.js:350 |
| `动效／局部常量` | `if (!N.REDUCED) anim(bar, [{ opacity: 0, transform: 'translateY(-8px)' }, { opacity: 1, transform: 'none' }], { duration: N.DUR.move, easing: EASE_OUT });` | `chat` | 预览资源/js/chat.js:459 |
| `动效／局部常量` | `const r = n.getBoundingClientRect(); return r.bottom > rect.top + 76 && r.top < rect.bottom;` | `chat` | 预览资源/js/chat.js:495 |
| `动效／局部常量` | `const messages = hero ? $$('.hero-kicker, h1, .hero-sub, .chips', hero) : visible;` | `chat` | 预览资源/js/chat.js:498 |
| `动效／局部常量` | `{ duration: SP.soft.d, easing: SP.soft.e });` | `chat` | 预览资源/js/chat.js:541 |
| `动效／局部常量` | `let added = 0;` | `chat` | 预览资源/js/chat.js:598 |
| `动效／局部常量` | `const slot = section.children[index + 1];` | `chat` | 预览资源/js/chat.js:639 |
| `动效／局部常量` | `{ duration: 200, easing: 'ease-in' }).onfinish = () => {` | `chat` | 预览资源/js/chat.js:751 |
| `动效／局部常量` | `const used = state.context ? (state.context.system \|\| 0) + (state.context.tools \|\| 0) + (state.context.messages \|\| 0) : 0;` | `chat` | 预览资源/js/chat.js:909 |
| `动效／局部常量` | `const win = state.context?.window \|\| 0;` | `chat` | 预览资源/js/chat.js:910 |
| `动效／局部常量` | `const pct = win ? Math.min(100, Math.round(used / win * 100)) : 0;` | `chat` | 预览资源/js/chat.js:911 |
| `动效／局部常量` | `const known = used > 0;` | `chat` | 预览资源/js/chat.js:913 |
| `动效／局部常量` | `const used = (c.system \|\| 0) + (c.tools \|\| 0) + (c.messages \|\| 0);` | `chat` | 预览资源/js/chat.js:926 |
| `动效／局部常量` | `const pct = c.window ? Math.min(100, Math.round(used / c.window * 100)) : 0;` | `chat` | 预览资源/js/chat.js:927 |
| `动效／局部常量` | `const cache = u.estimated ? '—' : (u.input + u.cached ? Math.round(u.cached / (u.input + u.cached) * 100) + '%' : '—');` | `chat` | 预览资源/js/chat.js:929 |
| `动效／局部常量` | `const from = panel.hidden ? { opacity: 0, transform: 'translateY(8px) scale(.94)' }` | `chat` | 预览资源/js/chat.js:959 |
| `动效／局部常量` | `{ duration: N.SP.bouncy.d, easing: N.SP.bouncy.e, fill: 'backwards' });` | `chat` | 预览资源/js/chat.js:969 |
| `动效／局部常量` | `const motion = N.anim(panel, [from, { opacity: 0, transform: 'translateY(6px) scale(.94)' }],` | `chat` | 预览资源/js/chat.js:975 |
| `动效／局部常量` | `{ duration: 140, easing: 'ease-in', fill: 'forwards' });` | `chat` | 预览资源/js/chat.js:976 |
| `动效／局部常量` | `{ duration: N.SP.bouncy.d, easing: N.SP.bouncy.e });` | `chat` | 预览资源/js/chat.js:993 |
| `动效／局部常量` | `{ duration: 140, easing: 'ease-in', fill: 'forwards' })).onfinish = done;` | `chat` | 预览资源/js/chat.js:1000 |
| `动效／局部常量` | `const h = Math.min(200, Math.ceil(Math.max(parseFloat(style.minHeight) \|\| 0, inputMeasure.scrollHeight) / 4) * 4);` | `chat` | 预览资源/js/chat.js:1062 |
| `动效／局部常量` | `const blobToBase64 = blob => new Promise((resolve, reject) => {` | `chat` | 预览资源/js/chat.js:1087 |
| `动效／局部常量` | `anim(sendBtn, [{ transform: 'scale(.91)' }, { transform: 'scale(1)' }], { duration: N.DUR.micro, easing: SP.tap.e });` | `chat` | 预览资源/js/chat.js:1168 |
| `动效／局部常量` | `setTimeout(async () => {` | `chat` | 预览资源/js/chat.js:1192 |
| `动效／局部常量` | `const cx = r.left + r.width / 2, cy = r.top + r.height / 2;` | `chat` | 预览资源/js/chat.js:1239 |
| `动效／局部常量` | `{ duration: N.DUR.enter, easing: EASE_OUT }).onfinish = () => i.remove();` | `chat` | 预览资源/js/chat.js:1247 |
| `动效／局部常量` | `{ duration: 420, easing: EASE_OUT, fill: 'forwards' }).onfinish = () => ghost.remove();` | `chat` | 预览资源/js/chat.js:1260 |
| `动效／局部常量` | `node._sendMotion = anim(node, [{ transform: 'translateY(${from.y - to.y}px)' }, { transform: 'none' }], { duration: SP.soft.d, easing: SP.soft.e });` | `chat` | 预览资源/js/chat.js:1286 |
| `动效／局部常量` | `], { duration: SP.bouncy.d, easing: SP.bouncy.e, fill: 'backwards' }).onfinish = () => {` | `chat` | 预览资源/js/chat.js:1293 |
| `动效／局部常量` | `let seq = 0;` | `chat` | 预览资源/js/chat.js:1299 |
| `动效／局部常量` | `const MAX_FILES = 8;` | `chat` | 预览资源/js/chat.js:1300 |
| `动效／局部常量` | `if (state.files.length >= MAX_FILES) { N.notify('每次最多 8 个附件', 'cinnabar'); break; }` | `chat` | 预览资源/js/chat.js:1303 |
| `动效／局部常量` | `if (isImage && file.size > 5 * 1024 * 1024) { N.notify('图片超过 5 MiB：' + file.name, 'cinnabar'); continue; }` | `chat` | 预览资源/js/chat.js:1305 |
| `动效／局部常量` | `if (!isImage && file.size > 25 * 1024 * 1024) { N.notify('文件超过 25 MiB：' + file.name, 'cinnabar'); continue; }` | `chat` | 预览资源/js/chat.js:1306 |
| `动效／局部常量` | `{ duration: 340, easing: EASE_OUT }).onfinish = () => { node.style.overflow = ''; renderChat(); };` | `chat` | 预览资源/js/chat.js:1419 |
| `动效／局部常量` | `const done = () => { state.files.splice(i, 1); renderFiles(); updateSend(); N.save(); };` | `chat` | 预览资源/js/chat.js:1494 |
| `动效／局部常量` | `if (chip && !N.REDUCED) N.anim(chip, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'scale(.6)' }], { duration: N.DUR.micro, easing: 'ease-in' }).onfinish = done;` | `chat` | 预览资源/js/chat.js:1495 |
| `动效／局部常量` | `const ctx = { tab: 'system', selected: new Set(), undo: null, canUndo: false, busy: false, enteredAt: 0 };` | `context` | 预览资源/js/context.js:8 |
| `动效／局部常量` | `const hit = /T(\d{2}):(\d{2})/.exec(text);` | `context` | 预览资源/js/context.js:69 |
| `动效／局部常量` | `const meta = [turnClock(t.at), t.tools ? '${t.tools} 次工具调用' : '', t.indices.length > 2 ? '${t.indices.length} 条记录' : ''].filter(Boolean).join(' · ');` | `context` | 预览资源/js/context.js:97 |
| `动效／局部常量` | `{ duration: N.SP.soft.d, delay: 200 + i * 70, easing: N.SP.soft.e, fill: 'backwards' }));` | `context` | 预览资源/js/context.js:139 |
| `动效／局部常量` | `{ duration: 320, delay: 240 + i * 60, easing: N.EASE_OUT, fill: 'backwards' }));` | `context` | 预览资源/js/context.js:141 |
| `动效／局部常量` | `const used = (c.system \|\| 0) + (c.tools \|\| 0) + (c.messages \|\| 0);` | `context` | 预览资源/js/context.js:151 |
| `动效／局部常量` | `const known = used > 0;` | `context` | 预览资源/js/context.js:152 |
| `动效／局部常量` | `const pct = known && c.window ? Math.min(100, Math.round(used / c.window * 100)) : null;` | `context` | 预览资源/js/context.js:153 |
| `动效／局部常量` | `const denom = used \|\| 1;` | `context` | 预览资源/js/context.js:154 |
| `动效／局部常量` | `let lastSync = 0;` | `context` | 预览资源/js/context.js:203 |
| `动效／局部常量` | `const wait = (ctx.enteredAt \|\| 0) + 720 - Date.now();` | `context` | 预览资源/js/context.js:207 |
| `动效／局部常量` | `if (wait > 0) { setTimeout(maybeSync, wait); return; }` | `context` | 预览资源/js/context.js:208 |
| `动效／局部常量` | `{ duration: N.DUR.exit, easing: N.EASE_EXIT, fill: 'forwards' }).finished;` | `context` | 预览资源/js/context.js:260 |
| `动效／局部常量` | `{ duration: N.DUR.micro, delay: Math.min(k * 20, 60), easing: N.EASE_EXIT, fill: 'forwards' }).finished));` | `context` | 预览资源/js/context.js:320 |
| `动效／局部常量` | `const wait = ms => new Promise(r => setTimeout(r, ms));` | `core` | 预览资源/js/core.js:17 |
| `动效／局部常量` | `const uid = (p = 'id') => p + '-' + Math.random().toString(36).slice(2, 9);` | `core` | 预览资源/js/core.js:18 |
| `动效／局部常量` | `const EASE_OUT = 'cubic-bezier(.22,1,.36,1)';` | `core` | 预览资源/js/core.js:21 |
| `动效／局部常量` | `const MOTION_LEVELS = ['0.5', '0.75', '1', '1.25', '1.5'];` | `core` | 预览资源/js/core.js:25 |
| `动效／局部常量` | `const MOTION_LABELS = { '0.5': '快', '0.75': '较快', '1': '标准', '1.25': '较慢', '1.5': '慢' };` | `core` | 预览资源/js/core.js:26 |
| `动效／局部常量` | `const MOTION_SCALES = { '0.5': .68, '0.75': .84, '1': 1, '1.25': 1.16, '1.5': 1.32 };` | `core` | 预览资源/js/core.js:27 |
| `动效／局部常量` | `const MOTION_ALIAS = { fast: '0.5', normal: '0.75', slow: '1' };` | `core` | 预览资源/js/core.js:28 |
| `动效／局部常量` | `const motionKey = id => (MOTION_ALIAS[id] \|\| (MOTION_SCALES[id] ? id : '1'));` | `core` | 预览资源/js/core.js:29 |
| `动效／局部常量` | `let motionId = '1';` | `core` | 预览资源/js/core.js:30 |
| `动效／局部常量` | `const motionScale = () => MOTION_SCALES[motionId] \|\| 1;` | `core` | 预览资源/js/core.js:31 |
| `动效／局部常量` | `const EASE_PRESS = tokenStyle.getPropertyValue('--ease-press').trim() \|\| 'cubic-bezier(.2,0,0,1)';` | `core` | 预览资源/js/core.js:39 |
| `动效／局部常量` | `const LINEAR_OK = CSS.supports('animation-timing-function', 'linear(0, 0.5, 1)');` | `core` | 预览资源/js/core.js:59 |
| `动效／局部常量` | `const dt = 1 / 600; let x = 0, v = 0, t = 0; const pts = [[0, 0]];` | `core` | 预览资源/js/core.js:61 |
| `动效／局部常量` | `const a = (-stiffness * (x - 1) - damping * v) / mass;` | `core` | 预览资源/js/core.js:63 |
| `动效／局部常量` | `const dur = t, COUNT = 56, vals = [];` | `core` | 预览资源/js/core.js:67 |
| `动效／局部常量` | `for (let i = 0, j = 0; i <= COUNT; i++) {` | `core` | 预览资源/js/core.js:68 |
| `动效／局部常量` | `const bouncy = spring({ stiffness: 320, damping: 19 });` | `core` | 预览资源/js/core.js:78 |
| `动效／局部常量` | `const snappy = spring({ stiffness: 460, damping: 34 });` | `core` | 预览资源/js/core.js:79 |
| `动效／局部常量` | `const soft = spring({ stiffness: 170, damping: 24 });` | `core` | 预览资源/js/core.js:80 |
| `动效／局部常量` | `const island = spring({ stiffness: 340, damping: 27 });` | `core` | 预览资源/js/core.js:81 |
| `动效／局部常量` | `if (REDUCED) for (const k in SP) SP[k] = { e: 'ease', d: 1 };` | `core` | 预览资源/js/core.js:94 |
| `动效／局部常量` | `if (REDUCED) return el.animate(kf, { easing: EASE_MOVE, ...o, duration: 1, delay: 0 });` | `core` | 预览资源/js/core.js:110 |
| `动效／局部常量` | `return el.animate(kf, { easing: EASE_MOVE, ...o, duration: (o.duration \|\| 0) * s, delay: (o.delay \|\| 0) * s });` | `core` | 预览资源/js/core.js:112 |
| `动效／局部常量` | `const baseDelay = delay \|\| (page ? 40 : floating ? 30 : 0);` | `core` | 预览资源/js/core.js:144 |
| `动效／局部常量` | `const from = start \|\| { opacity: 0, transform: fromTransform };` | `core` | 预览资源/js/core.js:146 |
| `动效／局部常量` | `const to = { opacity: 1, transform: 'none' };` | `core` | 预览资源/js/core.js:147 |
| `动效／局部常量` | `duration: duration ?? (floating ? DUR.base : SP.soft.d),` | `core` | 预览资源/js/core.js:149 |
| `动效／局部常量` | `delay: wait,` | `core` | 预览资源/js/core.js:150 |
| `动效／局部常量` | `const motion = anim(el, [from, { opacity: 0, transform: '${from.transform === 'none' ? '' : from.transform} translateY(${MOTION.exitOffset}px) scale(.99)' }],` | `core` | 预览资源/js/core.js:166 |
| `动效／局部常量` | `{ duration: 150, easing: 'ease-in', fill: 'forwards' });` | `core` | 预览资源/js/core.js:167 |
| `动效／局部常量` | `const p0 = previousAxis === axis ? (matrix ? (axis === 'x' ? matrix.m41 : matrix.m42) : 0) : null;` | `core` | 预览资源/js/core.js:186 |
| `动效／局部常量` | `const s0 = parseFloat(current[S]);` | `core` | 预览资源/js/core.js:187 |
| `动效／局部常量` | `const a0 = p0, b0 = p0 + s0, a1 = pos, b1 = pos + size;` | `core` | 预览资源/js/core.js:192 |
| `动效／局部常量` | `const goFwd = a1 >= a0;` | `core` | 预览资源/js/core.js:193 |
| `动效／局部常量` | `const midStart = goFwd ? a0 + (a1 - a0) * 0.12 : a1;` | `core` | 预览资源/js/core.js:194 |
| `动效／局部常量` | `const midEnd   = goFwd ? b1 : b0 + (b1 - b0) * 0.12;` | `core` | 预览资源/js/core.js:195 |
| `动效／局部常量` | `], { duration: Math.max(520, SP.bouncy.d * 0.9), easing: 'linear' });` | `core` | 预览资源/js/core.js:200 |
| `动效／局部常量` | `const nowHM = () => new Date().toTimeString().slice(0, 5);` | `core` | 预览资源/js/core.js:204 |
| `动效／局部常量` | `return size >= 1024 * 1024 ? (size / (1024 * 1024)).toFixed(1) + ' MiB' : Math.ceil(size / 1024) + ' KiB';` | `core` | 预览资源/js/core.js:207 |
| `动效／局部常量` | `const fmtNum = n => Number(n \|\| 0).toLocaleString('en-US');` | `core` | 预览资源/js/core.js:209 |
| `动效／局部常量` | `const t = clamp((now - start) / dur, 0, 1);` | `core` | 预览资源/js/core.js:231 |
| `动效／局部常量` | `const chart = '<rect x="18" y="120" width="26" height="58" rx="6" fill="#bfe9dd"/>` | `core` | 预览资源/js/core.js:248 |
| `动效／局部常量` | `const ui = '<rect x="18" y="22" width="90" height="10" rx="5" fill="#cfd8d5"/>` | `core` | 预览资源/js/core.js:253 |
| `动效／局部常量` | `const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="224" height="212" viewBox="0 0 224 212">` | `core` | 预览资源/js/core.js:259 |
| `动效／局部常量` | `const lang = [...code.classList].find(c => c.startsWith('language-'))?.slice(9) \|\| '';` | `core` | 预览资源/js/core.js:304 |
| `动效／局部常量` | `setTimeout(() => { e.target.textContent = '复制'; }, 1200);` | `core` | 预览资源/js/core.js:318 |
| `动效／局部常量` | `version: 2,` | `core` | 预览资源/js/core.js:380 |
| `动效／局部常量` | `liveRevision: 0,` | `core` | 预览资源/js/core.js:396 |
| `动效／局部常量` | `turns: 0,` | `core` | 预览资源/js/core.js:401 |
| `动效／局部常量` | `contextWindow: 0,` | `core` | 预览资源/js/core.js:412 |
| `动效／局部常量` | `N.later = (fn, ms) => { const t = setTimeout(() => { N.timers.delete(t); fn(); }, ms); N.timers.add(t); return t; };` | `core` | 预览资源/js/core.js:443 |
| `动效／局部常量` | `const motion = animateSurface(menu, from, { opacity: 0, transform: 'translateY(6px) scale(.94)' },` | `dialogs` | 预览资源/js/dialogs.js:28 |
| `动效／局部常量` | `{ duration: 140, easing: 'ease-in', fill: 'forwards' });` | `dialogs` | 预览资源/js/dialogs.js:29 |
| `动效／局部常量` | `{ duration: N.SP.bouncy.d, easing: N.EASE_OUT, fill: 'backwards' }, N.SP.bouncy.e);` | `dialogs` | 预览资源/js/dialogs.js:51 |
| `动效／局部常量` | `const left = Math.max(8, Math.min(r.left, innerWidth - mw - 12));` | `dialogs` | 预览资源/js/dialogs.js:56 |
| `动效／局部常量` | `let top = r.top - mh - 8;` | `dialogs` | 预览资源/js/dialogs.js:57 |
| `动效／局部常量` | `const next = e.key === 'Home' ? 0 : e.key === 'End' ? buttons.length - 1 : (index + (e.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length;` | `dialogs` | 预览资源/js/dialogs.js:83 |
| `动效／局部常量` | `let previewVersion = 0;` | `dialogs` | 预览资源/js/dialogs.js:161 |
| `动效／局部常量` | `if (line) setTimeout(() => { if (version === previewVersion && fp.open && !fp._closing) $('.fp-line.target', fpBody)?.scrollIntoView({ block: 'center' }); }, 40);` | `dialogs` | 预览资源/js/dialogs.js:205 |
| `动效／局部常量` | `{ duration: N.SP.bouncy.d, easing: N.EASE_OUT, fill: 'backwards' }, N.SP.bouncy.e);` | `dialogs` | 预览资源/js/dialogs.js:230 |
| `动效／局部常量` | `const motion = animateSurface(d, from, { opacity: 0, transform: 'translateY(12px) scale(.95)' },` | `dialogs` | 预览资源/js/dialogs.js:243 |
| `动效／局部常量` | `{ duration: 160, easing: 'ease-in', fill: 'forwards' });` | `dialogs` | 预览资源/js/dialogs.js:244 |
| `动效／局部常量` | `const {$,esc}=N;let owner=null,version=0;` | `host-sdk` | 预览资源/js/host-sdk.js:4 |
| `动效／局部常量` | `try{const r=await request('/api/plugins/${encodeURIComponent(pluginId)}/runtime');if(current!==version\|\|pluginId!==owner)return;const text=v=>esc(JSON.stringify(v,null,2));` | `host-sdk` | 预览资源/js/host-sdk.js:10 |
| `动效／局部常量` | `if(b.dataset.hostMethod){b.disabled=true;const method=b.dataset.hostMethod;const params=method.startsWith('jobs.')?{job_id:b.dataset.resource}:{dialogue_id:b.dataset.resource};const r=await request('/api/plugins/${encodeURIComponent(owner)}/runtime',{method,params});if(method==='dialogues.history'){const box=document.createElement('pre');box.className='host-runtime-json';box.textContent=JSON.stringify(r,null,2);b.parentElement.append(box);}else await runtime();}` | `host-sdk` | 预览资源/js/host-sdk.js:19 |
| `动效／局部常量` | `let ctx = canvas ? canvas.getContext('2d') : null;` | `pivot` | 预览资源/js/pivot.js:70 |
| `动效／局部常量` | `let cw = 0, ch = 0, dpr = 1;` | `pivot` | 预览资源/js/pivot.js:71 |
| `动效／局部常量` | `let fieldRAF = 0;` | `pivot` | 预览资源/js/pivot.js:72 |
| `动效／局部常量` | `let lastFieldTime = 0;` | `pivot` | 预览资源/js/pivot.js:73 |
| `动效／局部常量` | `let lusterAngle = 0;` | `pivot` | 预览资源/js/pivot.js:74 |
| `动效／局部常量` | `const bgPointer = { x: -9999, y: -9999, active: false, down: false, holdTime: 0, lastTrail: 0 };` | `pivot` | 预览资源/js/pivot.js:78 |
| `动效／局部常量` | `const JADE_RING_ROT = -0.20; // ~ -16° 主星环倾角，与中枢浑仪主轴呼应` | `pivot` | 预览资源/js/pivot.js:80 |
| `动效／局部常量` | `let hubX = 0, hubY = 0, hubOnHero = false;` | `pivot` | 预览资源/js/pivot.js:81 |
| `动效／局部常量` | `let savedHubOffsetFromMid = -157;` | `pivot` | 预览资源/js/pivot.js:82 |
| `动效／局部常量` | `holdTimer: 0,` | `pivot` | 预览资源/js/pivot.js:87 |
| `动效／局部常量` | `pointerId: -1,` | `pivot` | 预览资源/js/pivot.js:88 |
| `动效／局部常量` | `originX: 0,` | `pivot` | 预览资源/js/pivot.js:89 |
| `动效／局部常量` | `originY: 0,` | `pivot` | 预览资源/js/pivot.js:90 |
| `动效／局部常量` | `targetX: 0,` | `pivot` | 预览资源/js/pivot.js:91 |
| `动效／局部常量` | `targetY: 0,` | `pivot` | 预览资源/js/pivot.js:92 |
| `动效／局部常量` | `curX: 0,` | `pivot` | 预览资源/js/pivot.js:93 |
| `动效／局部常量` | `curY: 0,` | `pivot` | 预览资源/js/pivot.js:94 |
| `动效／局部常量` | `grasp: 0,` | `pivot` | 预览资源/js/pivot.js:95 |
| `动效／局部常量` | `keyBoost: 0,` | `pivot` | 预览资源/js/pivot.js:96 |
| `动效／局部常量` | `lastT: 0,` | `pivot` | 预览资源/js/pivot.js:97 |
| `动效／局部常量` | `let y = el.offsetHeight * 0.5;` | `pivot` | 预览资源/js/pivot.js:106 |
| `动效／局部常量` | `const starCount = Math.round(Math.max(60, cw * ch / 10000));` | `pivot` | 预览资源/js/pivot.js:136 |
| `动效／局部常量` | `const orbital = i < Math.round(starCount * .35);` | `pivot` | 预览资源/js/pivot.js:138 |
| `动效／局部常量` | `rRatio: orbital ? (0.14 + (i / Math.max(1, Math.round(starCount * .35))) * 0.44 + (Math.random() - 0.5) * 0.025) : 0,` | `pivot` | 预览资源/js/pivot.js:141 |
| `动效／局部常量` | `tilt: 0.5 + Math.random() * 0.36,` | `pivot` | 预览资源/js/pivot.js:146 |
| `动效／局部常量` | `alpha: 0.32 + Math.random() * 0.44,` | `pivot` | 预览资源/js/pivot.js:149 |
| `动效／局部常量` | `twinkleFreq: .35 + Math.random() * 1.1,` | `pivot` | 预览资源/js/pivot.js:150 |
| `动效／局部常量` | `ox: 0, oy: 0, moveBoost: 0,` | `pivot` | 预览资源/js/pivot.js:154 |
| `动效／局部常量` | `const span = Math.min(cw, ch) \|\| 900;` | `pivot` | 预览资源/js/pivot.js:160 |
| `动效／局部常量` | `let idx = 0;` | `pivot` | 预览资源/js/pivot.js:166 |
| `动效／局部常量` | `for (let band = 0; band < BANDS.length; band++) {` | `pivot` | 预览资源/js/pivot.js:167 |
| `动效／局部常量` | `for (let j = 0; j < b.count; j++, idx++) {` | `pivot` | 预览资源/js/pivot.js:169 |
| `动效／局部常量` | `const u = (j + 0.5) / b.count;` | `pivot` | 预览资源/js/pivot.js:171 |
| `动效／局部常量` | `const rBase = b.rMin + ((j * 0.618034) % 1) * (b.rMax - b.rMin);` | `pivot` | 预览资源/js/pivot.js:172 |
| `动效／局部常量` | `const tier = idx % 7 === 0 ? 1.22 : (idx % 2 === 0 ? 0.96 : 0.75);` | `pivot` | 预览资源/js/pivot.js:173 |
| `动效／局部常量` | `rRatio: rBase,` | `pivot` | 预览资源/js/pivot.js:179 |
| `动效／局部常量` | `rotOffset: 0,` | `pivot` | 预览资源/js/pivot.js:181 |
| `动效／局部常量` | `bobAmp: .35 + Math.random() * .5,` | `pivot` | 预览资源/js/pivot.js:187 |
| `动效／局部常量` | `bobFreq: 0.52 + Math.random() * 0.48,` | `pivot` | 预览资源/js/pivot.js:188 |
| `动效／局部常量` | `spinSpeed: .035 * (idx % 2 ? 1 : -1),` | `pivot` | 预览资源/js/pivot.js:191 |
| `动效／局部常量` | `ox: 0, oy: 0, moveBoost: 0,` | `pivot` | 预览资源/js/pivot.js:194 |
| `动效／局部常量` | `const nextDpr = Math.min(window.devicePixelRatio \|\| 1, 2);` | `pivot` | 预览资源/js/pivot.js:203 |
| `动效／局部常量` | `for (let i = 0; i <= sides; i++) {` | `pivot` | 预览资源/js/pivot.js:234 |
| `动效／局部常量` | `const a = rot + (i / sides) * Math.PI * 2;` | `pivot` | 预览资源/js/pivot.js:235 |
| `动效／局部常量` | `const baseR = Math.min(cw, ch) * 0.36;` | `pivot` | 预览资源/js/pivot.js:245 |
| `动效／局部常量` | `const rot12 = lusterAngle * 0.12;` | `pivot` | 预览资源/js/pivot.js:264 |
| `动效／局部常量` | `for (let i = 0; i < 12; i++) {` | `pivot` | 预览资源/js/pivot.js:273 |
| `动效／局部常量` | `const a = rot12 + (i / 12) * Math.PI * 2;` | `pivot` | 预览资源/js/pivot.js:274 |
| `动效／局部常量` | `const rIn = baseR * 0.84;` | `pivot` | 预览资源/js/pivot.js:275 |
| `动效／局部常量` | `const rOut = baseR + (i % 3 === 0 ? 5 : 2);` | `pivot` | 预览资源/js/pivot.js:276 |
| `动效／局部常量` | `const rot8 = -lusterAngle * 0.08;` | `pivot` | 预览资源/js/pivot.js:284 |
| `动效／局部常量` | `for (let i = 0; i < 12; i++) {` | `pivot` | 预览资源/js/pivot.js:332 |
| `动效／局部常量` | `const a = (i / 12) * Math.PI * 2;` | `pivot` | 预览资源/js/pivot.js:333 |
| `动效／局部常量` | `const r0 = baseR - (i % 3 === 0 ? 10 : 4.5);` | `pivot` | 预览资源/js/pivot.js:334 |
| `动效／局部常量` | `let targetOx = 0, targetOy = 0, holdDampen = 1;` | `pivot` | 预览资源/js/pivot.js:353 |
| `动效／局部常量` | `const w = Math.pow(1 - dist / 265, 1.45);` | `pivot` | 预览资源/js/pivot.js:359 |
| `动效／局部常量` | `const pull = Math.min(dist * 0.46, 58 * w);` | `pivot` | 预览资源/js/pivot.js:360 |
| `动效／局部常量` | `holdDampen = 1 - 0.85 * w;` | `pivot` | 预览资源/js/pivot.js:364 |
| `动效／局部常量` | `const w = Math.pow(1 - dist / 125, 1.6);` | `pivot` | 预览资源/js/pivot.js:366 |
| `动效／局部常量` | `const push = 13 * w;` | `pivot` | 预览资源/js/pivot.js:367 |
| `动效／局部常量` | `const rate = Math.min(1, dt * (bgPointer.down ? 5.6 : 4.6));` | `pivot` | 预览资源/js/pivot.js:372 |
| `动效／局部常量` | `const stepSpeed = Math.hypot(m.ox - prevOx, m.oy - prevOy) / Math.max(0.008, dt);` | `pivot` | 预览资源/js/pivot.js:376 |
| `动效／局部常量` | `m.moveBoost = Math.min(0.28, stepSpeed * 0.0022 + (1 - holdDampen) * 0.16);` | `pivot` | 预览资源/js/pivot.js:377 |
| `动效／局部常量` | `return holdDampen;` | `pivot` | 预览资源/js/pivot.js:378 |
| `动效／局部常量` | `const tSec = now * 0.001;` | `pivot` | 预览资源/js/pivot.js:384 |
| `动效／局部常量` | `const rx = span * m.rRatio;` | `pivot` | 预览资源/js/pivot.js:389 |
| `动效／局部常量` | `const holdDampen = applyPointerField(m, estX, estY, dt);` | `pivot` | 预览资源/js/pivot.js:400 |
| `动效／局部常量` | `m.angle += m.speed * holdDampen * dt * (state.replying ? 1.8 : 1);` | `pivot` | 预览资源/js/pivot.js:404 |
| `动效／局部常量` | `const rx = span * m.rRatio;` | `pivot` | 预览资源/js/pivot.js:405 |
| `动效／局部常量` | `m.ny = (m.ny + m.speed * holdDampen * dt + 1) % 1;` | `pivot` | 预览资源/js/pivot.js:413 |
| `动效／局部常量` | `const twinkle = 0.62 + 0.38 * Math.sin(tSec * m.twinkleFreq + m.twinklePhase);` | `pivot` | 预览资源/js/pivot.js:421 |
| `动效／局部常量` | `const boost = m.moveBoost \|\| 0;` | `pivot` | 预览资源/js/pivot.js:422 |
| `动效／局部常量` | `const alpha = Math.min(0.92, m.alpha * twinkle + boost);` | `pivot` | 预览资源/js/pivot.js:423 |
| `动效／局部常量` | `const r = m.size + boost * 1.2;` | `pivot` | 预览资源/js/pivot.js:425 |
| `动效／局部常量` | `const haloR = r * 3.4;` | `pivot` | 预览资源/js/pivot.js:427 |
| `动效／局部常量` | `const g = ctx.createRadialGradient(px, py, 0, px, py, haloR);` | `pivot` | 预览资源/js/pivot.js:428 |
| `动效／局部常量` | `const arm = r * 3.1;` | `pivot` | 预览资源/js/pivot.js:442 |
| `动效／局部常量` | `const haloR = s * (night ? 2.8 + flash * .65 : 1.8 + flash * .45);` | `pivot` | 预览资源/js/pivot.js:462 |
| `动效／局部常量` | `const g = ctx.createRadialGradient(px, py, s * 0.1, px, py, haloR);` | `pivot` | 预览资源/js/pivot.js:463 |
| `动效／局部常量` | `const litStr = flash > 0.35` | `pivot` | 预览资源/js/pivot.js:479 |
| `动效／局部常量` | `const midStr = 'rgba(${mid[0]},${mid[1]},${mid[2]},${alpha * 0.94})';` | `pivot` | 预览资源/js/pivot.js:482 |
| `动效／局部常量` | `const shadeStr = 'rgba(${shade[0]},${shade[1]},${shade[2]},${alpha * (night ? 0.88 : 0.92)})';` | `pivot` | 预览资源/js/pivot.js:483 |
| `动效／局部常量` | `const outerEdgeStr = 'rgba(${edge[0]},${edge[1]},${edge[2]},${alpha * (night ? 0.72 : 0.66)})';` | `pivot` | 预览资源/js/pivot.js:484 |
| `动效／局部常量` | `const ridgeStr = 'rgba(${rgb.hi[0]},${rgb.hi[1]},${rgb.hi[2]},${alpha * (0.62 + flash * 0.32)})';` | `pivot` | 预览资源/js/pivot.js:485 |
| `动效／局部常量` | `const L = s * 1.76, W = s * 0.88, waist = L * 0.08;` | `pivot` | 预览资源/js/pivot.js:489 |
| `动效／局部常量` | `const L = s * 1.68, W = s * 0.75, shoulder = L * 0.45;` | `pivot` | 预览资源/js/pivot.js:518 |
| `动效／局部常量` | `const L = s * 1.6, W = s * 0.85;` | `pivot` | 预览资源/js/pivot.js:548 |
| `动效／局部常量` | `const glintLen = s * (1.3 + flash * 1.05);` | `pivot` | 预览资源/js/pivot.js:569 |
| `动效／局部常量` | `const tSec = now * 0.001;` | `pivot` | 预览资源/js/pivot.js:585 |
| `动效／局部常量` | `const perspective = Math.min(1, 750 / span);` | `pivot` | 预览资源/js/pivot.js:586 |
| `动效／局部常量` | `const speedBoost = 1 + pv.grasp * 1.5 + pv.keyBoost * 1.2 + (state.replying ? 0.8 : 0);` | `pivot` | 预览资源/js/pivot.js:588 |
| `动效／局部常量` | `for (const [radius, width, alpha] of [[.399, 9, night ? .06 : .035], [.399, .8, night ? .28 : .15]]) {` | `pivot` | 预览资源/js/pivot.js:593 |
| `动效／局部常量` | `const rot = ringRotation + m.rotOffset + pv.curX * 0.0035;` | `pivot` | 预览资源/js/pivot.js:602 |
| `动效／局部常量` | `const tilt = Math.max(.08, Math.min(.62, m.tilt * perspective + pv.curY * .003));` | `pivot` | 预览资源/js/pivot.js:603 |
| `动效／局部常量` | `const rx = span * m.rRatio;` | `pivot` | 预览资源/js/pivot.js:605 |
| `动效／局部常量` | `const bob0 = Math.sin(tSec * m.bobFreq + m.phase) * m.bobAmp;` | `pivot` | 预览资源/js/pivot.js:609 |
| `动效／局部常量` | `const lx0 = Math.cos(m.angle) * rx;` | `pivot` | 预览资源/js/pivot.js:610 |
| `动效／局部常量` | `const ly0 = Math.sin(m.angle) * ry + m.bandDz + bob0;` | `pivot` | 预览资源/js/pivot.js:611 |
| `动效／局部常量` | `const estX = m.distant ? m.nx * cw : centerX + lx0 * cr - ly0 * sr;` | `pivot` | 预览资源/js/pivot.js:612 |
| `动效／局部常量` | `const estY = m.distant ? m.ny * ch : centerY + lx0 * sr + ly0 * cr;` | `pivot` | 预览资源/js/pivot.js:613 |
| `动效／局部常量` | `const holdDampen = applyPointerField(m, estX, estY, dt);` | `pivot` | 预览资源/js/pivot.js:614 |
| `动效／局部常量` | `m.angle = (m.angle + m.speed * speedBoost * holdDampen * dt) % (Math.PI * 2);` | `pivot` | 预览资源/js/pivot.js:616 |
| `动效／局部常量` | `m.spin += m.spinSpeed * holdDampen * dt;` | `pivot` | 预览资源/js/pivot.js:617 |
| `动效／局部常量` | `const persp = 0.84 + 0.24 * ((depth + 1) * 0.5);` | `pivot` | 预览资源/js/pivot.js:629 |
| `动效／局部常量` | `const behindCore = (hubOnHero && depth < 0 && distFromCore < 46)` | `pivot` | 预览资源/js/pivot.js:631 |
| `动效／局部常量` | `const boost = m.moveBoost \|\| 0;` | `pivot` | 预览资源/js/pivot.js:635 |
| `动效／局部常量` | `const flash = Math.pow(Math.max(0, Math.cos(m.spin * 2 + m.phase)), 10);` | `pivot` | 预览资源/js/pivot.js:636 |
| `动效／局部常量` | `const alpha = Math.min(0.95,` | `pivot` | 预览资源/js/pivot.js:637 |
| `动效／局部常量` | `const s = (m.size + boost * 0.4) * persp * (night ? 1 : 1.1);` | `pivot` | 预览资源/js/pivot.js:642 |
| `动效／局部常量` | `const dt = Math.min(0.05, lastFieldTime ? (now - lastFieldTime) / 1000 : 0.016);` | `pivot` | 预览资源/js/pivot.js:650 |
| `动效／局部常量` | `const centerX = hubX + pv.curX * 0.45;` | `pivot` | 预览资源/js/pivot.js:658 |
| `动效／局部常量` | `const centerY = hubY + pv.curY * 0.45;` | `pivot` | 预览资源/js/pivot.js:659 |
| `动效／局部常量` | `const charge = Math.min(1, bgPointer.holdTime / 1.1);` | `pivot` | 预览资源/js/pivot.js:670 |
| `动效／局部常量` | `const col = cool ? (charge > 0.55 ? rgb.a : rgb.p) : rgb.goldMid;` | `pivot` | 预览资源/js/pivot.js:671 |
| `动效／局部常量` | `const pulseR = 18 + charge * 40 + Math.sin(now * 0.006) * 2.5;` | `pivot` | 预览资源/js/pivot.js:672 |
| `动效／局部常量` | `const g = ctx.createRadialGradient(bgPointer.x, bgPointer.y, 0, bgPointer.x, bgPointer.y, pulseR * 1.5);` | `pivot` | 预览资源/js/pivot.js:673 |
| `动效／局部常量` | `for (let i = waves.length - 1; i >= 0; i--) {` | `pivot` | 预览资源/js/pivot.js:701 |
| `动效／局部常量` | `const ease = w.crystalWave ? (1 - Math.pow(1 - t, 2.7)) : (1 - Math.pow(1 - t, 2.4));` | `pivot` | 预览资源/js/pivot.js:705 |
| `动效／局部常量` | `const fade = Math.pow(1 - t, 1.5) * w.strength;` | `pivot` | 预览资源/js/pivot.js:707 |
| `动效／局部常量` | `const c2 = w.colors.a;` | `pivot` | 预览资源/js/pivot.js:732 |
| `动效／局部常量` | `r0: 4,` | `pivot` | 预览资源/js/pivot.js:804 |
| `动效／局部常量` | `strength: 0.5,` | `pivot` | 预览资源/js/pivot.js:806 |
| `动效／局部常量` | `r0: 14,` | `pivot` | 预览资源/js/pivot.js:820 |
| `动效／局部常量` | `let spotRAF = 0, spotX = 0, spotY = 0, spotTarget = null, lastSpotEl = null;` | `pivot` | 预览资源/js/pivot.js:838 |
| `动效／局部常量` | `let pivotRAF = 0;` | `pivot` | 预览资源/js/pivot.js:857 |
| `动效／局部常量` | `const ns = 'http://www.w3.org/2000/svg';` | `pivot` | 预览资源/js/pivot.js:867 |
| `动效／局部常量` | `for (let i = 0; i < 24; i++) {` | `pivot` | 预览资源/js/pivot.js:868 |
| `动效／局部常量` | `const a = (i / 24) * Math.PI * 2;` | `pivot` | 预览资源/js/pivot.js:869 |
| `动效／局部常量` | `const major = i % 6 === 0;` | `pivot` | 预览资源/js/pivot.js:870 |
| `动效／局部常量` | `const mid = i % 3 === 0;` | `pivot` | 预览资源/js/pivot.js:871 |
| `动效／局部常量` | `const r1 = major ? 85 : mid ? 87.5 : 89.2;` | `pivot` | 预览资源/js/pivot.js:872 |
| `动效／局部常量` | `const r2 = major ? 95.5 : 92.5;` | `pivot` | 预览资源/js/pivot.js:873 |
| `动效／局部常量` | `let settleTimer = 0;` | `pivot` | 预览资源/js/pivot.js:907 |
| `动效／局部常量` | `settleTimer = setTimeout(updateHubCenter, 360);` | `pivot` | 预览资源/js/pivot.js:912 |
| `动效／局部常量` | `const ns = 'http://www.w3.org/2000/svg';` | `pivot` | 预览资源/js/pivot.js:942 |
| `动效／局部常量` | `const beads = [0, 1, 2, 3].map(idx => {` | `pivot` | 预览资源/js/pivot.js:945 |
| `动效／局部常量` | `pv.holdTimer = setTimeout(() => {` | `pivot` | 预览资源/js/pivot.js:976 |
| `动效／局部常量` | `r0: 32,` | `pivot` | 预览资源/js/pivot.js:1004 |
| `动效／局部常量` | `strength: 0.9 + power * 0.32,` | `pivot` | 预览资源/js/pivot.js:1008 |
| `动效／局部常量` | `let merAngle = 0, breathePhase = 0, swayPhase = 0, coreScale = 1, coreAngle = 0;` | `pivot` | 预览资源/js/pivot.js:1018 |
| `动效／局部常量` | `const dt = Math.min(0.05, pv.lastT ? (now - pv.lastT) / 1000 : 0.016);` | `pivot` | 预览资源/js/pivot.js:1021 |
| `动效／局部常量` | `const tempo = N.REDUCED ? 0 : (cool ? 1.18 : 0.88) / Math.max(0.65, N.motionScale());` | `pivot` | 预览资源/js/pivot.js:1025 |
| `动效／局部常量` | `const targetGrasp = pv.engaged ? 1 : 0;` | `pivot` | 预览资源/js/pivot.js:1026 |
| `动效／局部常量` | `const damp = Math.min(1, dt * (cool ? 9.5 : 7.5));` | `pivot` | 预览资源/js/pivot.js:1030 |
| `动效／局部常量` | `const sc = 1 + pv.grasp * 0.045;` | `pivot` | 预览资源/js/pivot.js:1035 |
| `动效／局部常量` | `const breathe = 1 + Math.sin(breathePhase) * (cool ? 0.032 : 0.028) + pv.grasp * 0.05;` | `pivot` | 预览资源/js/pivot.js:1050 |
| `动效／局部常量` | `const sway = cool ? 0 : Math.sin(swayPhase) * 2.4 + pv.curX * 0.16;` | `pivot` | 预览资源/js/pivot.js:1051 |
| `动效／局部常量` | `const blend = Math.min(1, dt * 8);` | `pivot` | 预览资源/js/pivot.js:1052 |
| `动效／局部常量` | `const speedMul = tempo * (1 + pv.grasp * 2.8 + pv.keyBoost * 2.4 + (state.replying ? 1.15 : 0));` | `pivot` | 预览资源/js/pivot.js:1059 |
| `动效／局部常量` | `const rot = orb.baseRot + pv.curX * (0.45 - idx * 0.18) - pv.grasp * (idx === 1 ? 8 : -5);` | `pivot` | 预览资源/js/pivot.js:1063 |
| `动效／局部常量` | `const tiltScale = Math.max(0.45, Math.min(1.45, 1 + pv.curY * 0.018 * (idx % 2 ? -1 : 1)));` | `pivot` | 预览资源/js/pivot.js:1064 |
| `动效／局部常量` | `const tf = 'rotate(${rot.toFixed(2)}) scale(1, ${tiltScale.toFixed(3)})';` | `pivot` | 预览资源/js/pivot.js:1065 |
| `动效／局部常量` | `const inFront = Math.sin(theta) >= 0;` | `pivot` | 预览资源/js/pivot.js:1080 |
| `动效／局部常量` | `const depthScale = 0.84 + 0.24 * ((Math.sin(theta) + 1) * 0.5);` | `pivot` | 预览资源/js/pivot.js:1081 |
| `动效／局部常量` | `{ duration: N.DUR.move, easing: N.SP.settle.e });` | `pivot` | 预览资源/js/pivot.js:1109 |
| `动效／局部常量` | `let lastKeyWave = 0;` | `pivot` | 预览资源/js/pivot.js:1113 |
| `动效／局部常量` | `r0: 6, maxR: 76, dur: 900, strength: 0.4,` | `pivot` | 预览资源/js/pivot.js:1125 |
| `动效／局部常量` | `const y = detail.y ?? window.innerHeight * 0.82;` | `pivot` | 预览资源/js/pivot.js:1135 |
| `动效／局部常量` | `const title = document.createElement('h2'); title.id = titleId;` | `plugin-pages` | 预览资源/js/plugin-pages.js:148 |
| `动效／局部常量` | `if (fresh && spec.animate && !N.REDUCED) N.anim(node, [{ opacity: 0, transform: 'translateY(16px)' }, { opacity: 1, transform: 'none' }], { duration: N.DUR.content, easing: N.EASE_ENTER });` | `plugin-pages` | 预览资源/js/plugin-pages.js:290 |
| `动效／局部常量` | `const animation = N.anim(node, [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(-8px)' }], { duration: N.DUR.micro, easing: N.EASE_EXIT });` | `plugin-pages` | 预览资源/js/plugin-pages.js:296 |
| `动效／局部常量` | `let managerScrollTop = 0;` | `plugins` | 预览资源/js/plugins.js:11 |
| `动效／局部常量` | `let watchTimer = 0;` | `plugins` | 预览资源/js/plugins.js:61 |
| `动效／局部常量` | `let tries = 0;` | `plugins` | 预览资源/js/plugins.js:73 |
| `动效／局部常量` | `watchTimer = setTimeout(tick, 500);` | `plugins` | 预览资源/js/plugins.js:98 |
| `动效／局部常量` | `watchTimer = setTimeout(tick, 400);` | `plugins` | 预览资源/js/plugins.js:100 |
| `动效／局部常量` | `const detailTop = samePlugin ? $('.plug-detail-scroll', currentDetail)?.scrollTop \|\| 0 : 0;` | `plugins` | 预览资源/js/plugins.js:111 |
| `动效／局部常量` | `const shown = pv.more ? list : list.slice(0, 4);` | `plugins` | 预览资源/js/plugins.js:379 |
| `动效／局部常量` | `let requestVersion = 0;` | `plugins` | 预览资源/js/plugins.js:438 |
| `动效／局部常量` | `{ duration: N.DUR.exit, easing: N.EASE_EXIT, fill: 'forwards' });` | `plugins` | 预览资源/js/plugins.js:508 |
| `动效／局部常量` | `let selectedPath = '', autoName = '', editing = false, saving = false, picking = false, generation = 0;` | `projects` | 预览资源/js/projects.js:8 |
| `动效／局部常量` | `let providerVersion = 0, mcpVersion = 0;` | `settings` | 预览资源/js/settings.js:11 |
| `动效／局部常量` | `], { duration: N.SP.bouncy.d, delay: i * 50, easing: N.SP.bouncy.e });` | `settings` | 预览资源/js/settings.js:61 |
| `动效／局部常量` | `setTimeout(() => $('#p-name').focus(), 40);` | `settings` | 预览资源/js/settings.js:268 |
| `动效／局部常量` | `const first = state.providers[0];` | `settings` | 预览资源/js/settings.js:319 |
| `动效／局部常量` | `const model = first && (first.models \|\| [])[0];` | `settings` | 预览资源/js/settings.js:320 |
| `动效／局部常量` | `{ duration: N.DUR.base, easing: N.SP.settle.e });` | `settings` | 预览资源/js/settings.js:366 |
| `动效／局部常量` | `setTimeout(() => { if (!scrim.classList.contains('show')) scrim.hidden = true; }, N.DUR.exit * N.motionScale() + 20);` | `shell` | 预览资源/js/shell.js:32 |
| `动效／局部常量` | `const pendingReq = () => state.requests.length + state.works.reduce((n, w) => n + (w.pending_interactions?.length \|\| 0), 0);` | `shell` | 预览资源/js/shell.js:37 |
| `动效／局部常量` | `const a = activeWorks().slice(0, 3);` | `shell` | 预览资源/js/shell.js:59 |
| `动效／局部常量` | `const done = state.works.filter(w => w.state === 'completed').slice(0, 4);` | `shell` | 预览资源/js/shell.js:86 |
| `动效／局部常量` | `const ico = d.tone === 'jade' ? icon('check', 'i sm') : '<span class="orb-mini" style="--s:16px" aria-hidden="true"></span>';` | `shell` | 预览资源/js/shell.js:121 |
| `动效／局部常量` | `], { duration: closing ? 420 : SP.island.d, easing: closing ? N.EASE_OUT : SP.island.e, fill: 'forwards' });` | `shell` | 预览资源/js/shell.js:183 |
| `动效／局部常量` | `], { duration: closing ? 130 : 160, easing: N.EASE_OUT, fill: 'forwards' }).onfinish = () => ghost.remove();` | `shell` | 预览资源/js/shell.js:187 |
| `动效／局部常量` | `], { duration: closing ? 280 : 420, delay: prev === mode ? 0 : closing ? 25 : 55, easing: N.EASE_OUT, fill: 'backwards' });` | `shell` | 预览资源/js/shell.js:191 |
| `动效／局部常量` | `], { duration: SP.soft.d, delay: 90 + i * 32, easing: SP.soft.e, fill: 'backwards' });` | `shell` | 预览资源/js/shell.js:201 |
| `动效／局部常量` | `islandAnim = anim(island, [{ width: from + 'px' }, { width: to + 'px' }], { duration: SP.island.d, easing: SP.island.e });` | `shell` | 预览资源/js/shell.js:239 |
| `动效／局部常量` | `const keepTop = sc ? sc.scrollTop : 0;` | `shell` | 预览资源/js/shell.js:267 |
| `动效／局部常量` | `const keepThreadTop = tSc ? tSc.scrollTop : 0;` | `shell` | 预览资源/js/shell.js:269 |
| `动效／局部常量` | `toastTimer = setTimeout(() => { if (island.mode === 'toast') morphIsland('compact'); }, ms);` | `shell` | 预览资源/js/shell.js:295 |
| `动效／局部常量` | `toastTimer = setTimeout(() => t.classList.remove('show'), ms);` | `shell` | 预览资源/js/shell.js:300 |
| `动效／局部常量` | `let viewVersion = 0;` | `shell` | 预览资源/js/shell.js:326 |
| `动效／局部常量` | `], { duration: SP.soft.d, easing: SP.soft.e });` | `shell` | 预览资源/js/shell.js:384 |
| `动效／局部常量` | `], { duration: 150, easing: 'ease-in' });` | `shell` | 预览资源/js/shell.js:394 |
| `动效／局部常量` | `const x0 = a.left + a.width / 2, y0 = a.top + a.height / 2, x1 = b.left + b.width / 2, y1 = b.top + b.height / 2;` | `shell` | 预览资源/js/shell.js:427 |
| `动效／局部常量` | `const cx = x0 + (x1 - x0) * 0.25, cy = Math.min(y0, y1) + (Math.max(y0, y1) - Math.min(y0, y1)) * 0.15 - 60;` | `shell` | 预览资源/js/shell.js:428 |
| `动效／局部常量` | `const NSTEP = 28, kf = [];` | `shell` | 预览资源/js/shell.js:432 |
| `动效／局部常量` | `for (let i = 0; i <= NSTEP; i++) {` | `shell` | 预览资源/js/shell.js:433 |
| `动效／局部常量` | `const t = i / NSTEP, u = 1 - t;` | `shell` | 预览资源/js/shell.js:434 |
| `动效／局部常量` | `const x = u * u * x0 + 2 * u * t * cx + t * t * x1;` | `shell` | 预览资源/js/shell.js:435 |
| `动效／局部常量` | `const y = u * u * y0 + 2 * u * t * cy + t * t * y1;` | `shell` | 预览资源/js/shell.js:436 |
| `动效／局部常量` | `const dx = 2 * u * (cx - x0) + 2 * t * (x1 - cx);` | `shell` | 预览资源/js/shell.js:437 |
| `动效／局部常量` | `const dy = 2 * u * (cy - y0) + 2 * t * (y1 - cy);` | `shell` | 预览资源/js/shell.js:438 |
| `动效／局部常量` | `const ang = Math.atan2(dy, dx) * 180 / Math.PI;` | `shell` | 预览资源/js/shell.js:439 |
| `动效／局部常量` | `const stretch = 1 + Math.sin(t * Math.PI) * 0.9, sc = 1 - t * 0.35;` | `shell` | 预览资源/js/shell.js:440 |
| `动效／局部常量` | `const an = d.animate(kf, { duration: N.REDUCED ? 1 : 820 * N.motionScale(), easing: 'cubic-bezier(.55,0,.25,1)' });` | `shell` | 预览资源/js/shell.js:443 |
| `动效／局部常量` | `const x = r ? r.left + r.width / 2 : innerWidth / 2;` | `shell` | 预览资源/js/shell.js:455 |
| `动效／局部常量` | `const y = r ? r.top + r.height / 2 : 42;` | `shell` | 预览资源/js/shell.js:456 |
| `动效／局部常量` | `const frames = { '--theme-reveal-radius': ['-24px', (rad + 24) + 'px'] };` | `shell` | 预览资源/js/shell.js:484 |
| `动效／局部常量` | `const timing = { duration: N.MOTION.theme * N.motionScale(), easing: 'ease-in-out', fill: 'both' };` | `shell` | 预览资源/js/shell.js:485 |
| `动效／局部常量` | `if (island.mode === 'toast') toastTimer = setTimeout(() => island.mode === 'toast' && morphIsland('compact'), 1200);` | `shell` | 预览资源/js/shell.js:604 |
| `动效／局部常量` | `const first = focusable[0], last = focusable.at(-1);` | `shell` | 预览资源/js/shell.js:669 |
| `动效／局部常量` | `let rz = 0;` | `shell` | 预览资源/js/shell.js:692 |
| `动效／局部常量` | `let editorVersion = 0;` | `skills` | 预览资源/js/skills.js:11 |
| `动效／局部常量` | `{ duration: N.DUR.exit, easing: N.EASE_EXIT, fill: 'forwards' }).finished;` | `skills` | 预览资源/js/skills.js:139 |
| `动效／局部常量` | `const pendingCount = w => Array.isArray(w.pending_interactions) ? w.pending_interactions.length : 0;` | `work` | 预览资源/js/work.js:16 |
| `动效／局部常量` | `{ duration: N.DUR.base, easing: N.SP.settle.e });` | `work` | 预览资源/js/work.js:114 |
| `动效／局部常量` | `const from = body.hidden ? 0 : body.getBoundingClientRect().height;` | `work` | 预览资源/js/work.js:129 |
| `动效／局部常量` | `const to = next ? body.scrollHeight : 0;` | `work` | 预览资源/js/work.js:133 |
| `动效／局部常量` | `if (next) N.enterItems([...body.children], section, { profile: 'content', delay: 0 });` | `work` | 预览资源/js/work.js:135 |
| `动效／局部常量` | `], { duration: next ? N.SP.soft.d : 220, easing: next ? N.SP.soft.e : N.EASE_OUT });` | `work` | 预览资源/js/work.js:140 |
| `动效／局部常量` | `{ duration: N.DUR.exit, easing: N.EASE_EXIT, fill: 'forwards' });` | `work` | 预览资源/js/work.js:172 |
| `动效／局部常量` | `{ duration: N.DUR.base, easing: N.SP.settle.e });` | `work` | 预览资源/js/work.js:186 |
| `动效／局部常量` | `{ duration: N.DUR.exit, easing: N.EASE_EXIT, fill: 'forwards' });` | `work` | 预览资源/js/work.js:212 |
| `动效／局部常量` | `let detailVersion = 0;` | `work` | 预览资源/js/work.js:332 |
