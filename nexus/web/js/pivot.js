/* ==========================================================================
   灵 · 枢 — 浑仪星枢与双境动态场域引擎
   「灵玉」(jade)：宽域鎏金晶环（棱角金晶沿中心多道倾斜星环轨道运转）+ 多边形灵玉阵图
   「星枢」(cool)：漫天寒星闪烁（深空星轨圆点微芒）+ 天体星图底纹 + 棱镜折光
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, state } = N;

  /* ---------------------------------------------------------------- 渐进全字库加载
     本地子集已覆盖全部界面用字与高频汉字（离线零阻塞）；联网时后台静默补齐生僻字切片。 */
  function loadProgressiveFonts() {
    if (document.getElementById('nexus-gfonts')) return;
    const link = document.createElement('link');
    link.id = 'nexus-gfonts';
    link.rel = 'stylesheet';
    link.media = 'print';
    link.href = 'https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400..700&family=Noto+Sans+SC:wght@400..700&display=swap';
    link.onload = () => { link.media = 'all'; };
    document.head.appendChild(link);
  }
  setTimeout(loadProgressiveFonts, 160);

  let paintingTheme = null, previousBackground = null;
  const isCool = () => (paintingTheme || document.documentElement.dataset).palette === 'cool';
  const isNight = () => (paintingTheme || document.documentElement.dataset).theme === 'night';

  function themeRGB() {
    const cool = isCool(), night = isNight();
    if (cool) {
      return night ? {
        p: [134, 174, 255],
        hi: [216, 232, 255],
        a: [196, 168, 255],
        line: [158, 188, 255, 0.085],
      } : {
        p: [42, 92, 234],
        hi: [30, 68, 196],
        a: [126, 76, 240],
        line: [32, 62, 145, 0.098],
      };
    }
    // 灵玉：温玉多边形阵图 + 璀璨切面鎏金晶体调色盘
    return night ? {
      p: [44, 220, 180],
      hi: [255, 246, 198],
      a: [248, 192, 62],
      line: [110, 228, 196, 0.18],
      goldGlow: [255, 198, 58],
      goldLit: [255, 238, 158],
      goldMid: [244, 182, 44],
      goldShade: [188, 114, 14],
      goldEdge: [255, 248, 210],
    } : {
      p: [6, 138, 108],
      hi: [255, 248, 214],
      a: [216, 144, 16],
      line: [12, 68, 54, 0.096],
      goldGlow: [236, 162, 18],
      goldLit: [255, 226, 112],
      goldMid: [185, 121, 24],
      goldShade: [113, 70, 13],
      goldEdge: [130, 82, 17],
    };
  }

  /* ================================================================ 1. 全场域「枢场」双生动态画布 */
  const canvas = $('#pivot-field');
  let ctx = canvas ? canvas.getContext('2d') : null;
  let cw = 0, ch = 0, dpr = 1;
  let fieldRAF = 0;
  let lastFieldTime = 0;
  let lusterAngle = 0;
  const waves = [];
  let starMotes = [];
  let goldCrystals = [];
  const bgPointer = { x: -9999, y: -9999, active: false, down: false, holdTime: 0, lastTrail: 0 };

  const JADE_RING_ROT = -0.20; // ~ -16° 主星环倾角，与中枢浑仪主轴呼应
  let hubX = 0, hubY = 0, hubOnHero = false;
  let savedHubOffsetFromMid = -157;

  const pv = {
    holding: false,
    engaged: false,
    holdTimer: 0,
    pointerId: -1,
    originX: 0,
    originY: 0,
    targetX: 0,
    targetY: 0,
    curX: 0,
    curY: 0,
    grasp: 0,
    keyBoost: 0,
    lastT: 0,
  };

  // 页面与场域共用屏幕中轴，导航不改变构图圆心。
  function contentCenterX() {
    return cw * 0.5;
  }

  function layoutCenterY(el) {
    let y = el.offsetHeight * 0.5;
    let cur = el;
    while (cur && cur !== document.body && cur !== document.documentElement) {
      y += cur.offsetTop - (cur.scrollTop || 0);
      cur = cur.offsetParent;
    }
    return y;
  }

  function updateHubCenter() {
    hubX = contentCenterX();
    const anchor = $('#pivot-grip');
    const viewChat = $('#view-chat');
    if (anchor && viewChat && !viewChat.hidden && !state.items.length && anchor.offsetWidth > 0 && anchor.offsetHeight > 0) {
      hubY = layoutCenterY(anchor);
      if (ch > 0) savedHubOffsetFromMid = hubY - ch * 0.5;
      hubOnHero = state.view === 'chat';
    } else {
      hubY = Math.max(130, ch * 0.5 + savedHubOffsetFromMid);
      hubOnHero = false;
    }
    const core = $('#pivot-core');
    if (core) { core.style.left = hubX + 'px'; core.style.top = hubY + 'px'; }
  }

  function initParticles() {
    // 星枢：24 颗沿倾斜星轨运转的圆点星辰 + 16 颗散落天幕闪烁的远星
    const starCount = Math.round(Math.max(60, cw * ch / 10000));
    for (let i = starMotes.length; i < starCount; i++) {
      const orbital = i < Math.round(starCount * .35);
      starMotes.push({
        orbital,
        rRatio: orbital ? (0.14 + (i / Math.max(1, Math.round(starCount * .35))) * 0.44 + (Math.random() - 0.5) * 0.025) : 0,
        nx: Math.random(),
        ny: Math.random(),
        angle: Math.random() * Math.PI * 2,
        speed: orbital ? (0.028 + Math.random() * 0.048) * (i % 2 ? 1 : -0.82) : (Math.random() - 0.5) * 0.006,
        tilt: 0.5 + Math.random() * 0.36,
        rot: (Math.random() - 0.5) * 0.78,
        size: orbital ? (0.8 + Math.random() * 1.5) : (0.35 + Math.random() * 1.1),
        alpha: 0.32 + Math.random() * 0.44,
        twinkleFreq: .35 + Math.random() * 1.1,
        twinklePhase: Math.random() * Math.PI * 2,
        glint: i % 6 === 0,
        accent: i % 4 === 0,
        ox: 0, oy: 0, moveBoost: 0,
      });
    }
    starMotes.length = starCount;

    // 主环有序，伴随晶粒向外舒展；远处少量晶粒平衡全屏空间。
    const span = Math.min(cw, ch) || 900;
    const BANDS = [
      { count: Math.round(span * .10), rMin: .372, rMax: .422, tilt: .30, speed: .042 },
      { count: Math.round(span * .055), rMin: .30, rMax: .51, tilt: .33, speed: .034 },
      { count: Math.round(cw * ch / 26000), rMin: .50, rMax: .90, tilt: .65, speed: .012 },
    ];
    let idx = 0;
    for (let band = 0; band < BANDS.length; band++) {
      const b = BANDS[band];
      for (let j = 0; j < b.count; j++, idx++) {
        if (goldCrystals[idx]) continue;
        const u = (j + 0.5) / b.count;
        const rBase = b.rMin + ((j * 0.618034) % 1) * (b.rMax - b.rMin);
        const tier = idx % 7 === 0 ? 1.22 : (idx % 2 === 0 ? 0.96 : 0.75);

        goldCrystals.push({
          distant: band === 2,
          nx: Math.random(), ny: Math.random(),
          habit: idx % 5 === 0 ? 2 : (idx % 2 === 0 ? 0 : 1), // 0: 八面双锥菱晶, 1: 六棱晶柱, 2: 斜切晶簇片
          rRatio: rBase,
          tilt: b.tilt,
          rotOffset: 0,
          // 每一环带均独立均匀铺满完整 360° 圆周，避免单侧断层
          angle: u * Math.PI * 2 + (Math.random() - 0.5) * (Math.PI * 1.4 / b.count),
          bandDz: (Math.random() - 0.5) * 2,
          // 开普勒式差速同向公转：内环略快，外环从容，整条星环气脉合一
          speed: b.speed,
          bobAmp: .35 + Math.random() * .5,
          bobFreq: 0.52 + Math.random() * 0.48,
          phase: Math.random() * Math.PI * 2,
          spin: Math.random() * Math.PI * 2,
          spinSpeed: .035 * (idx % 2 ? 1 : -1),
          size: (band === 2 ? .7 + Math.random() * .6 : 1.25 + Math.random() * .6) * tier,
          alpha: (band === 2 ? .30 : .55) + Math.random() * .25,
          ox: 0, oy: 0, moveBoost: 0,
        });
      }
    }
    goldCrystals.length = idx;
  }

  function resizeField() {
    if (!canvas || !ctx) return;
    const nextDpr = Math.min(window.devicePixelRatio || 1, 2);
    if (cw === window.innerWidth && ch === window.innerHeight && dpr === nextDpr) return;
    dpr = nextDpr;
    cw = window.innerWidth;
    ch = window.innerHeight;
    canvas.width = Math.round(cw * dpr);
    canvas.height = Math.round(ch * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    initParticles();
    updateHubCenter();
    drawStaticOrWake();
  }

  function spawnWave(x, y, opts = {}) {
    if (!ctx || N.REDUCED) return;
    waves.push({
      x, y,
      r: opts.r0 || 6,
      maxR: opts.maxR || (isCool() ? 195 : 175),
      dur: opts.dur || (isCool() ? 1150 : 1450),
      born: performance.now(),
      accent: !!opts.accent,
      strength: opts.strength || 1,
      crystalWave: !isCool(),
      colors: themeRGB(),
    });
    ensureFieldLoop();
  }

  function drawPolygonRing(cx, cy, r, sides, rot) {
    ctx.beginPath();
    for (let i = 0; i <= sides; i++) {
      const a = rot + (i / sides) * Math.PI * 2;
      const x = cx + Math.cos(a) * r;
      const y = cy + Math.sin(a) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
  }

  function drawCenterWatermark(cx, cy, rgb, cool, now) {
    const [lr, lg, lb, la] = rgb.line;
    const baseR = Math.min(cw, ch) * 0.36;
    ctx.save();
    ctx.translate(cx, cy);

    if (!cool) {
      // 灵玉专属：恢弘多边形灵玉阵图（外圆 + 十二棱晶切环 + 双叠八角玉琮几何 + 金玉流光弧 + 倾斜星环轨）
      const night = isNight();
      if (night) { ctx.shadowColor = 'rgba(85, 224, 182, .32)'; ctx.shadowBlur = 5; }
      const [gr, gg, gb] = rgb.goldMid;
      const [pr, pg, pb] = rgb.p;

      // 1. 外层周天主环
      ctx.strokeStyle = `rgba(${lr},${lg},${lb},${la})`;
      ctx.lineWidth = 1.05;
      ctx.beginPath();
      ctx.arc(0, 0, baseR, 0, Math.PI * 2);
      ctx.stroke();

      // 2. 十二棱晶切大环（缓慢旋移）
      const rot12 = lusterAngle * 0.12;
      ctx.strokeStyle = `rgba(${lr},${lg},${lb},${la * 0.92})`;
      ctx.lineWidth = 1;
      drawPolygonRing(0, 0, baseR * 0.84, 12, rot12);
      ctx.stroke();

      // 十二棱顶点至外环的精密连梁刻度
      ctx.strokeStyle = `rgba(${lr},${lg},${lb},${la * 1.1})`;
      ctx.lineWidth = 0.9;
      for (let i = 0; i < 12; i++) {
        const a = rot12 + (i / 12) * Math.PI * 2;
        const rIn = baseR * 0.84;
        const rOut = baseR + (i % 3 === 0 ? 5 : 2);
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * rIn, Math.sin(a) * rIn);
        ctx.lineTo(Math.cos(a) * rOut, Math.sin(a) * rOut);
        ctx.stroke();
      }

      // 3. 中层双叠八角琢玉几何线（八方玉琮叠纹）
      const rot8 = -lusterAngle * 0.08;
      ctx.strokeStyle = `rgba(${lr},${lg},${lb},${la * 0.78})`;
      ctx.lineWidth = 0.95;
      drawPolygonRing(0, 0, baseR * 0.63, 8, rot8);
      ctx.stroke();

      ctx.strokeStyle = `rgba(${gr},${gg},${gb},${night ? .19 : .09})`;
      ctx.lineWidth = 0.85;
      drawPolygonRing(0, 0, baseR * 0.63, 8, rot8 + Math.PI / 8);
      ctx.stroke();

      // 4. 金玉双色流光弧沿外周天缓行
      if (!N.REDUCED) {
        ctx.save();
        ctx.strokeStyle = `rgba(${gr},${gg},${gb},${night ? .5 : .24})`;
        ctx.lineWidth = 1.85;
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.arc(0, 0, baseR, lusterAngle, lusterAngle + 0.84);
        ctx.stroke();

        ctx.strokeStyle = `rgba(${pr},${pg},${pb},${night ? .36 : .17})`;
        ctx.lineWidth = 1.45;
        ctx.beginPath();
        ctx.arc(0, 0, baseR, lusterAngle + Math.PI, lusterAngle + Math.PI + 0.76);
        ctx.stroke();
        ctx.restore();
      }

      ctx.restore();
      return;
    }

    // 星枢：开阔天体星图底纹（严格对齐中枢同心）
    ctx.strokeStyle = `rgba(${lr},${lg},${lb},${la})`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(0, 0, baseR, 0, Math.PI * 2);
    ctx.stroke();

    ctx.beginPath();
    ctx.setLineDash([3, 9]);
    ctx.arc(0, 0, baseR * 0.72, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.strokeStyle = `rgba(${lr},${lg},${lb},${la * 1.15})`;
    ctx.lineWidth = 1;
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2;
      const r0 = baseR - (i % 3 === 0 ? 10 : 4.5);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
      ctx.lineTo(Math.cos(a) * (baseR + (i % 3 === 0 ? 5 : 2)), Math.sin(a) * (baseR + (i % 3 === 0 ? 5 : 2)));
      ctx.stroke();
    }

    ctx.rotate(-0.34);
    ctx.beginPath();
    ctx.ellipse(0, 0, baseR * 0.92, baseR * 0.34, 0, 0, Math.PI * 2);
    ctx.strokeStyle = `rgba(${lr},${lg},${lb},${la * 0.75})`;
    ctx.stroke();
    ctx.restore();
  }

  /* 指针场物理：
     - 长按左键时：纯径向平缓向光标聚拢悬停（零切向涡流力，被吸引粒子自动放缓公转与自转，绝不绕光标打转！）
     - 普通掠过时：轻柔向两侧推开微让，松弛自然归位。 */
  function applyPointerField(m, baseX, baseY, dt) {
    let targetOx = 0, targetOy = 0, holdDampen = 1;
    if (bgPointer.active) {
      const dx = bgPointer.x - baseX;
      const dy = bgPointer.y - baseY;
      const dist = Math.hypot(dx, dy);
      if (bgPointer.down && dist < 265 && dist > 2) {
        const w = Math.pow(1 - dist / 265, 1.45);
        const pull = Math.min(dist * 0.46, 58 * w);
        targetOx = (dx / dist) * pull;
        targetOy = (dy / dist) * pull;
        // 被吸引聚拢时平缓悬停，消除绕圈与乱转观感
        holdDampen = 1 - 0.85 * w;
      } else if (!bgPointer.down && dist < 125 && dist > 2) {
        const w = Math.pow(1 - dist / 125, 1.6);
        const push = 13 * w;
        targetOx = -(dx / dist) * push;
        targetOy = -(dy / dist) * push;
      }
    }
    const rate = Math.min(1, dt * (bgPointer.down ? 5.6 : 4.6));
    const prevOx = m.ox, prevOy = m.oy;
    m.ox += (targetOx - m.ox) * rate;
    m.oy += (targetOy - m.oy) * rate;
    const stepSpeed = Math.hypot(m.ox - prevOx, m.oy - prevOy) / Math.max(0.008, dt);
    m.moveBoost = Math.min(0.28, stepSpeed * 0.0022 + (1 - holdDampen) * 0.16);
    return holdDampen;
  }

  function renderStarField(now, dt, pivotCenterX, pivotCenterY, rgb) {
    const span = Math.min(cw, ch);
    const night = isNight();
    const tSec = now * 0.001;
    for (const m of starMotes) {
      // 先用当前位置估算指针驻留阻尼，令长按吸附的星点安静悬停不绕转
      let estX, estY;
      if (m.orbital) {
        const rx = span * m.rRatio;
        const ry = rx * m.tilt;
        const lx = Math.cos(m.angle) * rx;
        const ly = Math.sin(m.angle) * ry;
        const cr = Math.cos(m.rot), sr = Math.sin(m.rot);
        estX = pivotCenterX + lx * cr - ly * sr;
        estY = pivotCenterY + lx * sr + ly * cr;
      } else {
        estX = m.nx * cw;
        estY = m.ny * ch;
      }
      const holdDampen = applyPointerField(m, estX, estY, dt);

      let baseX, baseY;
      if (m.orbital) {
        m.angle += m.speed * holdDampen * dt * (state.replying ? 1.8 : 1);
        const rx = span * m.rRatio;
        const ry = rx * m.tilt;
        const lx = Math.cos(m.angle) * rx;
        const ly = Math.sin(m.angle) * ry;
        const cr = Math.cos(m.rot), sr = Math.sin(m.rot);
        baseX = pivotCenterX + lx * cr - ly * sr;
        baseY = pivotCenterY + lx * sr + ly * cr;
      } else {
        m.ny = (m.ny + m.speed * holdDampen * dt + 1) % 1;
        baseX = m.nx * cw;
        baseY = m.ny * ch;
      }

      const px = baseX + m.ox;
      const py = baseY + m.oy;

      const twinkle = 0.62 + 0.38 * Math.sin(tSec * m.twinkleFreq + m.twinklePhase);
      const boost = m.moveBoost || 0;
      const alpha = Math.min(0.92, m.alpha * twinkle + boost);
      const col = m.accent ? rgb.a : (night && m.glint ? rgb.hi : rgb.p);
      const r = m.size + boost * 1.2;

      const haloR = r * 3.4;
      const g = ctx.createRadialGradient(px, py, 0, px, py, haloR);
      g.addColorStop(0, `rgba(${col[0]},${col[1]},${col[2]},${alpha * (night ? 0.34 : 0.24)})`);
      g.addColorStop(1, `rgba(${col[0]},${col[1]},${col[2]},0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(px, py, haloR, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = `rgba(${col[0]},${col[1]},${col[2]},${alpha})`;
      ctx.beginPath();
      ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fill();

      if (m.glint && twinkle > 0.78) {
        const arm = r * 3.1;
        ctx.strokeStyle = `rgba(${col[0]},${col[1]},${col[2]},${alpha * 0.55})`;
        ctx.lineWidth = 0.75;
        ctx.beginPath();
        ctx.moveTo(px - arm, py); ctx.lineTo(px + arm, py);
        ctx.moveTo(px, py - arm); ctx.lineTo(px, py + arm);
        ctx.stroke();
      }
    }
  }

  /* 灵玉专属：细巧、棱角分明的切面发光鎏金晶体 */
  function drawFacetedGoldCrystal(px, py, s, spin, habit, alpha, flash, night, rgb) {
    const glowCol = rgb.goldGlow;
    const lit = rgb.goldLit;
    const mid = rgb.goldMid;
    const shade = rgb.goldShade;
    const edge = rgb.goldEdge;

    // 1. 贴身暖金辉晕
    const haloR = s * (night ? 2.8 + flash * .65 : 1.8 + flash * .45);
    const g = ctx.createRadialGradient(px, py, s * 0.1, px, py, haloR);
    g.addColorStop(0, `rgba(${glowCol[0]},${glowCol[1]},${glowCol[2]},${alpha * (night ? .5 : .12) * (1 + flash * 0.42)})`);
    g.addColorStop(0.5, `rgba(${glowCol[0]},${glowCol[1]},${glowCol[2]},${alpha * (night ? .22 : .1)})`);
    g.addColorStop(1, `rgba(${glowCol[0]},${glowCol[1]},${glowCol[2]},0)`);
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(px, py, haloR, 0, Math.PI * 2);
    ctx.fill();

    // 2. 棱角分明的微缩多面晶身（硬朗 miter 折角 + 三阶明暗切面）
    ctx.save();
    ctx.translate(px, py);
    ctx.rotate(spin);
    ctx.lineJoin = 'miter';
    ctx.miterLimit = 6;

    const litStr = flash > 0.35
      ? `rgba(${rgb.hi[0]},${rgb.hi[1]},${rgb.hi[2]},${Math.min(0.98, alpha + 0.16)})`
      : `rgba(${lit[0]},${lit[1]},${lit[2]},${Math.min(0.95, alpha + 0.08)})`;
    const midStr = `rgba(${mid[0]},${mid[1]},${mid[2]},${alpha * 0.94})`;
    const shadeStr = `rgba(${shade[0]},${shade[1]},${shade[2]},${alpha * (night ? 0.88 : 0.92)})`;
    const outerEdgeStr = `rgba(${edge[0]},${edge[1]},${edge[2]},${alpha * (night ? 0.72 : 0.66)})`;
    const ridgeStr = `rgba(${rgb.hi[0]},${rgb.hi[1]},${rgb.hi[2]},${alpha * (0.62 + flash * 0.32)})`;

    if (habit === 0) {
      // 八面双锥菱晶
      const L = s * 1.76, W = s * 0.88, waist = L * 0.08;
      ctx.fillStyle = litStr;
      ctx.beginPath();
      ctx.moveTo(0, -L); ctx.lineTo(-W, 0); ctx.lineTo(0, waist); ctx.closePath();
      ctx.fill();

      ctx.fillStyle = midStr;
      ctx.beginPath();
      ctx.moveTo(-W, 0); ctx.lineTo(0, L); ctx.lineTo(0, waist); ctx.closePath();
      ctx.fill();

      ctx.fillStyle = shadeStr;
      ctx.beginPath();
      ctx.moveTo(0, -L); ctx.lineTo(W, 0); ctx.lineTo(0, L); ctx.lineTo(0, waist); ctx.closePath();
      ctx.fill();

      ctx.strokeStyle = outerEdgeStr;
      ctx.lineWidth = 0.68;
      ctx.beginPath();
      ctx.moveTo(0, -L); ctx.lineTo(W, 0); ctx.lineTo(0, L); ctx.lineTo(-W, 0); ctx.closePath();
      ctx.stroke();

      ctx.strokeStyle = ridgeStr;
      ctx.lineWidth = 0.55;
      ctx.beginPath();
      ctx.moveTo(0, -L); ctx.lineTo(0, L);
      ctx.stroke();
    } else if (habit === 1) {
      // 六棱晶柱
      const L = s * 1.68, W = s * 0.75, shoulder = L * 0.45;
      ctx.fillStyle = litStr;
      ctx.beginPath();
      ctx.moveTo(0, -L); ctx.lineTo(-W, -shoulder); ctx.lineTo(-W, shoulder); ctx.lineTo(0, L); ctx.closePath();
      ctx.fill();

      ctx.fillStyle = shadeStr;
      ctx.beginPath();
      ctx.moveTo(0, -L); ctx.lineTo(W, -shoulder); ctx.lineTo(W, shoulder); ctx.lineTo(0, L); ctx.closePath();
      ctx.fill();

      ctx.fillStyle = `rgba(${rgb.hi[0]},${rgb.hi[1]},${rgb.hi[2]},${alpha * (0.72 + flash * 0.24)})`;
      ctx.beginPath();
      ctx.moveTo(0, -L); ctx.lineTo(W, -shoulder); ctx.lineTo(0, -shoulder * 0.32); ctx.lineTo(-W, -shoulder); ctx.closePath();
      ctx.fill();

      ctx.strokeStyle = outerEdgeStr;
      ctx.lineWidth = 0.68;
      ctx.beginPath();
      ctx.moveTo(0, -L); ctx.lineTo(W, -shoulder); ctx.lineTo(W, shoulder);
      ctx.lineTo(0, L); ctx.lineTo(-W, shoulder); ctx.lineTo(-W, -shoulder); ctx.closePath();
      ctx.stroke();

      ctx.strokeStyle = ridgeStr;
      ctx.lineWidth = 0.52;
      ctx.beginPath();
      ctx.moveTo(0, -shoulder * 0.32); ctx.lineTo(0, L);
      ctx.stroke();
    } else {
      // 斜切菱晶
      const L = s * 1.6, W = s * 0.85;
      ctx.fillStyle = litStr;
      ctx.beginPath();
      ctx.moveTo(0, -L * 1.05); ctx.lineTo(-W * 0.9, L * 0.16); ctx.lineTo(0, L * 0.95); ctx.closePath();
      ctx.fill();

      ctx.fillStyle = shadeStr;
      ctx.beginPath();
      ctx.moveTo(0, -L * 1.05); ctx.lineTo(W * 0.95, -L * 0.16); ctx.lineTo(0, L * 0.95); ctx.closePath();
      ctx.fill();

      ctx.strokeStyle = outerEdgeStr;
      ctx.lineWidth = 0.68;
      ctx.beginPath();
      ctx.moveTo(0, -L * 1.05); ctx.lineTo(W * 0.95, -L * 0.16);
      ctx.lineTo(0, L * 0.95); ctx.lineTo(-W * 0.9, L * 0.16); ctx.closePath();
      ctx.stroke();
    }

    // 3. 切面迎光折光闪芒
    if (flash > 0.6) {
      const glintLen = s * (1.3 + flash * 1.05);
      ctx.strokeStyle = `rgba(${rgb.hi[0]},${rgb.hi[1]},${rgb.hi[2]},${(flash - 0.56) * 1.55 * alpha})`;
      ctx.lineWidth = 0.68;
      ctx.beginPath();
      ctx.moveTo(-glintLen, -s * 0.45); ctx.lineTo(glintLen, -s * 0.45);
      ctx.moveTo(0, -s * 0.45 - glintLen * 0.78); ctx.lineTo(0, -s * 0.45 + glintLen * 0.78);
      ctx.stroke();
    }

    ctx.restore();
  }

  /* 灵玉专属：宽阔三维鎏金晶环带（收束运动轨迹成环，铺展空间层次） */
  function renderGoldCrystalRing(now, dt, centerX, centerY, rgb) {
    const span = Math.min(cw, ch);
    const night = isNight();
    const tSec = now * 0.001;
    const perspective = Math.min(1, 750 / span);
    const ringRotation = JADE_RING_ROT * perspective;
    const speedBoost = 1 + pv.grasp * 1.5 + pv.keyBoost * 1.2 + (state.replying ? 0.8 : 0);

    ctx.save();
    ctx.translate(centerX, centerY);
    ctx.rotate(ringRotation);
    for (const [radius, width, alpha] of [[.399, 9, night ? .06 : .035], [.399, .8, night ? .28 : .15]]) {
      ctx.beginPath();
      ctx.ellipse(0, 0, span * radius, span * radius * .30 * perspective, 0, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(${rgb.goldMid.join(',')},${alpha})`;
      ctx.lineWidth = width;
      ctx.stroke();
    }
    ctx.restore();
    for (const m of goldCrystals) {
      const rot = ringRotation + m.rotOffset + pv.curX * 0.0035;
      const tilt = Math.max(.08, Math.min(.62, m.tilt * perspective + pv.curY * .003));
      const cr = Math.cos(rot), sr = Math.sin(rot);
      const rx = span * m.rRatio;
      const ry = rx * tilt;

      // 先以当前角度位置计算指针场吸附与悬停阻尼
      const bob0 = Math.sin(tSec * m.bobFreq + m.phase) * m.bobAmp;
      const lx0 = Math.cos(m.angle) * rx;
      const ly0 = Math.sin(m.angle) * ry + m.bandDz + bob0;
      const estX = m.distant ? m.nx * cw : centerX + lx0 * cr - ly0 * sr;
      const estY = m.distant ? m.ny * ch : centerY + lx0 * sr + ly0 * cr;
      const holdDampen = applyPointerField(m, estX, estY, dt);

      m.angle = (m.angle + m.speed * speedBoost * holdDampen * dt) % (Math.PI * 2);
      m.spin += m.spinSpeed * holdDampen * dt;

      const bob = Math.sin(tSec * m.bobFreq + m.phase) * m.bobAmp;
      const lx = Math.cos(m.angle) * rx;
      const ly = Math.sin(m.angle) * ry + m.bandDz + bob;
      const relX = lx * cr - ly * sr;
      const relY = lx * sr + ly * cr;
      const px = (m.distant ? m.nx * cw : centerX + relX) + m.ox;
      const py = (m.distant ? m.ny * ch : centerY + relY) + m.oy;

      // 前后半环景深缩放与中枢核心后方自然遮挡
      const depth = Math.sin(m.angle);
      const persp = 0.84 + 0.24 * ((depth + 1) * 0.5);
      const distFromCore = Math.hypot(relX, relY);
      const behindCore = (hubOnHero && depth < 0 && distFromCore < 46)
        ? Math.max(0.18, (distFromCore - 20) / 26)
        : 1;

      const boost = m.moveBoost || 0;
      const flash = Math.pow(Math.max(0, Math.cos(m.spin * 2 + m.phase)), 10);
      const alpha = Math.min(0.95,
        (m.alpha * (0.84 + 0.16 * Math.sin(tSec * 1.05 + m.phase)) + boost + flash * 0.16)
        * (0.76 + 0.24 * ((depth + 1) * 0.5))
        * behindCore
      );
      const s = (m.size + boost * 0.4) * persp * (night ? 1 : 1.1);

      drawFacetedGoldCrystal(px, py, s, m.spin, m.habit, alpha, flash, night, rgb);
    }
  }

  function renderFieldFrame(now) {
    if (!ctx) return;
    const dt = Math.min(0.05, lastFieldTime ? (now - lastFieldTime) / 1000 : 0.016);
    lastFieldTime = now;

    ctx.clearRect(0, 0, cw, ch);
    const rgb = themeRGB();
    const cool = isCool();

    // 灵玉与星枢统一对齐中枢圆心（首屏对齐 #orb-stage，子页面对齐 .view 视觉中轴）
    const centerX = hubX + pv.curX * 0.45;
    const centerY = hubY + pv.curY * 0.45;

    if (!N.REDUCED) {
      lusterAngle = (lusterAngle + dt * 0.25 * (state.replying ? 1.7 : 1)) % (Math.PI * 2);
    }

    drawCenterWatermark(centerX, centerY, rgb, cool, now);

    // 长按背景蓄能光晕（沉稳呼吸，不自转）
    if (bgPointer.down && !N.REDUCED) {
      bgPointer.holdTime += dt;
      const charge = Math.min(1, bgPointer.holdTime / 1.1);
      const col = cool ? (charge > 0.55 ? rgb.a : rgb.p) : rgb.goldMid;
      const pulseR = 18 + charge * 40 + Math.sin(now * 0.006) * 2.5;
      const g = ctx.createRadialGradient(bgPointer.x, bgPointer.y, 0, bgPointer.x, bgPointer.y, pulseR * 1.5);
      g.addColorStop(0, `rgba(${col[0]},${col[1]},${col[2]},${0.18 * (0.45 + charge * 0.55)})`);
      g.addColorStop(0.6, `rgba(${col[0]},${col[1]},${col[2]},${0.05 * charge})`);
      g.addColorStop(1, `rgba(${col[0]},${col[1]},${col[2]},0)`);
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(bgPointer.x, bgPointer.y, pulseR * 1.5, 0, Math.PI * 2);
      ctx.fill();

      ctx.strokeStyle = `rgba(${col[0]},${col[1]},${col[2]},${0.26 + charge * 0.24})`;
      ctx.lineWidth = cool ? 1.15 : 1.15;
      if (cool) {
        ctx.beginPath();
        ctx.arc(bgPointer.x, bgPointer.y, pulseR * 0.68, 0, Math.PI * 2);
        ctx.stroke();
      } else {
        drawPolygonRing(bgPointer.x, bgPointer.y, pulseR * 0.68, 8, Math.PI / 8);
        ctx.stroke();
      }
    }

    // 双境常驻动态场：星枢漫天寒星 vs 灵玉宽域鎏金晶环
    if (!N.REDUCED) {
      if (cool) renderStarField(now, dt, centerX, centerY, rgb);
      else renderGoldCrystalRing(now, dt, centerX, centerY, rgb);
    }

    // 点击与拖拽波纹
    for (let i = waves.length - 1; i >= 0; i--) {
      const w = waves[i];
      const t = (now - w.born) / (w.dur * N.motionScale());
      if (t >= 1) { waves.splice(i, 1); continue; }
      const ease = w.crystalWave ? (1 - Math.pow(1 - t, 2.7)) : (1 - Math.pow(1 - t, 2.4));
      const r = w.r + (w.maxR - w.r) * ease;
      const fade = Math.pow(1 - t, 1.5) * w.strength;

      if (w.crystalWave) {
        const gCol = w.colors.goldMid;
        const jCol = w.colors.p;
        ctx.strokeStyle = `rgba(${gCol[0]},${gCol[1]},${gCol[2]},${0.28 * fade})`;
        ctx.lineWidth = 1.1;
        drawPolygonRing(w.x, w.y, r, 8, Math.PI / 8);
        ctx.stroke();

        if (r > 20) {
          ctx.strokeStyle = `rgba(${jCol[0]},${jCol[1]},${jCol[2]},${0.16 * fade})`;
          ctx.lineWidth = 0.9;
          ctx.beginPath();
          ctx.arc(w.x, w.y, r * 0.78, 0, Math.PI * 2);
          ctx.stroke();
        }
      } else {
        const col = w.accent ? w.colors.a : w.colors.p;
        ctx.strokeStyle = `rgba(${col[0]},${col[1]},${col[2]},${0.28 * fade})`;
        ctx.lineWidth = 1.25;
        ctx.beginPath();
        ctx.arc(w.x, w.y, r, 0, Math.PI * 2);
        ctx.stroke();
        if (r > 24) {
          const c2 = w.colors.a;
          ctx.strokeStyle = `rgba(${c2[0]},${c2[1]},${c2[2]},${0.16 * fade})`;
          ctx.lineWidth = 0.85;
          ctx.beginPath();
          ctx.arc(w.x, w.y, r * 0.76, 0, Math.PI * 2);
          ctx.stroke();
        }
      }
    }

    if (previousBackground) {
      const current = { ctx, starMotes, goldCrystals, lusterAngle };
      const old = previousBackground;
      ctx = old.ctx; starMotes = old.stars; goldCrystals = old.gold;
      paintingTheme = old.theme;
      lusterAngle = old.angle + dt * .25 * (state.replying ? 1.7 : 1);
      ctx.clearRect(0, 0, cw, ch);
      const colors = themeRGB();
      drawCenterWatermark(centerX, centerY, colors, isCool(), now);
      if (isCool()) renderStarField(now, dt, centerX, centerY, colors);
      else renderGoldCrystalRing(now, dt, centerX, centerY, colors);
      old.angle = lusterAngle;
      ({ ctx, starMotes, goldCrystals, lusterAngle } = current);
      paintingTheme = null;
    }

    if (!N.REDUCED && !document.hidden) {
      fieldRAF = requestAnimationFrame(renderFieldFrame);
    } else {
      fieldRAF = 0;
      lastFieldTime = 0;
    }
  }

  function ensureFieldLoop() {
    if (!fieldRAF && ctx && !document.hidden) {
      lastFieldTime = 0;
      fieldRAF = requestAnimationFrame(renderFieldFrame);
    }
  }

  function drawStaticOrWake() {
    if (!ctx) return;
    if (!N.REDUCED) ensureFieldLoop();
    else renderFieldFrame(performance.now());
  }

  /* 空白背景自然交互：避开一切按钮、输入框、消息与卡片，只在纯粹画布留白处响应 */
  const INTERACTIVE_GUARD = 'button, a, input, textarea, select, summary, details, dialog, label, .card, .work-item, .plug-item, .ctx-role, .ctx-sec, .theme-card, .req-card, .composer, .dock, .island, .menu, .usage-pop, .msg, .orb-stage, .orb-anchor, .chip, .tabs, .seg';

  document.addEventListener('pointerdown', e => {
    if (e.button !== 0 || N.REDUCED) return;
    if (e.target?.closest?.(INTERACTIVE_GUARD)) return;
    bgPointer.x = e.clientX;
    bgPointer.y = e.clientY;
    bgPointer.active = true;
    bgPointer.down = true;
    bgPointer.holdTime = 0;
    bgPointer.lastTrail = performance.now();
    spawnWave(e.clientX, e.clientY, { maxR: isCool() ? 135 : 125, strength: 0.85 });
    ensureFieldLoop();
  }, { passive: true });

  window.addEventListener('pointermove', e => {
    bgPointer.x = e.clientX;
    bgPointer.y = e.clientY;
    bgPointer.active = true;
    if (bgPointer.down && !N.REDUCED) {
      const now = performance.now();
      if (now - bgPointer.lastTrail > (isCool() ? 105 : 125)) {
        bgPointer.lastTrail = now;
        spawnWave(e.clientX, e.clientY, {
          r0: 4,
          maxR: isCool() ? 92 : 84,
          strength: 0.5,
          accent: bgPointer.holdTime > 0.6,
        });
      }
    }
    scheduleCardSpotlight(e.clientX, e.clientY, e.target);
  }, { passive: true });

  function endBgHold() {
    if (!bgPointer.down) return;
    const held = bgPointer.holdTime;
    bgPointer.down = false;
    if (held > 0.26 && !N.REDUCED) {
      spawnWave(bgPointer.x, bgPointer.y, {
        r0: 14,
        maxR: isCool() ? 245 : 210,
        dur: isCool() ? 1250 : 1450,
        accent: true,
        strength: Math.min(1.2, 0.78 + held * 0.45),
      });
    }
    bgPointer.holdTime = 0;
  }
  window.addEventListener('pointerup', endBgHold, { passive: true });
  window.addEventListener('pointercancel', endBgHold, { passive: true });
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && fieldRAF) { cancelAnimationFrame(fieldRAF); fieldRAF = 0; }
    else drawStaticOrWake();
  });
  window.addEventListener('resize', resizeField, { passive: true });

  /* ================================================================ 2. 双境卡片边框流光追光（灵玉金玉流光 / 星枢棱镜折射） */
  let spotRAF = 0, spotX = 0, spotY = 0, spotTarget = null, lastSpotEl = null;
  function scheduleCardSpotlight(x, y, target) {
    if (N.REDUCED) return;
    spotX = x; spotY = y; spotTarget = target;
    if (spotRAF) return;
    spotRAF = requestAnimationFrame(() => {
      spotRAF = 0;
      const surface = spotTarget?.closest?.('.card, .work-item, .work-quiet, .plug-item, .ctx-role, .ctx-sec, .theme-card, .req-card, .composer, .dock, .chip, .qrow, .history-undo, .usage-pop, .sheet, .island, .mcp-row, .hui-section, .file, .plug-page');
      if (lastSpotEl && lastSpotEl !== surface) lastSpotEl.style.removeProperty('--spot-on');
      if (!surface) { lastSpotEl = null; return; }
      const r = surface.getBoundingClientRect();
      surface.style.setProperty('--mx', (spotX - r.left).toFixed(1) + 'px');
      surface.style.setProperty('--my', (spotY - r.top).toFixed(1) + 'px');
      surface.style.setProperty('--spot-on', '1');
      lastSpotEl = surface;
    });
  }

  /* ================================================================ 3. 常驻背景中枢与首页长按交互 */
  let pivotRAF = 0;
  let pivotMountedStage = null;
  const ORBITS = [
    { rx: 80, ry: 27, baseRot: -24, speed: 0.38, phase: 0.6 },
    { rx: 66, ry: 22, baseRot: 32, speed: -0.3, phase: 2.3 },
    { rx: 50, ry: 17, baseRot: 72, speed: 0.48, phase: 4.1 },
  ];

  function buildTicks(ticksGroup) {
    if (!ticksGroup || ticksGroup.childNodes.length) return;
    const ns = 'http://www.w3.org/2000/svg';
    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * Math.PI * 2;
      const major = i % 6 === 0;
      const mid = i % 3 === 0;
      const r1 = major ? 85 : mid ? 87.5 : 89.2;
      const r2 = major ? 95.5 : 92.5;
      const line = document.createElementNS(ns, 'line');
      line.setAttribute('x1', (Math.cos(a) * r1).toFixed(2));
      line.setAttribute('y1', (Math.sin(a) * r1).toFixed(2));
      line.setAttribute('x2', (Math.cos(a) * r2).toFixed(2));
      line.setAttribute('y2', (Math.sin(a) * r2).toFixed(2));
      if (major) line.setAttribute('class', 'major');
      ticksGroup.appendChild(line);
    }
  }

  function activeSlotStates() {
    const active = (state.works || []).filter(w => ['running', 'waiting_user', 'queued', 'pending', 'pausing'].includes(w.state));
    return [
      'primary',
      active[0]?.state || 'idle',
      active[1]?.state || 'idle',
      active[2]?.state || 'idle',
    ];
  }

  function stopPivot() {
    cancelAnimationFrame(pivotRAF);
    pivotRAF = 0;
    pv.lastT = 0;
    clearTimeout(pv.holdTimer);
    pv.holding = false;
    pv.engaged = false;
    pv.curX = 0;
    pv.curY = 0;
    pv.grasp = 0;
    pivotMountedStage = null;
  }

  let settleTimer = 0;
  function scheduleHubSync() {
    updateHubCenter();
    requestAnimationFrame(updateHubCenter);
    clearTimeout(settleTimer);
    settleTimer = setTimeout(updateHubCenter, 360);
  }

  function mountPivot() {
    const stage = $('#orb-stage');
    const shouldRun = !!stage;
    if (!shouldRun) {
      if (pivotMountedStage) stopPivot();
      scheduleHubSync();
      return;
    }
    if (pivotMountedStage === stage) {
      scheduleHubSync();
      return;
    }
    stopPivot();
    pivotMountedStage = stage;
    scheduleHubSync();

    const ticksGroup = $('#pv-ticks', stage);
    const backNodes = $('#pv-nodes-back', stage);
    const frontNodes = $('#pv-nodes-front', stage);
    const meridian = $('#pv-meridian', stage);
    const coreGroup = $('#pv-core', stage);
    const backPlanes = $$('.pv-back .pv-plane', stage);
    const frontPlanes = $$('.pv-front .pv-plane', stage);
    if (!backNodes || !frontNodes) return;

    buildTicks(ticksGroup);

    const ns = 'http://www.w3.org/2000/svg';
    backNodes.replaceChildren();
    frontNodes.replaceChildren();
    const beads = [0, 1, 2, 3].map(idx => {
      const g = document.createElementNS(ns, 'g');
      g.setAttribute('class', 'pv-bead');
      const halo = document.createElementNS(ns, 'circle');
      halo.setAttribute('class', 'pv-bead-halo');
      halo.setAttribute('r', idx === 0 ? '8.5' : '7');
      const dot = document.createElementNS(ns, 'circle');
      dot.setAttribute('class', 'pv-bead-core');
      dot.setAttribute('r', idx === 0 ? '4.2' : '3.3');
      g.append(halo, dot);
      frontNodes.appendChild(g);
      return { g, dot, halo, orbitIdx: idx === 0 ? 0 : idx === 3 ? 2 : 1, offset: idx === 2 ? Math.PI : 0 };
    });

    if (!stage._pivotBound) {
      stage._pivotBound = true;
      document.addEventListener('pointerdown', e => {
        const grip = e.target.closest?.(state.backgroundOnly ? '#orb-stage' : '#pivot-grip');
        if (!grip || e.button !== 0 || N.REDUCED) return;
        e.stopPropagation();
        const rect = grip.getBoundingClientRect();
        pv.originX = rect.left + rect.width / 2;
        pv.originY = rect.top + rect.height / 2;
        pv.pointerId = e.pointerId;
        pv.grip = grip;
        grip.setPointerCapture(e.pointerId);
        pv.holding = true;
        pv.targetX = Math.max(-22, Math.min(22, (e.clientX - pv.originX) * 0.16));
        pv.targetY = Math.max(-18, Math.min(18, (e.clientY - pv.originY) * 0.16));

        clearTimeout(pv.holdTimer);
        pv.holdTimer = setTimeout(() => {
          if (!pv.holding) return;
          pv.engaged = true;
          stage.classList.add('is-grasped');
          spawnWave(pv.originX, pv.originY, { r0: 28, maxR: 155, strength: 0.8 });
        }, 110);
      });

      document.addEventListener('pointermove', e => {
        if (!pv.holding || e.pointerId !== pv.pointerId) return;
        pv.targetX = Math.max(-24, Math.min(24, (e.clientX - pv.originX) * 0.18));
        pv.targetY = Math.max(-20, Math.min(20, (e.clientY - pv.originY) * 0.18));
      });

      const releaseGrasp = e => {
        if (!pv.holding || (e && e.pointerId !== undefined && e.pointerId !== pv.pointerId)) return;
        clearTimeout(pv.holdTimer);
        const wasEngaged = pv.engaged;
        const power = pv.grasp;
        pv.holding = false;
        pv.engaged = false;
        pv.targetX = 0;
        pv.targetY = 0;
        stage.classList.remove('is-grasped');
        if (pv.grip?.hasPointerCapture(pv.pointerId)) pv.grip.releasePointerCapture(pv.pointerId);
        pv.grip = null;
        if (wasEngaged && !N.REDUCED) {
          spawnWave(pv.originX, pv.originY, {
            r0: 32,
            maxR: isCool() ? 260 : 225,
            dur: isCool() ? 1200 : 1450,
            accent: power > 0.55,
            strength: 0.9 + power * 0.32,
          });
          pulseIsland();
        }
      };
      document.addEventListener('pointerup', releaseGrasp);
      document.addEventListener('pointercancel', releaseGrasp);
      document.addEventListener('lostpointercapture', releaseGrasp);
    }

    let merAngle = 0, breathePhase = 0, swayPhase = 0, coreScale = 1, coreAngle = 0;
    const renderPivot = now => {
      if (!stage.isConnected) { stopPivot(); scheduleHubSync(); return; }
      const dt = Math.min(0.05, pv.lastT ? (now - pv.lastT) / 1000 : 0.016);
      pv.lastT = now;

      const cool = isCool();
      const tempo = N.REDUCED ? 0 : (cool ? 1.18 : 0.88) / Math.max(0.65, N.motionScale());
      const targetGrasp = pv.engaged ? 1 : 0;
      pv.grasp += (targetGrasp - pv.grasp) * Math.min(1, dt * (pv.engaged ? 5.6 : 4.4));
      pv.keyBoost *= Math.pow(0.18, dt);

      const damp = Math.min(1, dt * (cool ? 9.5 : 7.5));
      pv.curX += ((pv.engaged ? pv.targetX : 0) - pv.curX) * damp;
      pv.curY += ((pv.engaged ? pv.targetY : 0) - pv.curY) * damp;

      if (Math.abs(pv.curX) > 0.02 || Math.abs(pv.curY) > 0.02 || pv.grasp > 0.01) {
        const sc = 1 + pv.grasp * 0.045;
        stage.style.transform = `translate3d(${pv.curX.toFixed(2)}px, ${pv.curY.toFixed(2)}px, 0) scale(${sc.toFixed(4)})`;
      } else {
        stage.style.transform = '';
      }

      if (meridian && !N.REDUCED) {
        merAngle = (merAngle + dt * (cool ? 3.4 : 2.2) * (1 + pv.grasp * 3.5 + pv.keyBoost * 2)) % 360;
        meridian.setAttribute('transform', `rotate(${merAngle.toFixed(2)})`);
      }

      if (coreGroup && !N.REDUCED) {
        // 累积相位，避免换主题频率时跳帧；缩放与摆角平滑衔接。
        breathePhase = (breathePhase + dt * (cool ? 2.4 : 1.9)) % (Math.PI * 2);
        swayPhase = (swayPhase + dt * 1.1) % (Math.PI * 2);
        const breathe = 1 + Math.sin(breathePhase) * (cool ? 0.032 : 0.028) + pv.grasp * 0.05;
        const sway = cool ? 0 : Math.sin(swayPhase) * 2.4 + pv.curX * 0.16;
        const blend = Math.min(1, dt * 8);
        coreScale += (breathe - coreScale) * blend;
        coreAngle += (sway - coreAngle) * blend;
        coreGroup.setAttribute('transform', `rotate(${coreAngle.toFixed(2)}) scale(${coreScale.toFixed(4)})`);
      }

      const states = activeSlotStates();
      const speedMul = tempo * (1 + pv.grasp * 2.8 + pv.keyBoost * 2.4 + (state.replying ? 1.15 : 0));

      ORBITS.forEach((orb, idx) => {
        orb.phase = (orb.phase + orb.speed * speedMul * dt) % (Math.PI * 2);
        const rot = orb.baseRot + pv.curX * (0.45 - idx * 0.18) - pv.grasp * (idx === 1 ? 8 : -5);
        const tiltScale = Math.max(0.45, Math.min(1.45, 1 + pv.curY * 0.018 * (idx % 2 ? -1 : 1)));
        const tf = `rotate(${rot.toFixed(2)}) scale(1, ${tiltScale.toFixed(3)})`;
        if (backPlanes[idx]) backPlanes[idx].setAttribute('transform', tf);
        if (frontPlanes[idx]) frontPlanes[idx].setAttribute('transform', tf);
        orb._curRot = rot * Math.PI / 180;
        orb._curRy = orb.ry * tiltScale;
      });

      beads.forEach((b, idx) => {
        const orb = ORBITS[b.orbitIdx];
        const theta = orb.phase + b.offset;
        const lx = Math.cos(theta) * orb.rx;
        const ly = Math.sin(theta) * orb._curRy;
        const c = Math.cos(orb._curRot), s = Math.sin(orb._curRot);
        const x = lx * c - ly * s;
        const y = lx * s + ly * c;
        const inFront = Math.sin(theta) >= 0;
        const depthScale = 0.84 + 0.24 * ((Math.sin(theta) + 1) * 0.5);
        const st = states[idx];
        b.g.setAttribute('data-state', st);
        b.g.setAttribute('transform', `translate(${x.toFixed(2)}, ${y.toFixed(2)}) scale(${depthScale.toFixed(3)})`);
        const parent = inFront ? frontNodes : backNodes;
        if (b.g.parentNode !== parent) parent.appendChild(b.g);
      });

      if (previousBackground) for (const [source, copy] of previousBackground.core) {
        if (source.hasAttribute('transform')) copy.setAttribute('transform', source.getAttribute('transform'));
        copy.style.transform = source.style.transform;
        copy.dataset.state = source.dataset.state || '';
      }

      pivotRAF = N.REDUCED || document.hidden ? 0 : requestAnimationFrame(renderPivot);
    };
    pivotMountedStage._wakePivot = () => { if (!pivotRAF && !N.REDUCED && !document.hidden) pivotRAF = requestAnimationFrame(renderPivot); };

    pivotRAF = requestAnimationFrame(renderPivot);
  }

  /* ================================================================ 4. 打字与发送的中枢共鸣 */
  function pulseIsland() {
    if (N.REDUCED) return;
    const islOrb = $('#isl-orb');
    if (islOrb) {
      islOrb.getAnimations().forEach(a => a.cancel());
      N.anim(islOrb, [{ transform: 'scale(1)' }, { transform: 'scale(1.26)' }, { transform: 'scale(1)' }],
        { duration: N.DUR.move, easing: N.SP.settle.e });
    }
  }

  let lastKeyWave = 0;
  function emitPulse(kind, detail = {}) {
    if (N.REDUCED) return;
    if (kind === 'key') {
      pv.keyBoost = Math.min(1.6, pv.keyBoost + 0.38);
      const now = performance.now();
      if (now - lastKeyWave > 300) {
        lastKeyWave = now;
        const comp = $('#composer');
        if (comp) {
          const r = comp.getBoundingClientRect();
          spawnWave(r.left + r.width * (0.25 + Math.random() * 0.5), r.top + 8, {
            r0: 6, maxR: 76, dur: 900, strength: 0.4,
          });
        }
      }
      return;
    }
    if (kind === 'send') {
      pv.keyBoost = 1.8;
      pulseIsland();
      const x = detail.x ?? contentCenterX();
      const y = detail.y ?? window.innerHeight * 0.82;
      spawnWave(x, y, { r0: 12, maxR: isCool() ? 225 : 195, accent: false, strength: 0.92 });
      return;
    }
    if (kind === 'theme') {
      scheduleHubSync();
      drawStaticOrWake();
    }
  }

  document.addEventListener('visibilitychange', () => { if (!document.hidden) N.syncPivot?.(); });
  matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => { N.syncPivot?.(); });

  const inputEl = $('#input');
  inputEl?.addEventListener('input', () => emitPulse('key'), { passive: true });

  resizeField();
  mountPivot();

  const threadEl = $('#thread');
  if (threadEl) new MutationObserver(() => { mountPivot(); scheduleHubSync(); }).observe(threadEl, { childList: true });

  N.syncPivot = () => {
    mountPivot();
    pivotMountedStage?._wakePivot?.();
    scheduleHubSync();
    drawStaticOrWake();
  };
  N.emitPulse = emitPulse;
  N.prepareThemeBackground = () => {
    const layer = document.createElement('div');
    layer.className = 'theme-background';
    layer.setAttribute('aria-hidden', 'true');
    const body = getComputedStyle(document.body);
    layer.style.background = body.background;
    const shadow = layer.attachShadow({ mode: 'open' });
    const copies = new Map();
    const properties = ['position', 'inset', 'width', 'height', 'margin', 'display', 'overflow', 'border-radius',
      'background', 'box-shadow', 'filter', 'opacity', 'mix-blend-mode', 'transform', 'translate',
      'fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin', 'stop-color', 'stop-opacity'];
    for (const source of [$('.aura'), $('#pivot-core')]) {
      const clone = source.cloneNode(true);
      const originals = [source, ...source.querySelectorAll('*')];
      const cloned = [clone, ...clone.querySelectorAll('*')];
      originals.forEach((node, i) => {
        const copy = cloned[i], style = getComputedStyle(node);
        for (const property of properties) copy.style.setProperty(property, style.getPropertyValue(property));
        copies.set(node, copy);
      });
      shadow.appendChild(clone);
    }
    const oldCanvas = copies.get(canvas);
    oldCanvas.getContext('2d').drawImage(canvas, 0, 0);
    oldCanvas.getContext('2d').setTransform(dpr, 0, 0, dpr, 0, 0);
    previousBackground = {
      ctx: oldCanvas.getContext('2d'), theme: { ...document.documentElement.dataset },
      stars: starMotes.map(p => ({ ...p })), gold: goldCrystals.map(p => ({ ...p })), angle: lusterAngle,
      core: [...copies].filter(([node]) => node.matches('.orb-stage, .pv-meridian, .pv-core-group, .pv-plane, .pv-bead')),
    };
    document.body.appendChild(layer);
    return { element: layer, remove() { previousBackground = null; layer.remove(); } };
  };
})(window.N);
