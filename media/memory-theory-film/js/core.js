/* 核心工具：时间轴、缓动、确定性随机、绘制辅助。
 * 全片所有画面都是时间 t 的纯函数，便于任意跳转与逐帧导出。 */
(function () {
  const M = (window.MF = window.MF || {});
  M.W = 1920;
  M.H = 1080;

  // 80 BPM：一拍 0.75s，一小节 3s。场景边界落在小节线上，音乐与画面共用同一张时间表。
  M.BPM = 80;
  M.BEAT = 60 / M.BPM;
  M.BAR = M.BEAT * 4;
  M.SCENES = [
    { id: 'prologue', t0: 0, t1: 12, title: '' },
    { id: 'archive', t0: 12, t1: 27, title: '存储，不等于使用' },
    { id: 'events', t0: 27, t1: 48, title: '来源与事件' },
    { id: 'clusters', t0: 48, t1: 69, title: '记忆团 · 背景概览' },
    { id: 'network', t0: 69, t1: 90, title: '关联网络' },
    { id: 'activation', t0: 90, t1: 120, title: '情境 · 激活 · 概率' },
    { id: 'quantities', t0: 120, t1: 138, title: '四个量，分别表达' },
    { id: 'revision', t0: 138, t1: 159, title: '巩固与修订' },
    { id: 'branches', t0: 159, t1: 177, title: '一个个体，多个分支' },
    { id: 'projection', t0: 177, t1: 192, title: '派生的投影' },
    { id: 'final', t0: 192, t1: 225, title: '最终形态' },
  ];
  M.DURATION = 225;

  M.clamp = (x, a = 0, b = 1) => (x < a ? a : x > b ? b : x);
  M.lerp = (a, b, t) => a + (b - a) * t;
  M.prog = (t, a, b) => M.clamp((t - a) / (b - a));
  M.smooth = (t) => {
    t = M.clamp(t);
    return t * t * (3 - 2 * t);
  };
  M.ease = {
    inOut: (t) => {
      t = M.clamp(t);
      return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
    },
    out: (t) => 1 - Math.pow(1 - M.clamp(t), 3),
    in: (t) => Math.pow(M.clamp(t), 3),
    outExpo: (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * M.clamp(t))),
    outBack: (t) => {
      t = M.clamp(t);
      const c1 = 1.70158, c3 = c1 + 1;
      return 1 + c3 * Math.pow(t - 1, 3) + c1 * Math.pow(t - 1, 2);
    },
    inOutSine: (t) => -(Math.cos(Math.PI * M.clamp(t)) - 1) / 2,
  };
  // 淡入淡出窗口：[a,b] 内为 1，两端各用 fi/fo 秒平滑过渡
  M.win = (t, a, b, fi = 0.8, fo = 0.8) =>
    M.smooth((t - a) / fi) * M.smooth((b - t) / fo);

  M.rng = function (seed) {
    let s = seed >>> 0;
    return function () {
      s = (s + 0x6d2b79f5) >>> 0;
      let r = Math.imul(s ^ (s >>> 15), 1 | s);
      r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r;
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  };
  M.hash = (n) => {
    const x = Math.sin(n * 127.1 + 311.7) * 43758.5453123;
    return x - Math.floor(x);
  };
  M.noise1 = (x, seed = 0) => {
    const i = Math.floor(x), f = x - i;
    const a = M.hash(i + seed * 57.3), b = M.hash(i + 1 + seed * 57.3);
    const u = f * f * (3 - 2 * f);
    return a + (b - a) * u;
  };
  M.gauss = (r) => {
    const u = Math.max(1e-9, r()), v = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  };

  M.rgba = (c, a) => `rgba(${c[0]},${c[1]},${c[2]},${M.clamp(a)})`;
  M.mixC = (a, b, t) => [
    Math.round(M.lerp(a[0], b[0], t)),
    Math.round(M.lerp(a[1], b[1], t)),
    Math.round(M.lerp(a[2], b[2], t)),
  ];

  // 调色板
  M.C = {
    bg: [4, 6, 13],
    ink: [232, 238, 248],
    dim: [130, 145, 170],
    cyan: [94, 231, 255],
    teal: [79, 227, 193],
    gold: [255, 206, 112],
    coral: [255, 138, 107],
    blue: [106, 168, 255],
    violet: [180, 140, 255],
    magenta: [255, 95, 162],
    white: [255, 255, 255],
    gray: [92, 102, 120],
  };

  M.FONT = {
    serif: '"Noto Serif SC", "Source Han Serif SC", "STSong", "SimSun", serif',
    sans: '"Noto Sans SC", "Microsoft YaHei", "DengXian", sans-serif',
    mono: '"Cascadia Code", "Cascadia Mono", Consolas, monospace',
  };

  // ---------- 文本 ----------
  const widthCache = new Map();
  function charW(ctx, ch) {
    const k = ctx.font + '|' + ch;
    let w = widthCache.get(k);
    if (w === undefined) {
      w = ctx.measureText(ch).width;
      widthCache.set(k, w);
    }
    return w;
  }
  M.font = (ctx, size, fam = 'sans', weight = 400) => {
    ctx.font = `${weight} ${size}px ${M.FONT[fam]}`;
  };
  M.textWidth = (ctx, str, spacing = 0) => {
    let w = 0;
    for (const ch of str) w += charW(ctx, ch) + spacing;
    return w - (str.length ? spacing : 0);
  };
  /* 逐字显现的文本。reveal ∈ [0,1]；边界处的字带上浮与淡入。 */
  M.text = (ctx, str, x, y, o = {}) => {
    const {
      size = 28, fam = 'sans', weight = 400, color = M.C.ink, alpha = 1,
      align = 'center', reveal = 1, spacing = 0, soft = 4, rise = 10,
    } = o;
    if (alpha <= 0.003 || reveal <= 0) return 0;
    M.font(ctx, size, fam, weight);
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    const chars = [...str];
    const total = M.textWidth(ctx, str, spacing);
    let cx = align === 'center' ? x - total / 2 : align === 'right' ? x - total : x;
    const n = chars.length;
    const head = reveal * (n + soft);
    for (let i = 0; i < n; i++) {
      const ch = chars[i];
      const k = M.clamp((head - i) / soft);
      if (k > 0) {
        const e = M.ease.out(k);
        ctx.fillStyle = M.rgba(color, alpha * e);
        ctx.fillText(ch, cx, y + (1 - e) * rise);
      }
      cx += charW(ctx, ch) + spacing;
    }
    return total;
  };
  // 字幕：底部居中，跟随时间窗口淡入淡出，带柔和暗底衬托确保清晰可读
  M.caption = (ctx, t, str, a, b, o = {}) => {
    const w = M.win(t, a, b, 0.6, 0.6);
    if (w <= 0) return;
    const y = o.y || M.H - 92;
    const sz = o.size || 34;
    const fam = o.fam || 'sans';
    const weight = o.weight || 300;
    const spacing = o.spacing ?? 3;
    M.font(ctx, sz, fam, weight);
    const tw = M.textWidth(ctx, str, spacing);
    const padX = 36, padY = Math.round(sz * 0.68);
    ctx.save();
    ctx.fillStyle = M.rgba([3, 6, 14], 0.72 * w);
    M.roundRect(ctx, M.W / 2 - tw / 2 - padX, y - padY, tw + padX * 2, padY * 2, padY);
    ctx.fill();
    ctx.restore();
    M.text(ctx, str, M.W / 2, y, {
      size: sz, fam, weight,
      color: o.color || M.C.ink, alpha: w * (o.alpha || 0.96),
      reveal: M.clamp((t - a) / (o.typeDur || 0.55)), spacing,
    });
  };

  M.roundRect = (ctx, x, y, w, h, r) => {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  };

  M.glowDot = (ctx, x, y, r, c, a) => {
    if (a <= 0.003) return;
    const g = ctx.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, M.rgba(c, a));
    g.addColorStop(0.35, M.rgba(c, a * 0.35));
    g.addColorStop(1, M.rgba(c, 0));
    ctx.fillStyle = g;
    ctx.fillRect(x - r, y - r, r * 2, r * 2);
  };

  // 凸包 + 圆角外扩，用于绘制记忆团边界
  M.convexHull = (pts) => {
    if (pts.length < 3) return pts.slice();
    const p = pts.slice().sort((a, b) => a.x - b.x || a.y - b.y);
    const cross = (o, a, b) => (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
    const lo = [], up = [];
    for (const q of p) {
      while (lo.length >= 2 && cross(lo[lo.length - 2], lo[lo.length - 1], q) <= 0) lo.pop();
      lo.push(q);
    }
    for (let i = p.length - 1; i >= 0; i--) {
      const q = p[i];
      while (up.length >= 2 && cross(up[up.length - 2], up[up.length - 1], q) <= 0) up.pop();
      up.push(q);
    }
    up.pop(); lo.pop();
    return lo.concat(up);
  };
  M.hullPath = (ctx, pts, pad, grow = 1) => {
    const h = M.convexHull(pts);
    ctx.beginPath();
    if (h.length === 0) return;
    if (h.length === 1) {
      ctx.arc(h[0].x, h[0].y, pad * grow, 0, Math.PI * 2);
      return;
    }
    let cx = 0, cy = 0;
    for (const q of h) { cx += q.x; cy += q.y; }
    cx /= h.length; cy /= h.length;
    const n = h.length;
    let first = true;
    for (let i = 0; i < n; i++) {
      const prev = h[(i - 1 + n) % n], cur = h[i], next = h[(i + 1) % n];
      const a1 = Math.atan2(cur.y - prev.y, cur.x - prev.x) - Math.PI / 2;
      let a2 = Math.atan2(next.y - cur.y, next.x - cur.x) - Math.PI / 2;
      while (a2 < a1) a2 += Math.PI * 2;
      const px = cx + (cur.x - cx) * grow, py = cy + (cur.y - cy) * grow;
      for (let s = 0; s <= 6; s++) {
        const a = a1 + ((a2 - a1) * s) / 6;
        const x = px + Math.cos(a) * pad, y = py + Math.sin(a) * pad;
        if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
      }
    }
    ctx.closePath();
  };
})();
