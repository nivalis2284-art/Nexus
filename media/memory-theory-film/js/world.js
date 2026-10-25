/* 记忆世界：来源记录、事件块、记忆团、关联联系，以及各场景的布局。
 * 全部由固定种子生成，保证每次播放与导出一致。 */
(function () {
  const M = window.MF;
  const R = M.rng(20261023);

  // 团成员：P=人物·小林  J=项目·发布  T=时间·第三周  D=主题·延期
  // grp：在三维关联网络中的大致区域（发布 / 研究 / 生活 / 插件）
  const RAW = [
    ['commit', '承诺：周五前完成测试', 'PJT', 'rel', 3],
    ['propose', '小林提出周五发布', 'PJTD', 'rel', 2],
    ['depfail', '依赖库升级失败', 'JTD', 'rel', 4],
    ['delayplan', '讨论延期方案', 'PJD', 'rel', 4],
    ['brief', '小林偏好简短回复', 'P', 'life', 2],
    ['readagent', '阅读 ReadAgent 论文', '', 'res', 2],
    ['plugerr', '插件重载报错', 'T', 'plug', 3],
    ['plugfix', '修复插件加载顺序', 'T', 'plug', 3],
    ['clustersk', '记忆团草图', 'T', 'res', 2],
    ['incident', '上月的发布事故', 'JD', 'rel', 4],
    ['skipver', '回顾：验证被跳过', 'J', 'rel', 3],
    ['walk', '与阿澈聊散步', '', 'life', 2],
    ['book', '阿澈推荐一本书', '', 'life', 2],
    ['newdate', '小林确认新截止日', 'PJTD', 'rel', 4],
    ['split', '测试分工调整', 'PJT', 'rel', 3],
    ['watch', '网页监听上线', 'J', 'plug', 3],
    ['daily', '长期交代：每日回顾', '', 'life', 2],
    ['correct', '用户纠正：不是周四', 'PTD', 'rel', 3],
    ['depcheck', '依赖就绪检查', 'JT', 'rel', 3],
    ['raptor', 'RAPTOR 层级摘要', '', 'res', 2],
    ['cache', '失败案例：缓存未清', 'J', 'rel', 3],
    ['bday', '小林的生日', 'P', 'life', 2],
    ['weekly', '周报草稿', 'JT', 'rel', 2],
    ['spread', '研究：扩散激活', '', 'res', 3],
    ['contin', '对话：何为连续性', '', 'res', 3],
    ['night', '夜间整理笔记', 'T', 'res', 2],
    ['prosp', '前瞻：周五提醒检查', 'JT', 'rel', 2],
    ['otherinc', '另一项目的事故', 'D', 'plug', 3],
    ['rain', '雨天，暂停监听', '', 'life', 2],
    ['ask', '小林询问进度', 'PT', 'rel', 2],
    ['notes', '发布说明定稿', 'J', 'rel', 3],
    ['sent', '延期通知已发送', 'PJTD', 'rel', 3],
    ['swap', '模型切换实验', '', 'res', 3],
    ['zip', '插件 ZIP 导入', '', 'plug', 2],
    ['tests', '阿澈：先写测试', '', 'life', 2],
    ['olddelay', '上上周的延期', 'PD', 'rel', 3],
    ['deriv', '派生依赖的定义', '', 'res', 3],
    ['evid', '证据关系：派生不增来源', '', 'res', 3],
    ['lineage', '来源谱系草图', '', 'res', 2],
    ['tea', '午后的茶', '', 'life', 1],
  ];

  const events = RAW.map((r, i) => ({
    i, key: r[0], label: r[1], mem: r[2], grp: r[3], nsrc: r[4],
    P: r[2].includes('P'), J: r[2].includes('J'), T: r[2].includes('T'), D: r[2].includes('D'),
    phase: R() * Math.PI * 2, size: 0.8 + R() * 0.5,
  }));
  const byKey = {};
  for (const e of events) byKey[e.key] = e.i;
  M.events = events;
  M.ev = (k) => byKey[k];

  // ---------- 来源记录 ----------
  const SRC_KINDS = ['消息', '文件', '工具', '纠正', '网页', '反思'];
  const sources = [];
  for (const e of events) {
    e.src = [];
    for (let k = 0; k < e.nsrc; k++) {
      const s = { i: sources.length, ev: e.i, kind: SRC_KINDS[Math.floor(R() * SRC_KINDS.length)], ang: R() * Math.PI * 2, rad: 26 + R() * 22, spin: (R() - 0.5) * 0.8 };
      sources.push(s);
      e.src.push(s.i);
    }
  }
  M.sources = sources;

  // ---------- 联系（事件之间，可跨团） ----------
  const TYPES = {
    sim: { name: '相似问题', color: M.C.cyan },
    seq: { name: '时间接续', color: [200, 220, 255] },
    prom: { name: '承诺 → 履行', color: M.C.gold, dir: true },
    corr: { name: '纠正', color: M.C.magenta, dir: true },
    ana: { name: '类比', color: M.C.violet },
  };
  M.LTYPES = TYPES;
  const LINK_SPEC = [
    ['propose', 'delayplan', 'seq', 0.9], ['delayplan', 'newdate', 'seq', 0.9], ['newdate', 'sent', 'seq', 0.9],
    ['plugerr', 'plugfix', 'seq', 0.8], ['readagent', 'raptor', 'sim', 0.7], ['raptor', 'clustersk', 'sim', 0.7],
    ['clustersk', 'spread', 'seq', 0.6], ['spread', 'contin', 'ana', 0.5], ['contin', 'swap', 'ana', 0.6],
    ['commit', 'depcheck', 'prom', 1.0], ['prosp', 'depcheck', 'prom', 0.9], ['commit', 'notes', 'prom', 0.7],
    ['correct', 'propose', 'corr', 0.9], ['correct', 'newdate', 'seq', 0.6],
    ['incident', 'otherinc', 'ana', 0.95], ['olddelay', 'delayplan', 'ana', 0.7],
    ['depfail', 'cache', 'sim', 0.85], ['depfail', 'depcheck', 'seq', 0.8], ['skipver', 'tests', 'sim', 0.8],
    ['incident', 'skipver', 'seq', 0.8], ['brief', 'ask', 'sim', 0.5], ['ask', 'split', 'seq', 0.6],
    ['split', 'commit', 'seq', 0.7], ['watch', 'rain', 'seq', 0.4], ['plugfix', 'zip', 'sim', 0.5],
    ['walk', 'book', 'seq', 0.5], ['book', 'contin', 'ana', 0.4], ['daily', 'night', 'sim', 0.6],
    ['night', 'clustersk', 'seq', 0.5], ['deriv', 'evid', 'sim', 0.9], ['evid', 'lineage', 'sim', 0.8],
    ['lineage', 'readagent', 'ana', 0.4], ['deriv', 'depfail', 'ana', 0.45], ['weekly', 'notes', 'seq', 0.5],
    ['cache', 'otherinc', 'sim', 0.5], ['tea', 'walk', 'sim', 0.3], ['bday', 'brief', 'seq', 0.3],
    ['swap', 'lineage', 'seq', 0.6], ['tests', 'depcheck', 'sim', 0.5], ['spread', 'deriv', 'seq', 0.4],
    ['watch', 'plugerr', 'seq', 0.5], ['delayplan', 'depfail', 'seq', 0.7],
  ];
  const links = LINK_SPEC.map(([a, b, ty, w], i) => ({ i, a: byKey[a], b: byKey[b], type: ty, w, bow: (R() - 0.5) * 0.5 }));
  M.links = links;
  M.TYPE_ORDER = ['sim', 'seq', 'prom', 'corr', 'ana'];

  // ---------- 背景星尘（未标注的大量经历） ----------
  const dust = [];
  for (let k = 0; k < 420; k++) {
    const arm = k % 3;
    const r = 120 + Math.pow(R(), 0.7) * 760;
    const a = arm * (Math.PI * 2 / 3) + r * 0.0055 + M.gauss(R) * 0.35;
    dust.push({
      x: Math.cos(a) * r, z: Math.sin(a) * r, y: M.gauss(R) * (40 + r * 0.06),
      s: 0.4 + R() * 1.4, tw: R() * 10, hue: R(),
    });
  }
  M.dust = dust;
  const dustLinks = [];
  for (let k = 0; k < 160; k++) {
    const a = Math.floor(R() * dust.length);
    let best = -1, bd = 1e9;
    for (let j = 0; j < 30; j++) {
      const b = Math.floor(R() * dust.length);
      if (b === a) continue;
      const d = Math.hypot(dust[a].x - dust[b].x, dust[a].y - dust[b].y, dust[a].z - dust[b].z);
      if (d < bd) { bd = d; best = b; }
    }
    if (best >= 0 && bd < 220) dustLinks.push([a, best]);
  }
  M.dustLinks = dustLinks;

  // ---------- 布局 ----------
  const CX = M.W / 2, CY = M.H / 2;
  // 1) 经历流：按时间顺序排成一条缓慢起伏的长带
  const order = events.map((e) => e.i).sort((a, b) => M.hash(a * 3.1) - M.hash(b * 3.1));
  const LS = [];
  order.forEach((ei, k) => {
    const col = k % 10, row = Math.floor(k / 10);
    const x = -760 + col * 168 + (row % 2) * 84;
    const y = -255 + row * 170 + Math.sin(col * 0.9 + row) * 26;
    LS[ei] = { x, y, z: 0 };
  });
  // 2) 记忆团：四个重叠区域，成员位置取所属团中心的平均，并经确定性斥力松弛避免重叠
  const CC = { P: { x: -330, y: -155 }, J: { x: 250, y: -165 }, T: { x: 185, y: 185 }, D: { x: -245, y: 155 } };
  M.CLUSTER_CENTERS = CC;
  const LC = [];
  const outer = events.filter((e) => !e.mem);
  events.forEach((e) => {
    if (e.mem) {
      let x = 0, y = 0;
      const ks = [...e.mem];
      for (const k of ks) { x += CC[k].x; y += CC[k].y; }
      x /= ks.length; y /= ks.length;
      const spread = ks.length === 4 ? 46 : ks.length === 1 ? 130 : 95;
      const a = M.hash(e.i * 7.7) * Math.PI * 2, r = spread * (0.35 + 0.65 * M.hash(e.i * 2.3));
      LC[e.i] = { x: x + Math.cos(a) * r, y: y + Math.sin(a) * r, z: 0 };
    } else {
      const k = outer.indexOf(e);
      const a = (k / outer.length) * Math.PI * 2 + 0.3;
      LC[e.i] = { x: -30 + Math.cos(a) * 650, y: 10 + Math.sin(a) * 340, z: 0 };
    }
  });
  // 将四团交汇焦点 newdate 锚定在中心附近，并对所有团内节点做斥力松弛，保证节点与标签不堆叠
  const ndIdx = byKey.newdate;
  LC[ndIdx] = { x: -45, y: 8, z: 0 };
  for (let it = 0; it < 48; it++) {
    for (let i = 0; i < events.length; i++) {
      for (let j = i + 1; j < events.length; j++) {
        const dx = LC[j].x - LC[i].x, dy = LC[j].y - LC[i].y;
        const rx = 112, ry = 50;
        const nx = dx / rx, ny = dy / ry;
        const d2 = nx * nx + ny * ny;
        if (d2 < 1 && d2 > 1e-6) {
          const d = Math.sqrt(d2);
          const push = (1 - d) * 0.42;
          const px = (nx / d) * rx * push, py = (ny / d) * ry * push;
          if (i !== ndIdx) { LC[i].x -= px; LC[i].y -= py; }
          if (j !== ndIdx) { LC[j].x += px; LC[j].y += py; }
        }
      }
    }
    LC[ndIdx].x = -45;
    LC[ndIdx].y = 8;
  }
  // 3) 三维关联网络：按区域聚成星团，适度拉开空间分布
  const GC = { rel: [-85, -25, 35], res: [340, -105, -170], life: [-360, 95, -130], plug: [210, 135, 250] };
  const LG = events.map((e) => {
    const c = GC[e.grp];
    return { x: c[0] + M.gauss(R) * 145, y: c[1] + M.gauss(R) * 98, z: c[2] + M.gauss(R) * 135 };
  });
  for (let it = 0; it < 28; it++) {
    for (let i = 0; i < events.length; i++) {
      for (let j = i + 1; j < events.length; j++) {
        const dx = LG[j].x - LG[i].x, dy = LG[j].y - LG[i].y, dz = LG[j].z - LG[i].z;
        const d = Math.hypot(dx, dy * 1.4, dz * 0.7);
        if (d < 92 && d > 1e-4) {
          const push = (92 - d) * 0.3;
          LG[i].x -= (dx / d) * push; LG[i].y -= (dy / d) * push;
          LG[j].x += (dx / d) * push; LG[j].y += (dy / d) * push;
        }
      }
    }
  }
  // 让几条关键的跨区域联系足够醒目
  LG[byKey.incident] = { x: -210, y: -65, z: 55 };
  LG[byKey.otherinc] = { x: 270, y: 95, z: 220 };
  LG[byKey.skipver] = { x: -165, y: 65, z: 30 };
  LG[byKey.tests] = { x: -335, y: 155, z: -80 };
  M.LAYOUT = { stream: LS, cluster: LC, galaxy: LG };

  // ---------- 情境化激活（a^{k+1} = ρs + (1-ρ)Pᵀa^k） ----------
  const N = events.length;
  function activation(context, seedKeys, iters = 14, rho = 0.32) {
    // 情境调节联系的有效使用程度 F(L, c, ...)，不改写长期 L
    const W = Array.from({ length: N }, () => new Float64Array(N));
    const boost = (e) => (context === 'release' ? (e.grp === 'rel' ? 1.6 : e.grp === 'res' ? 0.35 : 0.8) : (e.grp === 'res' ? 1.7 : e.grp === 'rel' ? 0.45 : 0.7));
    for (const l of links) {
      const ea = events[l.a], eb = events[l.b];
      W[l.a][l.b] += l.w * boost(eb);
      if (!TYPES[l.type].dir) W[l.b][l.a] += l.w * boost(ea);
      else W[l.b][l.a] += l.w * 0.25 * boost(ea);
    }
    for (let i = 0; i < N; i++) {
      let s = 0;
      for (let j = 0; j < N; j++) s += W[i][j];
      if (s === 0) { W[i][i] = 1; s = 1; }
      for (let j = 0; j < N; j++) W[i][j] /= s;
    }
    const s = new Float64Array(N);
    for (const k of seedKeys) s[byKey[k]] = 1 / seedKeys.length;
    const steps = [Float64Array.from(s)];
    let a = Float64Array.from(s);
    for (let it = 0; it < iters; it++) {
      const nx = new Float64Array(N);
      for (let i = 0; i < N; i++) nx[i] = rho * s[i];
      for (let i = 0; i < N; i++) if (a[i]) for (let j = 0; j < N; j++) nx[j] += (1 - rho) * W[i][j] * a[i];
      a = nx;
      steps.push(Float64Array.from(a));
    }
    // 显示用归一化（以最终状态的最大值为 1）
    const mx = Math.max(...steps[steps.length - 1]);
    return steps.map((v) => Array.from(v, (x) => Math.min(1.25, x / mx)));
  }
  M.ACT = {
    release: activation('release', ['depfail', 'depcheck']),
    research: activation('research', ['deriv', 'depfail']),
  };
  M.WORKSET = {
    release: ['depcheck', 'cache', 'incident', 'commit'],
    research: ['deriv', 'evid', 'lineage', 'swap'],
  };
})();
