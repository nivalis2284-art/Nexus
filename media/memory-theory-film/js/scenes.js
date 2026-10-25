/* 画面：相机、记忆世界的逐帧状态、绘制与各场景叠层。 */
(function () {
  const M = window.MF;
  const C = M.C, W = M.W, H = M.H, CX = W / 2, CY = H / 2;
  const E = M.events, S = M.sources, LK = M.links, N = E.length;
  const { lerp, clamp, prog, smooth, win, ease, hash, rgba } = M;
  const R = M.rng(777);
  const TM = (M.TIMING = {});

  // ======================= 时间表（音乐与画面共用） =======================
  // 档案网格
  const GRID = { cols: 24, rows: 12, cw: 50, ch: 32 };
  GRID.x0 = CX - (GRID.cols * GRID.cw) / 2;
  GRID.y0 = 545 - (GRID.rows * GRID.ch) / 2;
  const NCELL = GRID.cols * GRID.rows;
  const perm = [...Array(NCELL).keys()].sort(() => R() - 0.5);
  const landOrder = [...Array(NCELL).keys()].sort(() => R() - 0.5);
  const FRAGS = ['好的，周五前发布', 'plan.md 已更新', 'tool → exit 0', '我更喜欢简短的回答', '那个方案不行', '依赖 1.4 → 2.0 失败',
    '提醒我周五检查', '不是周四，是周五', '今天下雨了', 'RAPTOR: tree summary', 'pytest 37 passed', '记得先写测试', '网页正文已变化',
    '小林：进度如何？', '读完了 ReadAgent', '缓存没清导致失败', '插件已重载', '延期通知已发送', '生日快乐！', '换个模型试试',
    '每日回顾 09:00', '阿澈：推荐这本书', 'commit 3f2a9c', '这次先别发'];
  const cells = [];
  for (let c = 0; c < NCELL; c++) {
    const col = c % GRID.cols, row = Math.floor(c / GRID.cols);
    const a = R() * Math.PI * 2, sp = 260 + R() * 900;
    cells.push({
      x: GRID.x0 + col * GRID.cw + GRID.cw / 2, y: GRID.y0 + row * GRID.ch + GRID.ch / 2,
      land: 13.2 + (landOrder[c] / NCELL) * 7.2, sx: 160 + R() * (W - 320),
      vx: Math.cos(a) * sp, vy: Math.sin(a) * sp * 0.75, rot: (R() - 0.5) * 8,
      frag: c % 9 === 0 ? FRAGS[(c / 9) % FRAGS.length] : null, src: -1, w: 0.4 + R() * 0.5,
    });
  }
  S.forEach((s, k) => { cells[perm[k]].src = k; s.cell = perm[k]; });
  TM.cellLand = cells.map((c) => c.land);
  TM.SHATTER = 24.0;

  // 事件出现
  const evAppear = E.map((e) => 31 + 2.6 * hash(e.i * 1.7));
  TM.evAppear = evAppear;

  // 关联网络中各类联系的显现
  const typeCount = {}, typeIdx = [];
  for (const l of LK) { typeIdx[l.i] = typeCount[l.type] = (typeCount[l.type] || 0) + 1; }
  const linkReveal = LK.map((l) => 74 + M.TYPE_ORDER.indexOf(l.type) * 1.6 + (0.9 * (typeIdx[l.i] - 1)) / typeCount[l.type]);
  TM.linkReveal = linkReveal;
  TM.linkType = LK.map((l) => l.type);

  // 激活扩散
  const ACT_WIN = { release: [93, 101], research: [110.5, 116.5] };
  function actAt(ctx, t) {
    const steps = M.ACT[ctx], [a, b] = ACT_WIN[ctx];
    const kf = clamp((t - a) / (b - a)) * (steps.length - 1);
    const k0 = Math.floor(kf), k1 = Math.min(steps.length - 1, k0 + 1), f = kf - k0;
    return steps[k0].map((v, i) => lerp(v, steps[k1][i], f));
  }
  TM.actCross = [];
  for (const ctx of ['release', 'research']) {
    const steps = M.ACT[ctx], [a, b] = ACT_WIN[ctx];
    for (let i = 0; i < N; i++) {
      if (steps[0][i] > 0) { TM.actCross.push({ t: a + 0.02, i, ctx, seed: true }); continue; }
      for (let k = 1; k < steps.length; k++) {
        if (steps[k][i] >= 0.3) {
          const f = (0.3 - steps[k - 1][i]) / Math.max(1e-6, steps[k][i] - steps[k - 1][i]);
          TM.actCross.push({ t: a + ((k - 1 + f) / (steps.length - 1)) * (b - a), i, ctx });
          break;
        }
      }
    }
  }
  // 概率云：对最终激活做 softmax（示意 π）
  const PI_ = {};
  for (const ctx of ['release', 'research']) {
    const fin = M.ACT[ctx][M.ACT[ctx].length - 1];
    const cand = [...fin.keys()].sort((x, y) => fin[y] - fin[x]).slice(0, 9);
    const z = cand.map((i) => Math.exp(fin[i] / 0.18));
    const sz = z.reduce((p, q) => p + q, 0);
    PI_[ctx] = cand.map((i, k) => ({ i, p: 0.9 * (z[k] / sz) + 0.1 / cand.length }));
  }
  M.PI = PI_;
  const WS_T = { release: [103, 103.9, 104.6, 105.3], research: [114.4, 115.0, 115.6, 116.2] };
  TM.WS_T = WS_T;
  TM.recall = [128, 129.2, 130.4, 131.6, 132.8];

  // ======================= 相机 =======================
  function cam(t) {
    let yaw = 0, pitch = 0, zoom = 1, ox = 0, oy = 0;
    if (t > 69) { const u = t - 69; yaw = 0.07 * (u - 3 * (1 - Math.exp(-u / 3))); }
    const g = ease.inOut(prog(t, 69, 74));
    pitch = 0.32 * g;
    zoom = lerp(1, 1.08, g);
    // 团概览卡片出现时整体右移
    ox += 230 * ease.inOut(prog(t, 60.5, 62)) * (1 - ease.inOut(prog(t, 67.5, 70)));
    // 激活场景给左侧情境面板留位
    ox += 70 * ease.inOut(prog(t, 89, 92)) * (1 - ease.inOut(prog(t, 119, 122)));
    // 派生投影：俯视成圆盘
    const pj = ease.inOut(prog(t, 175.5, 179.5)) * (1 - ease.inOut(prog(t, 191, 195)));
    pitch = lerp(pitch, 1.18, pj);
    oy = lerp(oy, 300, pj);
    zoom = lerp(zoom, 1.0, pj);
    // 最终形态：拉远，给宿主留位；高潮时推近
    const fz = ease.inOut(prog(t, 192, 197));
    ox = lerp(ox, -170, fz * (1 - ease.inOut(prog(t, 206, 209))));
    zoom = lerp(zoom, 0.82, fz);
    zoom = lerp(zoom, 1.22, ease.inOut(prog(t, 206.5, 214)));
    pitch = lerp(pitch, 0.45, ease.inOut(prog(t, 206.5, 214)));
    zoom = lerp(zoom, 0.95, ease.inOut(prog(t, 214, 223)));
    return { yaw, pitch, zoom, ox, oy, f: 1250 };
  }
  function proj(p, k) {
    const cy = Math.cos(k.yaw), sy = Math.sin(k.yaw);
    const x = p.x * cy - p.z * sy, z = p.x * sy + p.z * cy;
    const cp = Math.cos(k.pitch), sp = Math.sin(k.pitch);
    const y2 = p.y * cp - z * sp, z2 = p.y * sp + z * cp;
    const s = k.f / (k.f + z2);
    return { x: CX + k.ox + x * s * k.zoom, y: CY + k.oy + y2 * s * k.zoom, s: s * k.zoom, z: z2 };
  }
  M.proj = proj;
  M.cam = cam;

  // 事件在三维中的位置（布局之间平滑过渡）
  function evPos(i, t) {
    const L = M.LAYOUT, e = E[i];
    let p;
    if (t < 48) p = L.stream[i];
    else if (t < 51.5) {
      const k = ease.inOut(prog(t, 48, 51.5 - 0.6 * hash(i)));
      p = { x: lerp(L.stream[i].x, L.cluster[i].x, k), y: lerp(L.stream[i].y, L.cluster[i].y, k), z: 0 };
    } else if (t < 69) p = L.cluster[i];
    else {
      const k = ease.inOut(prog(t, 69 + 0.8 * hash(i * 3), 73.5));
      p = { x: lerp(L.cluster[i].x, L.galaxy[i].x, k), y: lerp(L.cluster[i].y, L.galaxy[i].y, k), z: lerp(0, L.galaxy[i].z, k) };
    }
    const b = t > 69 ? 10 : 5;
    return { x: p.x + Math.sin(t * 0.5 + e.phase) * b, y: p.y + Math.cos(t * 0.43 + e.phase * 1.3) * b, z: p.z };
  }

  // ======================= 背景星空 =======================
  const stars = [];
  for (let k = 0; k < 340; k++) stars.push({ x: R() * W, y: R() * H, a: 0.08 + R() * 0.4, s: R() < 0.1 ? 1.6 : 1, tw: R() * 20 });

  function drawBackground(ctx, t, k) {
    const night = win(t, 138, 159, 2, 2);
    const g = ctx.createRadialGradient(CX, CY * 0.9, 50, CX, CY, 1150);
    g.addColorStop(0, rgba(M.mixC([14, 20, 38], [16, 18, 52], night), 1));
    g.addColorStop(1, rgba(C.bg, 1));
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);
    const sa = clamp((t - 1) / 4);
    for (const s of stars) {
      const x = (s.x - k.yaw * 40 + W * 10) % W;
      const a = s.a * sa * (0.65 + 0.35 * Math.sin(t * 0.8 + s.tw));
      ctx.fillStyle = rgba(C.ink, a);
      ctx.fillRect(x, s.y, s.s, s.s);
    }
  }

  // ======================= 记忆世界逐帧状态 =======================
  function worldState(t) {
    const st = {
      evA: new Float32Array(N).fill(0), glow: new Float32Array(N), col: new Array(N).fill(C.cyan),
      scale: new Float32Array(N).fill(1), lab: new Float32Array(N), linkA: new Float32Array(LK.length),
      linkDraw: new Float32Array(LK.length).fill(1), linkGlow: new Float32Array(LK.length),
      srcA: 0, srcK: 1, dustA: 0, dustLinkA: 0, act: null, srcGlow: 0, evDim: 1,
    };
    for (let i = 0; i < N; i++) st.evA[i] = ease.out(prog(t, evAppear[i], evAppear[i] + 0.9));
    // 来源记录可见度：27 秒后由碎片落定
    st.srcA = smooth(prog(t, 27.5, 31)) * 0.95;
    st.srcK = lerp(1, 0.42, ease.inOut(prog(t, 48, 52)));
    st.srcA *= lerp(1, 0.55, ease.inOut(prog(t, 48, 52)));
    // 标签
    const labStream = win(t, 33, 48.5, 1.2, 1.5) * 0.55;
    const labCluster = win(t, 50, 69.5, 1.2, 1.5) * 0.7;
    for (let i = 0; i < N; i++) st.lab[i] = Math.max(labStream, labCluster);
    // 星尘
    st.dustA = smooth(prog(t, 69.5, 74.5));
    st.dustLinkA = st.dustA * 0.5;
    // 联系
    for (const l of LK) {
      const r = linkReveal[l.i];
      st.linkDraw[l.i] = ease.inOut(prog(t, r, r + 0.9));
      st.linkA[l.i] = st.linkDraw[l.i] > 0 ? 0.55 : 0;
      st.linkGlow[l.i] = Math.exp(-Math.max(0, t - r - 0.5) * 1.4) * (t > r ? 1 : 0);
    }
    // 网络场景：各类型首次出现时显示两端标签
    if (t > 73 && t < 90) {
      const labNet = win(t, 73, 89.5, 1, 1.2) * 0.32;
      for (let i = 0; i < N; i++) st.lab[i] = labNet;
      for (const l of LK) {
        const r = linkReveal[l.i];
        const w = win(t, r, r + 3.2, 0.3, 0.8) * 0.65;
        st.lab[l.a] = Math.max(st.lab[l.a], w);
        st.lab[l.b] = Math.max(st.lab[l.b], w);
      }
      const hl = win(t, 82.5, 89.5, 0.8, 1);
      if (hl > 0) {
        const keys = ['incident', 'otherinc', 'skipver', 'tests'];
        for (let i = 0; i < N; i++) {
          if (!keys.includes(E[i].key)) {
            st.evA[i] *= 1 - 0.55 * hl;
            st.lab[i] *= 1 - 0.92 * hl;
          }
        }
        for (const k of keys) { st.lab[M.ev(k)] = hl; st.glow[M.ev(k)] = 0.8 * hl; }
        for (const l of LK) {
          const feat = (E[l.a].key === 'incident' && E[l.b].key === 'otherinc') || (E[l.a].key === 'skipver' && E[l.b].key === 'tests');
          if (feat) { st.linkGlow[l.i] = Math.max(st.linkGlow[l.i], hl); st.linkA[l.i] = 0.55 + 0.45 * hl; }
          else st.linkA[l.i] *= 1 - 0.6 * hl;
        }
      }
    }
    // 激活场景
    if (t > 90 && t < 121) {
      const A = new Float32Array(N);
      const fRel = clamp((t - 92.8) / 0.3) * (1 - smooth(prog(t, 108.6, 109.8)));
      const fRes = clamp((t - 110.3) / 0.3) * (1 - smooth(prog(t, 118.5, 120.5)));
      if (fRel > 0) { const a = actAt('release', t); for (let i = 0; i < N; i++) A[i] += a[i] * fRel; }
      if (fRes > 0) { const a = actAt('research', t); for (let i = 0; i < N; i++) A[i] += a[i] * fRes; }
      // 联想链：取回的“上月的发布事故”又成为线索
      const ch = win(t, 106.2, 108.8, 0.5, 0.6);
      if (ch > 0) {
        A[M.ev('otherinc')] += 0.8 * ch * smooth(prog(t, 106.5, 107.3));
        A[M.ev('skipver')] += 0.7 * ch * smooth(prog(t, 106.7, 107.5));
      }
      st.act = A;
      const sceneW = win(t, 90, 120.5, 1, 1.2);
      for (let i = 0; i < N; i++) {
        const a = Math.min(1.2, A[i]);
        st.evA[i] = lerp(1, 0.35 + 0.65 * Math.min(1, a * 1.6), sceneW);
        st.glow[i] = a * 1.1 * sceneW;
        st.col[i] = M.mixC(C.cyan, [255, 244, 214], clamp(a));
        st.lab[i] = Math.max(st.lab[i] * (1 - sceneW), clamp((a - 0.18) * 2.5) * sceneW * 0.9);
      }
      for (const l of LK) {
        const v = Math.min(A[l.a], A[l.b]);
        st.linkA[l.i] = lerp(st.linkA[l.i], 0.12 + 0.85 * clamp(v * 2.2), sceneW);
        st.linkGlow[l.i] = clamp(v * 2) * sceneW;
      }
      if (ch > 0) for (const l of LK) {
        const ka = E[l.a].key, kb = E[l.b].key;
        if (ka === 'incident' && (kb === 'otherinc' || kb === 'skipver')) st.linkGlow[l.i] = Math.max(st.linkGlow[l.i], ch);
      }
    }
    // 四个量：世界退为背景
    const dimQ = win(t, 120, 138.5, 1.2, 1.5);
    // 夜间巩固
    const night = win(t, 138, 159, 1.5, 1.5);
    // 分支：几乎隐去
    const br = win(t, 159, 176.5, 1.2, 1.5);
    const dimAll = 1 - 0.78 * dimQ - 0.25 * night - 0.9 * br;
    st.evDim = dimAll;
    st.dustA *= 1 - 0.6 * dimQ - 0.9 * br;
    for (let l = 0; l < LK.length; l++) st.linkA[l] *= 1 - 0.75 * dimQ - 0.3 * night - 0.95 * br;
    // 投影场景：标签隐藏，少数节点发光作为光柱源
    const pj = win(t, 177, 192, 1.5, 1.5);
    if (pj > 0) for (let i = 0; i < N; i++) st.lab[i] *= 1 - pj;
    // 最终形态
    if (t > 192) {
      // 更换模型：结构变暗，来源保持
      const off = smooth(prog(t, 200.3, 201.6));
      for (let i = 0; i < N; i++) {
        const p = M.LAYOUT.galaxy[i];
        const d = Math.hypot(p.x, p.y, p.z);
        const rb = smooth(prog(t, 203 + d / 520 * 2.6, 203.6 + d / 520 * 2.6));
        const k = 1 - off * (1 - rb);
        st.evA[i] *= lerp(0.12, 1, k);
        st.lab[i] = 0;
        st.glow[i] = win(t, 207, 214.5, 1.5, 1) * 0.35 * (0.5 + 0.5 * Math.sin(t * 2 + E[i].phase));
      }
      for (const l of LK) {
        const pa = M.LAYOUT.galaxy[l.a], pb = M.LAYOUT.galaxy[l.b];
        const d = Math.hypot((pa.x + pb.x) / 2, (pa.y + pb.y) / 2, (pa.z + pb.z) / 2);
        const rb = smooth(prog(t, 203.2 + d / 520 * 2.6, 204 + d / 520 * 2.6));
        st.linkA[l.i] *= lerp(0.05, 1, 1 - off * (1 - rb));
        st.linkGlow[l.i] = Math.max(st.linkGlow[l.i], win(t, 203.2 + d / 520 * 2.6, 205 + d / 520 * 2.6, 0.2, 0.8) * 0.9 + win(t, 207, 214, 1, 1) * 0.28);
      }
      st.srcGlow = win(t, 200.3, 206.5, 0.6, 1.2);
      st.dustA *= lerp(1, 0.25, off * (1 - smooth(prog(t, 203.5, 206.5))));
      // 高潮大字期间适度压暗背景节点，突出核心主题句
      st.evDim *= 1 - 0.38 * win(t, 207.2, 214.4, 0.8, 0.8);
    }
    // 片尾标题时世界退后
    const tail = smooth(prog(t, 214, 216.5));
    st.evDim *= 1 - 0.62 * tail;
    st.dustA *= 1 - 0.45 * tail;
    for (let l = 0; l < LK.length; l++) st.linkA[l] *= 1 - 0.55 * tail;
    return st;
  }

  // ======================= 绘制记忆世界 =======================
  function drawWorld(ctx, t, k, st, P) {
    // 星尘
    if (st.dustA > 0.01) {
      const DP = M.dust.map((d) => proj({ x: d.x, y: d.y, z: d.z }, k));
      if (st.dustLinkA > 0.01) {
        ctx.lineWidth = 0.6;
        for (const [a, b] of M.dustLinks) {
          const pa = DP[a], pb = DP[b];
          const al = st.dustLinkA * 0.14 * Math.min(pa.s, pb.s);
          ctx.strokeStyle = rgba([110, 150, 210], al);
          ctx.beginPath(); ctx.moveTo(pa.x, pa.y); ctx.lineTo(pb.x, pb.y); ctx.stroke();
        }
      }
      for (let j = 0; j < M.dust.length; j++) {
        const d = M.dust[j], p = DP[j];
        if (p.s <= 0) continue;
        const a = st.dustA * (0.25 + 0.35 * Math.sin(t * 1.3 + d.tw)) * clamp(p.s * 1.2 - 0.3);
        const c = d.hue < 0.15 ? C.gold : d.hue < 0.3 ? C.violet : [150, 190, 255];
        ctx.fillStyle = rgba(c, a);
        const r = d.s * p.s * 1.3;
        ctx.fillRect(p.x - r / 2, p.y - r / 2, r, r);
      }
    }
    // 联系
    for (const l of LK) {
      const a = st.linkA[l.i] * Math.min(st.evA[l.a], st.evA[l.b]) * 1.0;
      const dr = st.linkDraw[l.i];
      if (a <= 0.01 || dr <= 0) continue;
      const pa = P[l.a], pb = P[l.b];
      const ty = M.LTYPES[l.type];
      const mx = (pa.x + pb.x) / 2 - (pb.y - pa.y) * l.bow, my = (pa.y + pb.y) / 2 + (pb.x - pa.x) * l.bow;
      const gl = st.linkGlow[l.i];
      const col = M.mixC(ty.color, C.white, gl * 0.4);
      ctx.strokeStyle = rgba(col, a * (0.45 + 0.55 * gl));
      ctx.lineWidth = (1 + 1.6 * gl + l.w * 0.6) * Math.min(pa.s, pb.s);
      if (l.type === 'ana') ctx.setLineDash([6, 6]); else ctx.setLineDash([]);
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      if (dr >= 1) ctx.quadraticCurveTo(mx, my, pb.x, pb.y);
      else {
        const n = 18;
        for (let s = 1; s <= Math.ceil(n * dr); s++) {
          const u = Math.min(dr, s / n);
          ctx.lineTo((1 - u) * (1 - u) * pa.x + 2 * (1 - u) * u * mx + u * u * pb.x, (1 - u) * (1 - u) * pa.y + 2 * (1 - u) * u * my + u * u * pb.y);
        }
      }
      ctx.stroke();
      ctx.setLineDash([]);
      // 流动的光点
      if (dr >= 1 && a > 0.05) {
        const sp = 0.25 + 0.2 * hash(l.i);
        const u = ((t * sp + hash(l.i * 9)) % 1);
        const x = (1 - u) * (1 - u) * pa.x + 2 * (1 - u) * u * mx + u * u * pb.x;
        const y = (1 - u) * (1 - u) * pa.y + 2 * (1 - u) * u * my + u * u * pb.y;
        const r = (1.6 + 2.2 * gl) * Math.min(pa.s, pb.s);
        ctx.fillStyle = rgba(M.mixC(ty.color, C.white, 0.6), a * (0.6 + 0.4 * gl));
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      }
      // 有方向的联系画箭头
      if (ty.dir && dr >= 1) {
        const u = 0.82;
        const x = (1 - u) * (1 - u) * pa.x + 2 * (1 - u) * u * mx + u * u * pb.x;
        const y = (1 - u) * (1 - u) * pa.y + 2 * (1 - u) * u * my + u * u * pb.y;
        const dx = 2 * (1 - u) * (mx - pa.x) + 2 * u * (pb.x - mx), dy = 2 * (1 - u) * (my - pa.y) + 2 * u * (pb.y - my);
        const an = Math.atan2(dy, dx), s = 9 * Math.min(pa.s, pb.s);
        ctx.fillStyle = rgba(col, a * 0.9);
        ctx.beginPath();
        ctx.moveTo(x + Math.cos(an) * s, y + Math.sin(an) * s);
        ctx.lineTo(x + Math.cos(an + 2.5) * s, y + Math.sin(an + 2.5) * s);
        ctx.lineTo(x + Math.cos(an - 2.5) * s, y + Math.sin(an - 2.5) * s);
        ctx.fill();
      }
    }
  }

  // 来源记录（金色菱形）与谱系细线
  function sourcePos(s, t, k, P) {
    const p = P[s.ev];
    const a = s.ang + s.spin * t * 0.25;
    const r = s.rad * st_srcK * p.s;
    return { x: p.x + Math.cos(a) * r, y: p.y + Math.sin(a) * r * 0.8, s: p.s };
  }
  let st_srcK = 1;
  function diamond(ctx, x, y, r, rot) {
    ctx.beginPath();
    for (let q = 0; q < 4; q++) {
      const a = rot + (q * Math.PI) / 2;
      const px = x + Math.cos(a) * r, py = y + Math.sin(a) * r;
      if (q) ctx.lineTo(px, py); else ctx.moveTo(px, py);
    }
    ctx.closePath();
  }

  // 档案网格与碎片（场景 1）及其落定为来源记录的过程
  function cellPos(c, t) {
    if (t < TM.SHATTER) return { x: c.x, y: c.y, rot: 0 };
    const u = t - TM.SHATTER;
    const d = (1 - Math.exp(-u * 2.4)) / 2.4;
    return { x: c.x + c.vx * d, y: c.y + c.vy * d + 18 * u * u * 0.2, rot: c.rot * d };
  }
  function drawArchive(ctx, t, k, P) {
    if (t < 12.5 || t > 32) return;
    const scan = win(t, 19.6, 23.6, 0.3, 0.3);
    const scanY = GRID.y0 + ((t - 19.8) / 3.4) * GRID.rows * GRID.ch;
    for (let c = 0; c < NCELL; c++) {
      const cell = cells[c];
      const tl = cell.land;
      if (t < tl - 0.9) continue;
      let x, y, rot = 0, a = 1, landed = t >= tl;
      if (!landed) {
        const u = ease.in(prog(t, tl - 0.9, tl));
        x = lerp(cell.sx, cell.x, ease.inOut(u));
        y = lerp(-40, cell.y, u);
        a = clamp((t - tl + 0.9) / 0.3);
      } else {
        const p = cellPos(cell, t);
        x = p.x; y = p.y; rot = p.rot;
      }
      // 落定为来源记录
      if (cell.src >= 0 && t > 27) {
        const s = S[cell.src];
        const tgt = sourcePos(s, t, k, P);
        const u = ease.inOut(prog(t, 27 + 0.8 * hash(c), 30.6 + 0.4 * hash(c * 2)));
        x = lerp(x, tgt.x, u); y = lerp(y, tgt.y, u); rot *= 1 - u;
        if (u >= 1) continue; // 交由来源绘制
      }
      if (cell.src < 0 && t > TM.SHATTER) a *= 1 - smooth(prog(t, TM.SHATTER + 0.2 + hash(c) * 1.5, TM.SHATTER + 2.8 + hash(c) * 1.2));
      if (a <= 0.01) continue;
      const shat = t > TM.SHATTER;
      const isSrc = cell.src >= 0;
      const colc = shat ? (isSrc ? M.mixC(C.gray, C.gold, smooth(prog(t, 24.3, 27))) : C.gray) : [120, 132, 150];
      let hi = 0;
      if (scan > 0 && landed && !shat) {
        hi = Math.exp(-Math.abs(y - scanY) / 10) * scan;
        if (hash(c * 13.3 + Math.floor(t * 6)) > 0.83 && y < scanY) hi = Math.max(hi, 0.55 * scan);
      }
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(rot);
      const ww = shat ? lerp(GRID.cw - 8, 8, smooth(prog(t, 24.2, 27))) : GRID.cw - 8;
      const hh = shat ? lerp(GRID.ch - 10, 8, smooth(prog(t, 24.2, 27))) : GRID.ch - 10;
      ctx.fillStyle = rgba(M.mixC(colc, C.cyan, hi), (0.16 + 0.6 * hi) * a + (shat && isSrc ? 0.35 * a : 0));
      ctx.fillRect(-ww / 2, -hh / 2, ww, hh);
      if (!shat) {
        ctx.fillStyle = rgba(M.mixC([150, 160, 180], C.white, hi), (0.35 + 0.5 * hi) * a);
        ctx.fillRect(-ww / 2 + 4, -2, (ww - 8) * cell.w, 1.5);
        ctx.fillRect(-ww / 2 + 4, 3, (ww - 8) * cell.w * 0.6, 1.5);
      }
      ctx.restore();
      if (cell.frag && !landed) {
        M.text(ctx, cell.frag, x, y - 18, { size: 17, fam: 'mono', color: [190, 205, 225], alpha: a * 0.85 });
      }
    }
    // 网格外框、计数器与检索条
    const gA = win(t, 12.8, 24.0, 0.8, 0.05);
    if (gA > 0) {
      ctx.strokeStyle = rgba([120, 140, 170], 0.28 * gA);
      ctx.lineWidth = 1;
      ctx.strokeRect(GRID.x0 - 10, GRID.y0 - 10, GRID.cols * GRID.cw + 20, GRID.rows * GRID.ch + 20);
      let n = 0;
      for (const c of cells) if (t >= c.land) n++;
      const count = Math.floor(n * 43.08 + (n > 0 ? 17 : 0));
      M.text(ctx, '已存储  ' + count.toLocaleString('en-US') + '  条记录', GRID.x0 + GRID.cols * GRID.cw + 10, GRID.y0 - 34, { size: 18, fam: 'mono', color: C.dim, alpha: gA, align: 'right' });
      M.text(ctx, 'ARCHIVE / 全文存档', GRID.x0 - 10, GRID.y0 - 34, { size: 18, fam: 'mono', color: C.dim, alpha: gA * 0.8, align: 'left' });
    }
    if (scan > 0) {
      const qa = win(t, 19.2, 23.7, 0.4, 0.15);
      const bx = CX - 330, by = GRID.y0 - 100;
      ctx.strokeStyle = rgba(C.cyan, 0.5 * qa); ctx.lineWidth = 1.2;
      M.roundRect(ctx, bx, by - 24, 660, 48, 24); ctx.stroke();
      M.text(ctx, '检索：「上次答应小林什么？」', bx + 30, by, { size: 22, color: C.ink, alpha: qa, align: 'left', reveal: prog(t, 19.3, 20.3) });
      ctx.fillStyle = rgba(C.cyan, 0.85 * scan);
      ctx.fillRect(GRID.x0 - 10, scanY, GRID.cols * GRID.cw + 20, 1.5);
      const hits = Math.floor(prog(t, 20, 23.4) * 137);
      M.text(ctx, `命中 ${hits} 条 · 相似度 0.81  0.80  0.80  0.79  0.79 …`, CX, GRID.y0 + GRID.rows * GRID.ch + 44, { size: 18, fam: 'mono', color: C.cyan, alpha: qa * 0.85 });
    }
  }

  function drawSourcesAndEvents(ctx, t, k, st, P) {
    st_srcK = st.srcK;
    const focusK = M.ev('delayplan');
    const fo = win(t, 35, 46.8, 0.8, 1.0);
    const sp = ease.inOut(prog(t, 40.5, 42)) * (1 - ease.inOut(prog(t, 45.8, 47.3)));
    const fp = P[focusK];
    const splitPos = (side) => ({ x: fp.x + side * 82 * sp, y: fp.y + side * 6 * sp, s: fp.s });

    // 来源与谱系线
    if (st.srcA > 0.01) {
      for (const s of S) {
        const e = E[s.ev];
        const p = sourcePos(s, t, k, P);
        const cellDone = prog(t, 27 + 0.8 * hash(s.cell), 30.6 + 0.4 * hash(s.cell * 2)) >= 1;
        if (t < 31 && !cellDone) continue;
        let a = st.srcA * st.evDim;
        if (fo > 0 && s.ev !== focusK) a *= 1 - 0.6 * fo;
        const glow = st.srcGlow;
        // 谱系线
        let ep = P[s.ev];
        if (s.ev === focusK && sp > 0) ep = splitPos(e.src.indexOf(s.i) < 2 ? -1 : 1);
        const la = a * st.evA[s.ev] * (0.32 + 0.4 * glow);
        if (la > 0.01) {
          ctx.strokeStyle = rgba(C.gold, la);
          ctx.lineWidth = 0.8;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(ep.x, ep.y); ctx.stroke();
        }
        const r = (3.4 + 1.6 * glow) * Math.max(0.6, p.s);
        ctx.fillStyle = rgba(M.mixC(C.gold, C.white, glow * 0.3), a * (0.85 + 0.15 * glow));
        diamond(ctx, p.x, p.y, r, t * 0.3 + s.ang);
        ctx.fill();
        if (glow > 0.05) M.glowDot(ctx, p.x, p.y, 16 * p.s, C.gold, glow * 0.5 * a);
      }
    }
    // 收集当前帧的 UI / 字幕禁区，防止节点标签与卡片、标题、底部字幕重叠
    const placed = [
      { x0: 0, y0: H - 142, x1: W, y1: H },
      { x0: 0, y0: 0, x1: 560, y1: 128 },
    ];
    if (fo > 0.05) {
      const right = fp.x < 1150;
      const cw = 470, chh = 262;
      const cx0 = right ? fp.x + 120 : fp.x - 120 - cw, cy0 = clamp(fp.y - 130, 150, H - 420);
      placed.push({ x0: cx0 - 16, y0: cy0 - 16, x1: cx0 + cw + 16, y1: cy0 + chh + 16 });
    }
    if (t > 56.5 && t < 61.8) {
      const nd = P[M.ev('newdate')];
      placed.push({ x0: nd.x - 210, y0: nd.y - 74, x1: nd.x + 210, y1: nd.y - 20 });
    }
    if (t > 61.2 && t < 68.5) {
      placed.push({ x0: 54, y0: 284, x1: 586, y1: 646 });
    }
    if (t > 73 && t < 90) {
      placed.push({ x0: 76, y0: 316, x1: 300, y1: 600 });
      if (t > 82.5) placed.push({ x0: CX - 320, y0: 155, x1: CX + 320, y1: 280 });
    }
    if (t > 90 && t < 120.5) {
      placed.push({ x0: 64, y0: 134, x1: 526, y1: 380 });
      placed.push({ x0: W - 680, y0: 125, x1: W, y1: 235 });
      if (t > 102) placed.push({ x0: W - 486, y0: 584, x1: W - 64, y1: 946 });
    }
    const overlaps = (rc) => {
      if (rc.x0 < 24 || rc.x1 > W - 24 || rc.y0 < 24 || rc.y1 > H - 136) return true;
      for (const q of placed) {
        if (rc.x0 < q.x1 && rc.x1 > q.x0 && rc.y0 < q.y1 && rc.y1 > q.y0) return true;
      }
      return false;
    };

    // 事件节点球体（按深度从远到近绘制）
    const order = [...Array(N).keys()].sort((a, b) => P[b].z - P[a].z);
    const drawNodeBody = (x, y, r, gl, col, a, depth, ps) => {
      M.glowDot(ctx, x, y, r * (2.9 + 2.0 * gl), col, a * (0.16 + 0.28 * gl) * depth);
      ctx.fillStyle = rgba(M.mixC(col, C.white, 0.35 + 0.4 * clamp(gl)), a * depth);
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = rgba(col, a * 0.5 * depth);
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(x, y, r + 4 * ps, 0, Math.PI * 2); ctx.stroke();
    };
    for (const i of order) {
      const e = E[i], p = P[i];
      let a = st.evA[i] * st.evDim;
      if (fo > 0 && i !== focusK) a *= 1 - 0.65 * fo;
      if (a <= 0.01) continue;
      const depth = clamp(p.s * 1.25 - 0.25, 0.25, 1.2);
      const gl = st.glow[i];
      const col = st.col[i];
      const r = (4.2 + 2.4 * e.size) * p.s * st.scale[i] * (1 + 0.3 * gl);
      if (i === focusK && sp > 0.001) {
        const A = splitPos(-1), B = splitPos(1);
        drawNodeBody(A.x, A.y, r, gl, col, a, depth, p.s);
        drawNodeBody(B.x, B.y, r, gl, col, a, depth, p.s);
        placed.push({ x0: A.x - r - 6, y0: A.y - r - 6, x1: B.x + r + 6, y1: B.y + r + 6 });
      } else {
        drawNodeBody(p.x, p.y, r, gl, col, a, depth, p.s);
        placed.push({ x0: p.x - r - 4, y0: p.y - r - 4, x1: p.x + r + 4, y1: p.y + r + 4 });
      }
    }

    // 事件节点标签（按重要性排序后防碰撞绘制）
    const labOrder = [...Array(N).keys()].sort((a, b) => {
      const pa = (a === focusK ? 10 : 0) + st.glow[a] * 3 + st.lab[a];
      const pb = (b === focusK ? 10 : 0) + st.glow[b] * 3 + st.lab[b];
      return pb - pa;
    });
    for (const i of labOrder) {
      const e = E[i], p = P[i];
      let a = st.evA[i] * st.evDim;
      if (fo > 0 && i !== focusK) a *= 1 - 0.65 * fo;
      if (a <= 0.01) continue;
      const depth = clamp(p.s * 1.25 - 0.25, 0.25, 1.2);
      const gl = st.glow[i];
      const r = (4.2 + 2.4 * e.size) * p.s * st.scale[i] * (1 + 0.3 * gl);
      if (i === focusK && sp > 0.001) {
        const A = splitPos(-1), B = splitPos(1);
        const la = clamp(sp * 2 - 1) * a * depth;
        if (la > 0.01) {
          M.text(ctx, '评估延期方案', A.x - r - 12, A.y - 4, { size: 16, color: C.ink, alpha: la, align: 'right' });
          M.text(ctx, '倾向推迟到周五', B.x + r + 12, B.y + 4, { size: 16, color: C.ink, alpha: la, align: 'left' });
        }
        if (sp < 0.5) M.text(ctx, e.label, p.x, p.y - r - 18, { size: 16, alpha: (1 - sp * 2) * 0.85, align: 'center' });
        continue;
      }
      let la = Math.max(st.lab[i], i === focusK ? fo : 0);
      if (fo > 0 && i !== focusK) la *= 1 - 0.94 * fo;
      const finalA = la * a * depth;
      if (finalA <= 0.04) continue;
      const sz = Math.round(15 * clamp(p.s, 0.82, 1.22));
      M.font(ctx, sz, 'sans', 400);
      const tw = M.textWidth(ctx, e.label, 0);
      const hh = Math.round(sz * 0.72);
      const rcRight = { x0: p.x + r + 8, y0: p.y - hh, x1: p.x + r + 12 + tw, y1: p.y + hh };
      const rcLeft = { x0: p.x - r - 12 - tw, y0: p.y - hh, x1: p.x - r - 8, y1: p.y + hh };
      if (!overlaps(rcRight) || i === focusK) {
        placed.push({ x0: rcRight.x0 - 4, y0: rcRight.y0 - 3, x1: rcRight.x1 + 4, y1: rcRight.y1 + 3 });
        M.text(ctx, e.label, p.x + r + 9, p.y, { size: sz, color: C.ink, alpha: finalA, align: 'left' });
      } else if (!overlaps(rcLeft)) {
        placed.push({ x0: rcLeft.x0 - 4, y0: rcLeft.y0 - 3, x1: rcLeft.x1 + 4, y1: rcLeft.y1 + 3 });
        M.text(ctx, e.label, p.x - r - 9, p.y, { size: sz, color: C.ink, alpha: finalA, align: 'right' });
      }
    }
    return { fo, sp, fp, splitPos };
  }

  // ======================= 叠层：通用 =======================
  function header(ctx, t) {
    for (let s = 1; s < M.SCENES.length; s++) {
      const sc = M.SCENES[s];
      const t1 = sc.id === 'final' ? 199 : sc.t1 - 0.6;
      const a = win(t, sc.t0 + 0.4, t1, 0.8, 0.8);
      if (a <= 0) continue;
      const idx = String(s).padStart(2, '0');
      M.text(ctx, idx, 92, 92, { size: 18, fam: 'mono', color: C.cyan, alpha: a * 0.9, align: 'left', spacing: 2 });
      ctx.fillStyle = rgba(C.cyan, a * 0.6);
      ctx.fillRect(126, 91, 26 * ease.out(prog(t, sc.t0 + 0.4, sc.t0 + 1.4)), 1.2);
      M.text(ctx, sc.title, 166, 92, { size: 28, fam: 'serif', weight: 500, alpha: a, align: 'left', spacing: 2, reveal: prog(t, sc.t0 + 0.5, sc.t0 + 1.6) });
    }
  }

  function card(ctx, x, y, w, h, a, col = C.cyan) {
    if (a <= 0.01) return;
    ctx.save();
    ctx.fillStyle = rgba([10, 16, 30], 0.82 * a);
    M.roundRect(ctx, x, y, w, h, 14);
    ctx.fill();
    ctx.strokeStyle = rgba(col, 0.55 * a);
    ctx.lineWidth = 1.2;
    ctx.stroke();
    ctx.fillStyle = rgba(col, 0.9 * a);
    ctx.fillRect(x + 18, y, 46, 2);
    ctx.restore();
  }
  M.card = card;

  // ======================= 叠层：各场景 =======================
  function prologue(ctx, t) {
    if (t > 13) return;
    // 一个呼吸的光点
    const appear = smooth(prog(t, 0.6, 2.2));
    const beat = (u) => Math.exp(-((u % 1.5)) * 5);
    const pulse = t > 1 ? beat(t - 1) : 0;
    const burst = prog(t, 11.2, 12.2);
    const r = (3 + 2.5 * pulse) * appear * (1 - burst);
    if (r > 0.05) {
      M.glowDot(ctx, CX, CY - 20, 80 + 40 * pulse, C.cyan, (0.35 + 0.4 * pulse) * appear * (1 - burst));
      ctx.fillStyle = rgba(C.white, appear * (1 - burst));
      ctx.beginPath(); ctx.arc(CX, CY - 20, r, 0, Math.PI * 2); ctx.fill();
      // 扩散的环
      for (let q = 0; q < 3; q++) {
        const u = ((t - 1 + q * 0.5) % 1.5) / 1.5;
        if (t < 1) break;
        ctx.strokeStyle = rgba(C.cyan, (1 - u) * 0.25 * appear * (1 - burst));
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(CX, CY - 20, 10 + u * 160, 0, Math.PI * 2); ctx.stroke();
      }
    }
    if (burst > 0 && burst < 1) {
      for (let q = 0; q < 60; q++) {
        const a = hash(q) * Math.PI * 2, d = ease.out(burst) * (200 + 500 * hash(q * 3));
        ctx.fillStyle = rgba(C.cyan, (1 - burst) * 0.8);
        ctx.fillRect(CX + Math.cos(a) * d, CY - 20 + Math.sin(a) * d * 0.6, 3, 3);
      }
    }
    M.text(ctx, '如果一个 AI 持续存在——', CX, CY + 120, { size: 46, fam: 'serif', weight: 400, alpha: win(t, 3.2, 7.4, 1, 0.8), reveal: prog(t, 3.2, 5.2), spacing: 6 });
    M.text(ctx, '它该如何拥有自己的过去？', CX, CY + 120, { size: 46, fam: 'serif', weight: 400, alpha: win(t, 7.6, 11.6, 1, 0.6), reveal: prog(t, 7.6, 9.6), spacing: 6 });
  }

  function archiveOverlay(ctx, t) {
    M.caption(ctx, t, '过去被完整地保存了下来……', 14, 18.8);
    M.caption(ctx, t, '却很少在需要的时候，参与现在。', 19.2, 23.6);
    M.caption(ctx, t, '长期记忆的目标，不只是重新找到过去的文字。', 24.8, 27.6, { fam: 'serif', weight: 400, size: 38 });
  }

  function eventsOverlay(ctx, t, info) {
    M.caption(ctx, t, '来源记录：消息、文件、工具输出、纠正 —— 可追溯，证明「发生过」，不证明「为真」', 28.6, 34.2, { size: 30 });
    M.caption(ctx, t, '事件块：对一段经历的解释 —— 谁参与、经过如何、结果怎样、还有什么未决', 34.6, 40.2, { size: 30 });
    M.caption(ctx, t, '解释可以修订、重新切分；来源保持不变，并始终可回查', 40.6, 47.4, { size: 30 });
    const { fo, sp, fp } = info;
    if (fo <= 0.01) return;
    // 焦点事件信息卡
    const right = fp.x < 1150;
    const cw = 470, chh = 262;
    const x = right ? fp.x + 120 : fp.x - 120 - cw, y = clamp(fp.y - 130, 150, H - 420);
    const a = fo * ease.out(prog(t, 35.2, 36.2));
    card(ctx, x, y, cw, chh, a);
    const v2 = sp > 0.5;
    M.text(ctx, v2 ? '事件块 · 解释 v2（重新切分）' : '事件块 · 讨论延期方案', x + 24, y + 34, { size: 21, fam: 'serif', weight: 600, alpha: a, align: 'left' });
    const rows = v2
      ? [['切分', '评估延期方案 ｜ 倾向推迟到周五'], ['参与者', '小林 · 灵'], ['依据', '同一组 4 条来源，未新增'], ['变化', '解释 v1 → v2，旧版本保留']]
      : [['参与者', '小林 · 灵'], ['经过', '依赖升级失败，评估两种延期方案'], ['结果', '倾向推迟到周五'], ['未决', '依赖何时就绪？']];
    rows.forEach(([kk, vv], r) => {
      const ry = y + 80 + r * 34;
      const ra = a * clamp((t - 35.6 - r * 0.25) / 0.4);
      M.text(ctx, kk, x + 24, ry, { size: 16, fam: 'mono', color: C.cyan, alpha: ra * 0.9, align: 'left' });
      M.text(ctx, vv, x + 110, ry, { size: 18, color: C.ink, alpha: ra, align: 'left' });
    });
    M.text(ctx, '来源  消息×2 · 文件×1 · 工具×1', x + 24, y + chh - 30, { size: 15, fam: 'mono', color: C.gold, alpha: a * 0.9, align: 'left' });
    // 连接线
    ctx.strokeStyle = rgba(C.cyan, 0.4 * a);
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(fp.x + (right ? 30 : -30), fp.y); ctx.lineTo(right ? x : x + cw, y + 40); ctx.stroke();
    // 来源不变：环形提示
    const ring = win(t, 41.5, 46.5, 0.5, 0.8);
    if (ring > 0) {
      const u = ((t - 41.5) % 1.2) / 1.2;
      ctx.strokeStyle = rgba(C.gold, ring * (1 - u) * 0.8);
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(fp.x, fp.y, 60 + u * 40, 0, Math.PI * 2); ctx.stroke();
      M.text(ctx, '来源不变', fp.x, fp.y + 112, { size: 17, fam: 'mono', color: C.gold, alpha: ring });
    }
  }

  const CL = [
    { k: 'P', name: '人物 · 小林', col: C.coral, lx: -510, ly: -295 },
    { k: 'J', name: '项目 · 发布', col: C.teal, lx: 410, ly: -335 },
    { k: 'T', name: '时间 · 第三周', col: C.blue, lx: 340, ly: 335 },
    { k: 'D', name: '主题 · 延期', col: C.violet, lx: -530, ly: 235 },
  ];
  TM.hullT = CL.map((c, j) => 51 + j * 1.3);
  function clustersUnder(ctx, t, k, P) {
    const A = win(t, 50.5, 69.5, 0.8, 1.6);
    if (A <= 0) return;
    CL.forEach((c, j) => {
      const t0 = TM.hullT[j];
      const ap = ease.out(prog(t, t0, t0 + 1.0)) * A;
      if (ap <= 0.01) return;
      const pts = E.filter((e) => e[c.k]).map((e) => P[e.i]);
      ctx.save();
      M.hullPath(ctx, pts, 34 + 8 * Math.sin(t * 0.7 + j), lerp(0.6, 1, ap));
      ctx.fillStyle = rgba(c.col, 0.068 * ap);
      ctx.fill();
      ctx.strokeStyle = rgba(c.col, 0.65 * ap);
      ctx.lineWidth = 1.5;
      ctx.setLineDash([2, 5]);
      ctx.stroke();
      ctx.restore();
    });
  }
  function clustersOver(ctx, t, k, P) {
    const A = win(t, 50.5, 69.5, 0.8, 1.6);
    if (A <= 0) return;
    const hl = win(t, 57, 61.6, 0.6, 0.6);
    const nd = P[M.ev('newdate')];
    CL.forEach((c, j) => {
      const t0 = TM.hullT[j];
      const ap = ease.out(prog(t, t0 + 0.2, t0 + 1.2)) * A;
      const lx = CX + k.ox + c.lx, ly = CY + c.ly;
      if (hl > 0) {
        ctx.strokeStyle = rgba(c.col, 0.65 * hl);
        ctx.lineWidth = 1.2;
        const u = ease.inOut(prog(t, 57.2 + j * 0.15, 58.2 + j * 0.15));
        ctx.beginPath(); ctx.moveTo(nd.x, nd.y); ctx.lineTo(lerp(nd.x, lx, u), lerp(nd.y, ly, u)); ctx.stroke();
      }
      ctx.fillStyle = rgba(c.col, ap);
      ctx.beginPath(); ctx.arc(lx - 14, ly, 4, 0, Math.PI * 2); ctx.fill();
      M.text(ctx, c.name, lx, ly, { size: 22, fam: 'serif', weight: 500, color: M.mixC(c.col, C.white, 0.35), alpha: ap, align: 'left', reveal: prog(t, t0 + 0.2, t0 + 1.2), spacing: 2 });
    });
    if (hl > 0) {
      M.glowDot(ctx, nd.x, nd.y, 48, C.gold, 0.28 * hl);
      ctx.save();
      ctx.fillStyle = rgba([6, 11, 24], 0.85 * hl);
      M.roundRect(ctx, nd.x - 196, nd.y - 68, 392, 38, 19);
      ctx.fill();
      ctx.strokeStyle = rgba(C.gold, 0.55 * hl);
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.restore();
      M.text(ctx, '同一事件，同时属于四个团 —— 不复制', nd.x, nd.y - 49, { size: 19, color: C.ink, alpha: hl, reveal: prog(t, 57.4, 58.3) });
    }
    M.caption(ctx, t, '团可以重叠：按人物、项目、时间、主题……多个维度并存', 52, 57, { size: 30 });
    M.caption(ctx, t, '归属是一种组织方式，不是复制，也不是意识单元', 57.3, 61.5, { size: 30 });
    M.caption(ctx, t, '团回答：「这方面，大致有什么过去」—— 背景先行，细节可展开', 62, 68.3, { size: 30 });
    // 团概览卡片
    const ca = win(t, 61.6, 68.2, 0.8, 0.9);
    if (ca > 0) {
      const x = 70, y = 300, w = 500, h = 330;
      card(ctx, x, y, w, h, ca, C.coral);
      M.text(ctx, '团概览 · 人物 · 小林', x + 26, y + 38, { size: 22, fam: 'serif', weight: 600, alpha: ca, align: 'left', reveal: prog(t, 61.8, 62.6) });
      const rows = [['覆盖', '10/02 — 10/21 · 11 个事件'], ['主要经过', '发布延期讨论 ×3 · 测试分工'], ['关键变化', '截止日改为周五'], ['未决', '依赖是否就绪？'], ['更新', '10/21 夜间整理']];
      rows.forEach(([kk, vv], r) => {
        const rt = 62.4 + r * 0.45;
        M.text(ctx, kk, x + 26, y + 88 + r * 38, { size: 16, fam: 'mono', color: C.coral, alpha: ca * clamp((t - rt) / 0.3), align: 'left' });
        M.text(ctx, vv, x + 126, y + 88 + r * 38, { size: 18, alpha: ca, align: 'left', reveal: prog(t, rt, rt + 0.6) });
      });
      const ex = win(t, 65.3, 68.2, 0.3, 0.8);
      const pul = 0.6 + 0.4 * Math.sin(t * 6);
      M.text(ctx, '⤷ 展开具体事件与原话', x + 26, y + h - 32, { size: 18, color: C.gold, alpha: ca * (0.6 + 0.4 * ex * pul), align: 'left' });
      if (ex > 0) {
        ['newdate', 'propose', 'brief'].forEach((kk, q) => {
          const p = P[M.ev(kk)];
          const u = ease.inOut(prog(t, 65.4 + q * 0.18, 66.2 + q * 0.18));
          const sx = x + 230, sy = y + h - 32;
          ctx.strokeStyle = rgba(C.gold, 0.7 * ex);
          ctx.lineWidth = 1.2;
          ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(lerp(sx, p.x, u), lerp(sy, p.y, u)); ctx.stroke();
          if (u >= 1) M.glowDot(ctx, p.x, p.y, 40, C.gold, 0.5 * ex);
        });
      }
    }
  }

  function networkOverlay(ctx, t) {
    const A = win(t, 73.5, 89.6, 0.8, 1);
    if (A > 0) {
      M.TYPE_ORDER.forEach((ty, j) => {
        const r = 74 + j * 1.6;
        const a = A * ease.out(prog(t, r, r + 0.6));
        const y = 380 + j * 46;
        const T = M.LTYPES[ty];
        ctx.strokeStyle = rgba(T.color, a);
        ctx.lineWidth = 2;
        if (ty === 'ana') ctx.setLineDash([6, 6]);
        ctx.beginPath(); ctx.moveTo(92, y); ctx.lineTo(92 + 40 * ease.out(prog(t, r, r + 0.6)), y); ctx.stroke();
        ctx.setLineDash([]);
        M.text(ctx, T.name, 148, y, { size: 19, alpha: a, align: 'left', reveal: prog(t, r, r + 0.8) });
      });
      M.text(ctx, '联系类型', 92, 336, { size: 15, fam: 'mono', color: C.dim, alpha: A * 0.9, align: 'left' });
    }
    M.caption(ctx, t, '联想直接发生在事件之间，可以跨越任何团；关系可以有方向', 74.5, 82.2, { size: 30 });
    const s = win(t, 83, 89.4, 0.8, 0.9);
    if (s > 0) {
      M.text(ctx, '同团，不推出强联想', CX, 190, { size: 40, fam: 'serif', alpha: s, reveal: prog(t, 83, 84.4), spacing: 6 });
      M.text(ctx, '强联想，也不要求同团', CX, 250, { size: 40, fam: 'serif', alpha: s * clamp((t - 84.2) / 0.5), reveal: prog(t, 84.2, 85.6), spacing: 6 });
    }
    M.caption(ctx, t, '网络回答：「从这里，还可能想到什么」', 84.5, 89.6, { size: 32 });
  }

  function activationOverlay(ctx, t, k, P, st) {
    if (t < 90 || t > 121) return;
    const A = win(t, 90.3, 120, 0.8, 1.2);
    // 情境面板
    const x = 80, y = 150, w = 430, h = 214;
    card(ctx, x, y, w, h, A);
    M.text(ctx, '当前情境  c(b, t)', x + 24, y + 36, { size: 20, fam: 'mono', color: C.cyan, alpha: A, align: 'left' });
    const sw = smooth(prog(t, 108.6, 109.8));
    const rowsA = [['分支', '发布准备'], ['目标', '周五上线'], ['最近', '依赖库升级失败']];
    const rowsB = [['分支', '记忆研究'], ['目标', '定义证据关系'], ['最近', '阅读来源谱系']];
    for (let r = 0; r < 3; r++) {
      const ry = y + 86 + r * 40;
      M.text(ctx, rowsA[r][0], x + 24, ry, { size: 17, fam: 'mono', color: C.dim, alpha: A, align: 'left' });
      if (sw < 1) M.text(ctx, rowsA[r][1], x + 100, ry - sw * 12, { size: 21, alpha: A * (1 - sw), align: 'left', reveal: prog(t, 90.8 + r * 0.3, 91.6 + r * 0.3) });
      if (sw > 0) M.text(ctx, rowsB[r][1], x + 100, ry + (1 - sw) * 12, { size: 21, color: [225, 210, 255], alpha: A * sw, align: 'left' });
    }
    // 线索词
    const cueIn = (t0) => {
      const a = win(t, t0, t0 + 2.6, 0.3, 0.5);
      if (a <= 0) return;
      const u = ease.inOut(prog(t, t0, t0 + 1.1));
      const cx = lerp(W + 100, CX + 80, u), cy = 170;
      ctx.strokeStyle = rgba(C.gold, a * 0.8);
      ctx.lineWidth = 1.4;
      M.roundRect(ctx, cx - 70, cy - 26, 140, 52, 26);
      ctx.stroke();
      M.text(ctx, '线索「依赖」', cx, cy, { size: 22, color: C.gold, alpha: a });
      // 线索落向种子节点
      const seeds = t0 < 100 ? ['depfail', 'depcheck'] : ['deriv', 'depfail'];
      const d = prog(t, t0 + 1.1, t0 + 1.9);
      if (d > 0 && d < 1) for (const s of seeds) {
        const p = P[M.ev(s)];
        ctx.fillStyle = rgba(C.gold, a);
        ctx.beginPath(); ctx.arc(lerp(cx, p.x, ease.in(d)), lerp(cy + 26, p.y, ease.in(d)), 4, 0, Math.PI * 2); ctx.fill();
      }
    };
    cueIn(91.6);
    cueIn(109.0);
    // 扩散波纹
    const ripple = (t0, keys, col) => {
      for (const kk of keys) {
        const p = P[M.ev(kk)];
        for (let q = 0; q < 3; q++) {
          const u = prog(t, t0 + q * 0.35, t0 + 2.2 + q * 0.35);
          if (u <= 0 || u >= 1) continue;
          ctx.strokeStyle = rgba(col, (1 - u) * 0.7);
          ctx.lineWidth = 1.5;
          ctx.beginPath(); ctx.arc(p.x, p.y, 8 + u * 190 * p.s, 0, Math.PI * 2); ctx.stroke();
        }
      }
    };
    ripple(93, ['depfail', 'depcheck'], C.gold);
    ripple(106.4, ['incident'], C.cyan);
    ripple(110.5, ['deriv', 'depfail'], C.violet);
    // 公式
    const fA = win(t, 93.4, 101.6, 0.8, 0.6);
    const kNow = Math.min(14, Math.floor(prog(t, 93, 101) * 14));
    M.text(ctx, 'aᵏ⁺¹ = ρ·s + (1 − ρ)·Pᵀ aᵏ', W - 90, 160, { size: 30, fam: 'mono', color: C.ink, alpha: fA, align: 'right', reveal: prog(t, 93.4, 94.8) });
    M.text(ctx, `k = ${kNow}    ρ = 0.32`, W - 90, 206, { size: 18, fam: 'mono', color: C.dim, alpha: fA, align: 'right' });
    const pA = win(t, 101.6, 108.4, 0.6, 0.6);
    M.text(ctx, 'πᵢ = (1−ε)·softmax(zᵢ/T) + ε·q_explore', W - 90, 160, { size: 26, fam: 'mono', color: C.ink, alpha: pA, align: 'right', reveal: prog(t, 101.6, 103) });
    M.text(ctx, '概率只决定「此刻想起谁」，不随机创造过去', W - 90, 206, { size: 18, color: C.dim, alpha: pA, align: 'right' });
    // 概率云
    const cloud = (ctxName, a0, a1) => {
      const cA = win(t, a0, a1, 0.8, 0.8);
      if (cA <= 0) return;
      for (const { i, p } of M.PI[ctxName]) {
        const n = Math.round(10 + p * 160);
        const c = P[i];
        for (let q = 0; q < n; q++) {
          const h1 = hash(i * 31 + q * 7.7), h2 = hash(i * 17 + q * 3.3), h3 = hash(q * 5.1 + i);
          const ang = h1 * Math.PI * 2 + t * (0.6 + h2 * 1.4) * (h3 > 0.5 ? 1 : -1);
          const rr = (12 + 34 * Math.sqrt(h2) * (0.6 + p * 2.5)) * c.s;
          const fl = 0.5 + 0.5 * Math.sin(t * (3 + h3 * 6) + q);
          ctx.fillStyle = rgba(h3 > 0.8 ? C.gold : [200, 240, 255], cA * fl * 0.8);
          ctx.fillRect(c.x + Math.cos(ang) * rr, c.y + Math.sin(ang) * rr * 0.75, 1.8, 1.8);
        }
        if (cA > 0.3) M.text(ctx, `π ${p.toFixed(2)}`, c.x, c.y + 30 * c.s, { size: 13, fam: 'mono', color: C.gold, alpha: cA * 0.8 });
      }
    };
    cloud('release', 101.4, 106.2);
    cloud('research', 113.6, 117.4);
    // 工作集
    const wx = W - 470, wy = 600, ww = 390, wh = 330;
    const wA = win(t, 102.4, 119.6, 0.6, 1);
    card(ctx, wx, wy, ww, wh, wA, C.gold);
    M.text(ctx, '有限工作集 W · 容量 B = 4', wx + 24, wy + 34, { size: 19, fam: 'mono', color: C.gold, alpha: wA, align: 'left' });
    const slots = (ctxName, out0, out1) => {
      M.WORKSET[ctxName].forEach((kk, q) => {
        const t0 = WS_T[ctxName][q];
        if (t < t0) return;
        const i = M.ev(kk), p = P[i];
        const u = ease.inOut(prog(t, t0, t0 + 0.8));
        const sx = wx + 24, sy = wy + 76 + q * 60;
        const out = smooth(prog(t, out0, out1));
        const x = lerp(p.x, sx, u) + out * 420, y = lerp(p.y, sy, u);
        const a = wA * (1 - out);
        if (a <= 0.01) return;
        if (u < 1) M.glowDot(ctx, x, y, 40, C.gold, 0.6 * a);
        card(ctx, x, y - 22, ww - 48, 44, a * u, C.gold);
        M.text(ctx, E[i].label, x + 20, y, { size: 18, alpha: a * u, align: 'left' });
        if (u >= 1) M.text(ctx, '≈', x + ww - 76, y, { size: 16, fam: 'mono', color: C.gold, alpha: a * 0.7 });
      });
    };
    slots('release', 108.6, 109.6);
    slots('research', 200, 201);
    M.caption(ctx, t, '当前情境调节什么更容易浮现 —— 激活沿着联系扩散', 94, 101.2, { size: 30 });
    M.caption(ctx, t, '受约束的概率探索：从候选中取出少量材料，送入有限工作集', 101.6, 106, { size: 30 });
    M.caption(ctx, t, '新取回的经历，又成为下一步的线索', 106.4, 108.8, { size: 30 });
    M.caption(ctx, t, '同一线索，不同情境 —— 唤起不同的经历', 111, 119.6, { size: 36, fam: 'serif', weight: 400 });
    void st; void k;
  }

  function quantitiesOverlay(ctx, t) {
    if (t < 120 || t > 139) return;
    const A = win(t, 120.4, 138.4, 0.8, 1.2);
    const Q = [
      { g: 'L', n: '长期联系', q: '经历之间长期怎样关联？', c: C.cyan },
      { g: 'a', n: '当前激活', q: '此刻什么更容易浮现？', c: [255, 240, 200] },
      { g: 'π', n: '选择概率', q: '本次被抽取的机会？', c: C.violet },
      { g: 'q', n: '证据状态', q: '主张有何支持与冲突？', c: C.gold },
    ];
    const up = ease.inOut(prog(t, 126.4, 127.6));
    Q.forEach((o, j) => {
      const t0 = 120.8 + j * 0.9;
      const a = A * ease.out(prog(t, t0, t0 + 0.8));
      if (a <= 0.01) return;
      const cx = 330 + j * 420;
      const cy = lerp(470, 250, up);
      const w = 360, h = lerp(400, 180, up);
      card(ctx, cx - w / 2, cy - h / 2, w, h, a, o.c);
      const recallFlash = j === 1 ? Math.max(0, ...TM.recall.map((r) => (t > r ? Math.exp(-(t - r) * 4) : 0))) : 0;
      M.text(ctx, o.g, cx, cy - lerp(90, 30, up), { size: lerp(96, 64, up), fam: 'serif', weight: 500, color: M.mixC(o.c, C.white, recallFlash * 0.7), alpha: a });
      if (recallFlash > 0) M.glowDot(ctx, cx, cy - 30, 90, o.c, recallFlash * 0.6);
      M.text(ctx, o.n, cx, cy + lerp(10, 44, up), { size: 26, fam: 'serif', alpha: a, spacing: 4 });
      M.text(ctx, o.q, cx, cy + 58, { size: 18, color: C.dim, alpha: a * (1 - up) });
      // 小型示意
      const ma = a * (1 - up);
      if (ma > 0.01) {
        const my = cy + 130;
        if (j === 0) {
          const th = 1 + 5 * (0.5 + 0.5 * Math.sin(t * 0.8));
          ctx.strokeStyle = rgba(o.c, ma); ctx.lineWidth = th;
          ctx.beginPath(); ctx.moveTo(cx - 90, my); ctx.quadraticCurveTo(cx, my - 30, cx + 90, my); ctx.stroke();
          for (const s of [-1, 1]) { ctx.fillStyle = rgba(C.white, ma); ctx.beginPath(); ctx.arc(cx + s * 90, my, 7, 0, 7); ctx.fill(); }
        } else if (j === 1) {
          const g = 0.5 + 0.5 * Math.sin(t * 2.4);
          M.glowDot(ctx, cx, my - 6, 30 + 30 * g, o.c, 0.7 * ma);
          ctx.fillStyle = rgba(C.white, ma); ctx.beginPath(); ctx.arc(cx, my - 6, 6, 0, 7); ctx.fill();
        } else if (j === 2) {
          const bars = [0.9, 0.55, 0.35, 0.2, 0.12, 0.08];
          const pick = Math.floor(hash(Math.floor(t * 2.5)) * 4);
          bars.forEach((b, q) => {
            const hb = b * 60;
            ctx.fillStyle = rgba(q === pick ? C.gold : o.c, ma * (q === pick ? 1 : 0.6));
            ctx.fillRect(cx - 78 + q * 27, my + 20 - hb, 18, hb);
          });
        } else {
          ctx.strokeStyle = rgba(o.c, ma); ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(cx, my - 36); ctx.lineTo(cx + 30, my - 24); ctx.lineTo(cx + 26, my + 10); ctx.lineTo(cx, my + 28); ctx.lineTo(cx - 26, my + 10); ctx.lineTo(cx - 30, my - 24); ctx.closePath(); ctx.stroke();
          M.text(ctx, '支持 3 · 冲突 1', cx, my - 2, { size: 13, fam: 'mono', color: o.c, alpha: ma });
        }
      }
    });
    // 反复回忆同一来源
    const D = win(t, 127.2, 138.2, 0.8, 1.2);
    if (D > 0) {
      const cx = CX, cy = 590;
      const n = TM.recall.filter((r) => t >= r).length;
      const fl = Math.max(0, ...TM.recall.map((r) => (t > r ? Math.exp(-(t - r) * 3) : 0)));
      // 派生摘要（虚线卡片）
      for (let q = 0; q < Math.min(n, 4); q++) {
        const ap = D * ease.out(prog(t, TM.recall[q], TM.recall[q] + 0.5)) * 0.8;
        const ox = (q - 1.5) * 150, oy = -96 - (q % 2) * 22;
        ctx.save(); ctx.setLineDash([5, 5]);
        ctx.strokeStyle = rgba(C.dim, ap); ctx.lineWidth = 1;
        M.roundRect(ctx, cx + ox - 62, cy + oy - 18, 124, 36, 10); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(cx + ox, cy + oy + 18); ctx.lineTo(cx, cy + 128); ctx.stroke();
        ctx.restore();
        M.text(ctx, ['摘要', '转述', '再摘要', '概览引用'][q], cx + ox, cy + oy, { size: 15, color: C.dim, alpha: ap });
      }
      M.glowDot(ctx, cx, cy, 180, [255, 240, 200], 0.15 * D + fl * 0.35);
      card(ctx, cx - 230, cy - 38, 460, 76, D, [255, 240, 200]);
      M.text(ctx, '「小林偏好简短回复」', cx, cy, { size: 28, fam: 'serif', alpha: D });
      // 单一来源
      ctx.fillStyle = rgba(C.gold, D);
      diamond(ctx, cx, cy + 128, 9, t * 0.4); ctx.fill();
      ctx.strokeStyle = rgba(C.gold, 0.5 * D); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(cx, cy + 38); ctx.lineTo(cx, cy + 119); ctx.stroke();
      M.text(ctx, '来源：10/09 的一条消息', cx, cy + 160, { size: 15, fam: 'mono', color: C.gold, alpha: D * 0.85 });
      // 左：访问次数，右：独立来源
      M.text(ctx, '访问 / 激活', cx - 470, cy - 40, { size: 17, fam: 'mono', color: C.dim, alpha: D });
      M.text(ctx, '×' + n, cx - 470, cy + 14, { size: 64, fam: 'serif', color: M.mixC([255, 240, 200], C.white, fl), alpha: D });
      ctx.fillStyle = rgba([255, 240, 200], D * 0.8);
      ctx.fillRect(cx - 540, cy + 70, 140 * (n / 5), 4);
      M.text(ctx, '独立来源', cx + 470, cy - 40, { size: 17, fam: 'mono', color: C.dim, alpha: D });
      M.text(ctx, '1', cx + 470, cy + 14, { size: 64, fam: 'serif', color: C.gold, alpha: D });
      ctx.fillStyle = rgba(C.gold, D * 0.8);
      ctx.fillRect(cx + 400, cy + 70, 28, 4);
    }
    M.caption(ctx, t, '反复回忆与转述：激活上升，独立证据却没有增加', 128, 132.9, { size: 30 });
    M.caption(ctx, t, '熟悉感，不是真实性。', 133.4, 138, { size: 42, fam: 'serif', weight: 400, spacing: 8 });
  }

  function revisionOverlay(ctx, t, k) {
    if (t < 138 || t > 160) return;
    const A = win(t, 138.5, 158.8, 1, 1.2);
    // 后台整理的扫描弧
    const sw = win(t, 139, 146, 1, 1.2) + 0.4 * win(t, 146, 158, 1, 1);
    if (sw > 0) {
      const ang = (t - 139) * 1.1;
      const core = proj({ x: 0, y: 0, z: 0 }, k);
      const g = ctx.createConicGradient ? ctx.createConicGradient(ang, core.x, core.y) : null;
      if (g) {
        g.addColorStop(0, rgba(C.blue, 0.22 * sw));
        g.addColorStop(0.08, rgba(C.blue, 0));
        g.addColorStop(1, rgba(C.blue, 0));
        ctx.fillStyle = g;
        ctx.beginPath(); ctx.ellipse(core.x, core.y, 760, 760 * Math.cos(k.pitch) * 0.6 + 120, 0, 0, Math.PI * 2); ctx.fill();
      }
    }
    M.caption(ctx, t, '后台：整理事件、发现缺口、更新概览、修订认识 —— 但不直接对外行动', 139.4, 145, { size: 30 });
    // 认识卡片
    const cA = win(t, 144.4, 158.6, 0.8, 1.2);
    if (cA > 0) {
      const hit = 148.5;
      const v2 = smooth(prog(t, hit + 0.1, hit + 1.0));
      const shrink = ease.inOut(prog(t, 150.2, 151.6));
      // v1 卡片（修订后缩为历史）
      const x1 = lerp(CX - 380, 250, shrink), y1 = lerp(380, 700, shrink), s1 = lerp(1, 0.62, shrink);
      const a1 = cA * (shrink > 0 ? lerp(1, 0.55, shrink) : 1 - v2);
      if (a1 > 0.01) {
        ctx.save(); ctx.translate(x1, y1); ctx.scale(s1, s1);
        const shake = t > hit && t < hit + 0.5 ? Math.sin(t * 90) * 6 * (1 - (t - hit) / 0.5) : 0;
        ctx.translate(shake, 0);
        card(ctx, 0, 0, 760, 200, a1, C.cyan);
        M.text(ctx, '当前认识 · v1', 28, 36, { size: 18, fam: 'mono', color: C.cyan, alpha: a1, align: 'left' });
        M.text(ctx, '「方法 A 很可靠」', 380, 98, { size: 40, fam: 'serif', alpha: a1, reveal: prog(t, 144.6, 145.6) });
        M.text(ctx, '依据  3 次成功（10/03 · 10/08 · 10/12）', 28, 166, { size: 17, fam: 'mono', color: C.gold, alpha: a1 * 0.9, align: 'left' });
        ctx.restore();
      }
      // 新证据飞入
      const inc = win(t, 146.6, hit + 0.15, 0.3, 0.1);
      if (inc > 0) {
        const u = ease.in(prog(t, 146.8, hit));
        const x = lerp(W + 60, CX + 380, u), y = lerp(250, 470, u);
        ctx.fillStyle = rgba(C.magenta, inc);
        diamond(ctx, x, y, 11, t * 3); ctx.fill();
        M.glowDot(ctx, x, y, 60, C.magenta, 0.7 * inc);
        for (let q = 1; q < 8; q++) { ctx.fillStyle = rgba(C.magenta, inc * (1 - q / 8) * 0.6); ctx.fillRect(x + q * 22 * (1 - u * 0.5), y - q * 9 * (1 - u * 0.5), 3, 3); }
        M.text(ctx, '新来源 · 10/19 · 依赖未就绪时失败', x - 20, y - 34, { size: 17, fam: 'mono', color: C.magenta, alpha: inc, align: 'right' });
      }
      if (t > hit && t < hit + 1.2) {
        const u = (t - hit) / 1.2;
        ctx.strokeStyle = rgba(C.magenta, (1 - u) * 0.9); ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(CX + 380, 470, 20 + u * 260, 0, Math.PI * 2); ctx.stroke();
      }
      // v2 卡片
      if (v2 > 0) {
        const a2 = cA * v2;
        const x2 = CX - 380 + (1 - v2) * 30, y2 = lerp(380, 330, shrink);
        card(ctx, x2, y2, 760, 220, a2, C.gold);
        M.text(ctx, '当前认识 · v2', x2 + 28, y2 + 36, { size: 18, fam: 'mono', color: C.gold, alpha: a2, align: 'left' });
        M.text(ctx, '「方法 A 在依赖就绪时可靠」', x2 + 380, y2 + 100, { size: 40, fam: 'serif', alpha: a2, reveal: prog(t, hit + 0.2, hit + 1.4) });
        M.text(ctx, '条件  依赖就绪   ·   反例  10/19   ·   依据  4 条来源', x2 + 28, y2 + 180, { size: 17, fam: 'mono', color: C.gold, alpha: a2 * 0.9, align: 'left' });
      }
      // 版本链
      const ch = cA * ease.out(prog(t, 151.4, 152.6));
      if (ch > 0) {
        const ax = 250 + 760 * 0.62 + 16, ay = 700 + 62;
        const bx = CX - 50, by = 700 + 62;
        ctx.strokeStyle = rgba(C.magenta, ch * 0.8); ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(lerp(ax, bx + 380, ch), by); ctx.stroke();
        M.text(ctx, '因 10/19 反例而修订 · 旧版本与依据保留', lerp(ax, bx + 380, 0.5), by - 22, { size: 16, fam: 'mono', color: C.magenta, alpha: ch });
        ctx.fillStyle = rgba(C.gold, ch);
        ctx.beginPath(); ctx.arc(bx + 380, by, 6, 0, 7); ctx.fill();
        M.text(ctx, 'v2 · 当前', bx + 400, by, { size: 16, fam: 'mono', color: C.gold, alpha: ch, align: 'left' });
      }
    }
    M.caption(ctx, t, '保留反例与修订依据，而不是只留下一个流畅的故事', 150.6, 154.6, { size: 30 });
    M.caption(ctx, t, '「我曾这样认为；后来，是这些经历让我改变。」', 154.9, 158.8, { size: 38, fam: 'serif', weight: 400, spacing: 4 });
    void A;
  }

  // 分支
  const BR = [
    { name: '分支 · 发布准备', col: C.cyan, y: 330, x0: 380, x1: 1560 },
    { name: '分支 · 日常交流', col: C.coral, y: 450, x0: 540, x1: 1380 },
    { name: '分支 · 记忆研究', col: C.violet, y: 850, x0: 420, x1: 1620 },
  ];
  const TRUNK_Y = 640;
  function brPoint(b, u) {
    // 起点、拱起、回到主干的三次曲线
    const p0 = { x: b.x0, y: TRUNK_Y }, p3 = { x: b.x1, y: TRUNK_Y };
    const p1 = { x: lerp(b.x0, b.x1, 0.22), y: b.y }, p2 = { x: lerp(b.x0, b.x1, 0.78), y: b.y };
    const m = 1 - u;
    return {
      x: m * m * m * p0.x + 3 * m * m * u * p1.x + 3 * m * u * u * p2.x + u * u * u * p3.x,
      y: m * m * m * p0.y + 3 * m * m * u * p1.y + 3 * m * u * u * p2.y + u * u * u * p3.y,
    };
  }
  TM.branchDraw = [160.2, 160.9, 161.6];
  function branchesOverlay(ctx, t) {
    if (t < 158.5 || t > 178) return;
    const A = win(t, 159.2, 176.6, 1, 1.4);
    // 主干：共享的真实历史
    ctx.lineWidth = 3;
    const tg = ctx.createLinearGradient(0, 0, W, 0);
    tg.addColorStop(0, rgba(C.gold, 0)); tg.addColorStop(0.15, rgba(C.gold, 0.85 * A)); tg.addColorStop(0.85, rgba(C.gold, 0.85 * A)); tg.addColorStop(1, rgba(C.gold, 0));
    ctx.strokeStyle = tg;
    ctx.beginPath(); ctx.moveTo(0, TRUNK_Y); ctx.lineTo(W * ease.out(prog(t, 159.2, 160.6)), TRUNK_Y); ctx.stroke();
    for (let q = 0; q < 70; q++) {
      const u = ((t * 0.06 + hash(q)) % 1);
      ctx.fillStyle = rgba(C.gold, A * 0.7 * Math.sin(u * Math.PI));
      ctx.fillRect(u * W, TRUNK_Y - 1.5 + (hash(q * 3) - 0.5) * 8, 3, 3);
    }
    M.text(ctx, '共享的经历 · 一个个体', 120, TRUNK_Y + 34, { size: 19, fam: 'serif', color: C.gold, alpha: A, align: 'left' });
    M.text(ctx, '时间 →', W - 120, TRUNK_Y + 34, { size: 15, fam: 'mono', color: C.gold, alpha: A * 0.7, align: 'right' });
    BR.forEach((b, j) => {
      const d = ease.inOut(prog(t, TM.branchDraw[j], TM.branchDraw[j] + 1.4));
      if (d <= 0) return;
      ctx.strokeStyle = rgba(b.col, 0.75 * A); ctx.lineWidth = 2;
      ctx.beginPath();
      for (let s = 0; s <= 60 * d; s++) { const p = brPoint(b, s / 60); if (s) ctx.lineTo(p.x, p.y); else ctx.moveTo(p.x, p.y); }
      ctx.stroke();
      for (let q = 0; q < 16; q++) {
        const u = ((t * 0.09 + hash(q + j * 40)) % 1);
        if (u > d) continue;
        const p = brPoint(b, u);
        ctx.fillStyle = rgba(b.col, A * 0.9);
        ctx.beginPath(); ctx.arc(p.x, p.y, 2.2, 0, 7); ctx.fill();
      }
      const lp = brPoint(b, 0.5);
      M.text(ctx, b.name, lp.x, lp.y + (b.y < TRUNK_Y ? -30 : 34), { size: 21, fam: 'serif', color: M.mixC(b.col, C.white, 0.3), alpha: A * d, reveal: prog(t, TM.branchDraw[j] + 0.6, TM.branchDraw[j] + 1.6) });
    });
    // 分支甲：已发送的消息 → 进入共享历史
    const sA = BR[0], pA = brPoint(sA, 0.36);
    const e1 = win(t, 163.6, 176.6, 0.4, 1.4);
    if (e1 > 0) {
      const drop = ease.inOut(prog(t, 164.8, 165.8));
      const x = pA.x, y = lerp(pA.y, TRUNK_Y, drop);
      const col = M.mixC(C.cyan, C.gold, drop);
      M.glowDot(ctx, x, y, 44, col, 0.6 * e1);
      ctx.fillStyle = rgba(C.white, e1); ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.fill();
      if (drop > 0 && drop < 1) { ctx.strokeStyle = rgba(col, 0.5 * e1); ctx.setLineDash([3, 4]); ctx.beginPath(); ctx.moveTo(pA.x, pA.y); ctx.lineTo(x, y); ctx.stroke(); ctx.setLineDash([]); }
      M.text(ctx, drop < 0.5 ? '已发送：延期通知' : '已发生的行动 · 进入共享历史', x + 18, y - 20, { size: 17, alpha: e1, align: 'left' });
    }
    // 分支乙：已记录 · 本分支未知
    const sB = BR[2], pB = brPoint(sB, 0.3);
    const e2 = win(t, 166.4, 176.6, 0.6, 1.4);
    if (e2 > 0) {
      ctx.strokeStyle = rgba(C.violet, 0.8 * e2); ctx.lineWidth = 1.4; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.arc(pB.x, pB.y, 9, 0, 7); ctx.stroke(); ctx.setLineDash([]);
      ctx.strokeStyle = rgba(C.violet, 0.3 * e2);
      ctx.beginPath(); ctx.moveTo(pA.x, TRUNK_Y); ctx.quadraticCurveTo((pA.x + pB.x) / 2, TRUNK_Y + 80, pB.x, pB.y - 10); ctx.stroke();
      M.text(ctx, '已记录 · 本分支尚未读取', pB.x + 20, pB.y + 26, { size: 17, color: [220, 205, 255], alpha: e2, align: 'left' });
    }
    // 分支甲：计划 ≠ 事实
    const pP = brPoint(sA, 0.68);
    const e3 = win(t, 168, 176.6, 0.5, 1.4);
    if (e3 > 0) {
      ctx.strokeStyle = rgba(C.cyan, e3); ctx.lineWidth = 1.5; ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.arc(pP.x, pP.y, 10, 0, 7); ctx.stroke(); ctx.setLineDash([]);
      const tryd = prog(t, 168.8, 169.6), back = prog(t, 169.6, 170.4);
      const ty = pP.y + 70 * ease.out(tryd) * (1 - ease.inOut(back));
      if (tryd > 0 && back < 1) {
        ctx.strokeStyle = rgba(C.magenta, 0.8 * e3);
        ctx.beginPath(); ctx.moveTo(pP.x - 18, pP.y + 84); ctx.lineTo(pP.x + 18, pP.y + 84); ctx.stroke();
        ctx.fillStyle = rgba(C.cyan, 0.7 * e3); ctx.beginPath(); ctx.arc(pP.x, ty, 4, 0, 7); ctx.fill();
      }
      M.text(ctx, '计划：可能周五发送', pP.x + 20, pP.y - 22, { size: 17, alpha: e3, align: 'left' });
      M.text(ctx, '计划 ≠ 事实', pP.x + 20, pP.y + 8, { size: 16, fam: 'mono', color: C.magenta, alpha: e3, align: 'left' });
    }
    M.caption(ctx, t, '一个个体，多个活动分支 —— 共享同一段真实的历史', 160.4, 165.6, { size: 30 });
    M.caption(ctx, t, '记录属于哪个活动，与分支此刻知道什么，是两回事', 166, 170.6, { size: 30 });
    M.caption(ctx, t, '归并的是理解，而不是改写已经发生的事', 171, 176.6, { size: 34, fam: 'serif', weight: 400 });
  }

  // 派生投影
  const PJ = [
    { n: '自我模型', l1: '「我在承诺时间前，常漏掉验证」', l2: '来源 ×6 · 可修订', src: 'skipver', col: C.cyan },
    { n: '关系理解', l1: '「小林偏好简短回复」', l2: '来源 ×1 · 支持有限', src: 'brief', col: C.coral },
    { n: '经验与技能', l1: '「先核实依赖，再承诺时间」', l2: '适用：有外部依赖时 · 反例 10/19', src: 'depcheck', col: C.gold },
    { n: '前瞻记忆', l1: '「周五线索出现时，准备检查材料」', l2: '是否执行，由宿主决定', src: 'prosp', col: C.violet },
  ];
  TM.projT = PJ.map((p, j) => 179 + j * 1.2);
  function projectionOverlay(ctx, t, k, P) {
    if (t < 176.5 || t > 193) return;
    const A = win(t, 177.4, 191.6, 1, 1.2);
    PJ.forEach((o, j) => {
      const t0 = TM.projT[j];
      const a = A * ease.out(prog(t, t0, t0 + 1));
      if (a <= 0.01) return;
      const cx = 300 + j * 440, cy = 300 + (j === 1 || j === 2 ? -36 : 0) - 10 * Math.sin(t * 0.8 + j);
      const w = 390, h = 170;
      // 光柱（降低底部汇聚处亮度，避免多光柱叠加过曝）
      const sp = P[M.ev(o.src)];
      const bg = ctx.createLinearGradient(0, sp.y, 0, cy + h / 2);
      bg.addColorStop(0, rgba(o.col, 0.24 * a)); bg.addColorStop(1, rgba(o.col, 0.025 * a));
      ctx.fillStyle = bg;
      ctx.beginPath(); ctx.moveTo(sp.x - 2, sp.y); ctx.lineTo(cx - w / 2 + 20, cy + h / 2); ctx.lineTo(cx + w / 2 - 20, cy + h / 2); ctx.lineTo(sp.x + 2, sp.y); ctx.closePath(); ctx.fill();
      M.glowDot(ctx, sp.x, sp.y, 34, o.col, 0.4 * a);
      // 玻璃面板
      ctx.save();
      ctx.fillStyle = rgba(M.mixC([12, 18, 34], o.col, 0.08), 0.84 * a);
      M.roundRect(ctx, cx - w / 2, cy - h / 2, w, h, 16); ctx.fill();
      ctx.strokeStyle = rgba(o.col, 0.65 * a); ctx.lineWidth = 1.2; ctx.stroke();
      ctx.restore();
      M.text(ctx, o.n, cx, cy - 46, { size: 26, fam: 'serif', weight: 600, color: M.mixC(o.col, C.white, 0.4), alpha: a, spacing: 4 });
      M.text(ctx, o.l1, cx, cy + 8, { size: 20, alpha: a, reveal: prog(t, t0 + 0.4, t0 + 1.2) });
      M.text(ctx, o.l2, cx, cy + 52, { size: 15, fam: 'mono', color: o.col, alpha: a * 0.9 });
    });
    M.caption(ctx, t, '人格、自我、技能与前瞻 —— 建立在核心关系之上的投影', 178.2, 184.6, { size: 30 });
    M.caption(ctx, t, '由经历派生，可以修订；不是另造的一份设定', 185, 191.4, { size: 34, fam: 'serif', weight: 400 });
  }

  function finalOverlay(ctx, t, k, P) {
    if (t < 191.5) return;
    const core = proj({ x: 0, y: 0, z: 0 }, k);
    // 核心
    const cA = smooth(prog(t, 192.5, 195)) * (1 - 0.65 * smooth(prog(t, 207, 210)));
    if (cA > 0) {
      const pul = 0.85 + 0.15 * Math.sin(t * 2.2);
      M.glowDot(ctx, core.x, core.y, 190 * pul, C.gold, 0.14 * cA);
      M.glowDot(ctx, core.x, core.y, 44, [255, 245, 220], 0.36 * cA);
      ctx.strokeStyle = rgba([255, 240, 210], 0.38 * cA); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(core.x - 190 * pul, core.y); ctx.lineTo(core.x + 190 * pul, core.y); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(core.x, core.y - 60 * pul); ctx.lineTo(core.x, core.y + 60 * pul); ctx.stroke();
      const la = cA * win(t, 193.5, 206.5, 1, 1);
      if (la > 0.01) {
        ctx.save();
        ctx.fillStyle = rgba([5, 9, 20], 0.72 * la);
        M.roundRect(ctx, core.x - 104, core.y + 38, 208, 86, 16);
        ctx.fill();
        ctx.restore();
        M.text(ctx, '灵', core.x, core.y + 66, { size: 34, fam: 'serif', weight: 600, color: [255, 240, 210], alpha: la });
        M.text(ctx, '一个持续存在的个体', core.x, core.y + 104, { size: 17, color: C.dim, alpha: la });
      }
    }
    // 宿主
    const hA = win(t, 193, 206.8, 1, 1.2);
    if (hA > 0) {
      const hx = 1660, hy = 540;
      ctx.strokeStyle = rgba(C.white, 0.8 * hA); ctx.lineWidth = 1.6;
      ctx.beginPath();
      for (let q = 0; q <= 6; q++) { const a = (q / 6) * Math.PI * 2 + t * 0.2; const x = hx + Math.cos(a) * 54, y = hy + Math.sin(a) * 54; if (q) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
      ctx.stroke();
      M.text(ctx, '宿主', hx, hy, { size: 24, fam: 'serif', weight: 600, alpha: hA });
      M.text(ctx, '决定外部行动', hx, hy + 84, { size: 16, fam: 'mono', color: C.dim, alpha: hA });
      // 工作集流向宿主
      for (let q = 0; q < 26; q++) {
        const u = ((t * 0.35 + hash(q * 2.2)) % 1);
        const mx = (core.x + hx) / 2, my = Math.min(core.y, hy) - 160;
        const x = (1 - u) * (1 - u) * core.x + 2 * (1 - u) * u * mx + u * u * (hx - 60);
        const y = (1 - u) * (1 - u) * core.y + 2 * (1 - u) * u * my + u * u * hy;
        ctx.fillStyle = rgba(C.gold, hA * Math.sin(u * Math.PI) * 0.9);
        ctx.beginPath(); ctx.arc(x, y, 2.4, 0, 7); ctx.fill();
      }
      M.text(ctx, '准备材料 →', (core.x + hx) / 2 + 40, Math.min(core.y, hy) - 108, { size: 16, fam: 'mono', color: C.gold, alpha: hA * 0.9 });
      // 行动向外
      const ar = (t * 0.8) % 1;
      ctx.strokeStyle = rgba(C.white, hA * 0.7); ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(hx + 62, hy); ctx.lineTo(W - 40, hy); ctx.stroke();
      ctx.fillStyle = rgba(C.white, hA * (1 - ar)); ctx.beginPath(); ctx.arc(lerp(hx + 62, W - 40, ar), hy, 3, 0, 7); ctx.fill();
      M.text(ctx, '行动', W - 70, hy - 24, { size: 16, fam: 'mono', color: C.ink, alpha: hA * 0.8 });
    }
    // 模型环
    const ring = (label, t0, t1, col) => {
      const a = win(t, t0, t1, 0.8, 0.8);
      if (a <= 0.01) return;
      const dis = smooth(prog(t, t1 - 0.8, t1));
      const Rr = 430;
      ctx.lineWidth = 1.4;
      let front = null;
      for (let s = 0; s < 120; s++) {
        if (s % 2) continue;
        const a0 = (s / 120) * Math.PI * 2 + t * 0.15, a1 = ((s + 1) / 120) * Math.PI * 2 + t * 0.15;
        const sc = 1 + dis * hash(s) * 0.5;
        const p0 = proj({ x: Math.cos(a0) * Rr * sc, y: 0, z: Math.sin(a0) * Rr * sc }, k), p1 = proj({ x: Math.cos(a1) * Rr * sc, y: 0, z: Math.sin(a1) * Rr * sc }, k);
        ctx.strokeStyle = rgba(col, a * (1 - dis) * clamp(p0.s));
        ctx.beginPath(); ctx.moveTo(p0.x, p0.y); ctx.lineTo(p1.x, p1.y); ctx.stroke();
        if (!front || p0.y > front.y) front = p0;
      }
      if (front) M.text(ctx, label, front.x, front.y + 30, { size: 18, fam: 'mono', color: col, alpha: a * (1 - dis) });
    };
    ring('模型 A · 本地', 198.6, 201.2, C.cyan);
    ring('模型 B · 外部', 202.4, 206.8, C.violet);
    const keep = win(t, 200.8, 206.4, 0.6, 0.8);
    if (keep > 0) M.text(ctx, '保留：来源 · 事件解释 · 修订历史        重建：向量 · 索引 · 派生结构', CX, 190, { size: 20, fam: 'mono', color: C.gold, alpha: keep });
    M.caption(ctx, t, '记忆可以自主整理与联想；外部行动，由宿主决定', 193.4, 198.6, { size: 30 });
    M.caption(ctx, t, '更换模型：来源与历史仍在，向量与结构可以重建', 199.4, 206.6, { size: 30 });
    // 主题句
    const lines = [['让过去', 207.6], ['准确、灵活、可修正地', 209.0], ['参与现在。', 210.6]];
    const la = win(t, 207.4, 214.2, 0.6, 0.9);
    if (la > 0) {
      const bg = ctx.createRadialGradient(CX, 520, 40, CX, 520, 480);
      bg.addColorStop(0, rgba([4, 7, 16], 0.72 * la));
      bg.addColorStop(0.65, rgba([4, 7, 16], 0.45 * la));
      bg.addColorStop(1, rgba([4, 7, 16], 0));
      ctx.fillStyle = bg;
      ctx.fillRect(CX - 520, 260, 1040, 520);
      lines.forEach(([s, t0], j) => {
        M.text(ctx, s, CX, 430 + j * 92, { size: j === 1 ? 58 : 66, fam: 'serif', weight: 500, alpha: la * clamp((t - t0) / 0.4), reveal: prog(t, t0, t0 + 0.9), spacing: 10, rise: 18 });
      });
    }
    // 片尾标题
    const T = win(t, 214.6, 222.8, 1.2, 1.6);
    if (T > 0) {
      const bg = ctx.createRadialGradient(CX, 540, 60, CX, 540, 640);
      bg.addColorStop(0, rgba([4, 7, 16], 0.75 * T));
      bg.addColorStop(0.7, rgba([4, 7, 16], 0.4 * T));
      bg.addColorStop(1, rgba([4, 7, 16], 0));
      ctx.fillStyle = bg;
      ctx.fillRect(CX - 680, 240, 1360, 640);
      M.text(ctx, '持续个体的情境化长期记忆', CX, 420, { size: 70, fam: 'serif', weight: 600, alpha: T, reveal: prog(t, 214.6, 216.0), spacing: 8, rise: 16 });
      M.text(ctx, 'CONTEXTUAL LONG-TERM MEMORY FOR PERSISTENT AGENTS', CX, 498, { size: 18, fam: 'mono', color: C.dim, alpha: T * clamp((t - 215.6) / 0.8), spacing: 5 });
      const lw = 520 * ease.inOut(prog(t, 216, 217.4));
      ctx.fillStyle = rgba(C.gold, 0.7 * T); ctx.fillRect(CX - lw / 2, 546, lw, 1.2);
      M.text(ctx, '背景概览  ·  事件联想  ·  可修订认识', CX, 598, { size: 26, fam: 'serif', alpha: T * clamp((t - 216.6) / 0.8), spacing: 6 });
      M.text(ctx, 'LingNexus', CX, 760, { size: 22, fam: 'mono', color: C.gold, alpha: T * clamp((t - 217.6) / 0.8), spacing: 8 });
      M.text(ctx, '实现可以重建  ·  认知可以更新  ·  方向保持连续', CX, 806, { size: 20, color: C.dim, alpha: T * clamp((t - 218.2) / 0.8), spacing: 4 });
    }
    void P;
  }

  // ======================= 主渲染 =======================
  M.render = function (ctx, t) {
    const k = cam(t);
    ctx.save();
    ctx.globalCompositeOperation = 'source-over';
    drawBackground(ctx, t, k);
    const st = worldState(t);
    const P = E.map((e) => proj(evPos(e.i, t), k));
    st_srcK = st.srcK;
    drawArchive(ctx, t, k, P);
    clustersUnder(ctx, t, k, P);
    drawWorld(ctx, t, k, st, P);
    const info = drawSourcesAndEvents(ctx, t, k, st, P);
    prologue(ctx, t);
    archiveOverlay(ctx, t);
    eventsOverlay(ctx, t, info);
    clustersOver(ctx, t, k, P);
    networkOverlay(ctx, t);
    activationOverlay(ctx, t, k, P, st);
    quantitiesOverlay(ctx, t);
    revisionOverlay(ctx, t, k);
    branchesOverlay(ctx, t);
    projectionOverlay(ctx, t, k, P);
    finalOverlay(ctx, t, k, P);
    header(ctx, t);
    ctx.restore();

    // 后期参数
    const sh = TM.SHATTER;
    const glitch = Math.max(win(t, sh - 0.45, sh + 0.35, 0.15, 0.3), 0.45 * win(t, 148.45, 148.9, 0.05, 0.3), 0.35 * win(t, 200.3, 200.9, 0.05, 0.4), 0.25 * win(t, 108.6, 108.95, 0.05, 0.2));
    const flash = (t > sh ? 0.28 * Math.exp(-(t - sh) * 5) : 0) + (t > 148.5 ? 0.08 * Math.exp(-(t - 148.5) * 6) : 0) + (t > 214.6 ? 0.06 * Math.exp(-(t - 214.6) * 3) : 0);
    const night = win(t, 138, 159, 2, 2);
    const fade = smooth(prog(t, 0, 1.2)) * (1 - smooth(prog(t, 222.6, 225)));
    return {
      time: t, bloom: 0.56 + 0.16 * win(t, 207, 214, 1, 1), threshold: 0.62, aberr: 0.0011 + glitch * 0.01,
      grain: 0.026, vig: 0.86, flash, fade, glitch,
      tint: [lerp(1, 0.86, night), lerp(1, 0.93, night), lerp(1, 1.16, night)],
    };
  };
})();
