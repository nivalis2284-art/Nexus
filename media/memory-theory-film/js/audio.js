/* 声音：全部用 Web Audio 实时合成（无外部采样）。
 * 同一份乐谱既用于浏览器实时播放，也用于 OfflineAudioContext 离线渲染导出。 */
(function () {
  const M = window.MF;
  const TM = M.TIMING;
  const mtof = (m) => 440 * Math.pow(2, (m - 69) / 12);

  // ---------------- 声音构件 ----------------
  function makeNoise(ctx) {
    const R = M.rng(99);
    const len = ctx.sampleRate * 2;
    const b = ctx.createBuffer(1, len, ctx.sampleRate);
    const d = b.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = R() * 2 - 1;
    return b;
  }
  function makeIR(ctx, sec = 2.2) {
    const R = M.rng(1234);
    const len = Math.floor(ctx.sampleRate * sec);
    const b = ctx.createBuffer(2, len, ctx.sampleRate);
    for (let c = 0; c < 2; c++) {
      const d = b.getChannelData(c);
      let lp = 0;
      for (let i = 0; i < len; i++) {
        const x = i / len;
        const n = R() * 2 - 1;
        lp = lp * 0.55 + n * 0.45; // 略暗的混响尾
        d[i] = lp * Math.pow(1 - x, 2.4) * (i < 200 ? i / 200 : 1);
      }
    }
    return b;
  }

  // 构建混音总线
  function buildBus(ctx, dest) {
    const A = { ctx };
    A.master = ctx.createGain();
    A.master.gain.value = 0.9;
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -16;
    comp.knee.value = 10;
    comp.ratio.value = 3;
    comp.attack.value = 0.01;
    comp.release.value = 0.3;
    A.master.connect(comp);
    comp.connect(dest);
    A.musicLP = ctx.createBiquadFilter();
    A.musicLP.type = 'lowpass';
    A.musicLP.frequency.value = 8000;
    A.musicLP.Q.value = 0.5;
    A.music = ctx.createGain();
    A.music.gain.value = 0.8;
    A.music.connect(A.musicLP);
    A.musicLP.connect(A.master);
    A.sfx = ctx.createGain();
    A.sfx.gain.value = 0.85;
    A.sfx.connect(A.master);
    // 混响
    A.rev = ctx.createGain();
    A.rev.gain.value = 1;
    const conv = ctx.createConvolver();
    conv.buffer = M._ir && M._ir.sampleRate === ctx.sampleRate ? M._ir : (M._ir = makeIR(ctx));
    const revOut = ctx.createGain();
    revOut.gain.value = 0.55;
    A.rev.connect(conv);
    conv.connect(revOut);
    revOut.connect(A.master);
    // 附点八分延迟
    A.dly = ctx.createGain();
    const d = ctx.createDelay(2);
    d.delayTime.value = M.BEAT * 0.75;
    const fb = ctx.createGain();
    fb.gain.value = 0.38;
    const dlp = ctx.createBiquadFilter();
    dlp.type = 'lowpass';
    dlp.frequency.value = 2600;
    const pan = ctx.createStereoPanner();
    pan.pan.value = 0.35;
    A.dly.connect(d);
    d.connect(dlp);
    dlp.connect(fb);
    fb.connect(d);
    dlp.connect(pan);
    pan.connect(A.musicLP);
    pan.connect(A.rev);
    A.noise = M._noise && M._noise.sampleRate === ctx.sampleRate ? M._noise : (M._noise = makeNoise(ctx));
    return A;
  }

  function out(A, node, bus, rev = 0, dly = 0, pan = 0) {
    let n = node;
    if (pan) {
      const p = A.ctx.createStereoPanner();
      p.pan.value = pan;
      node.connect(p);
      n = p;
    }
    const outs = [n];
    n.connect(bus);
    if (rev) { const g = A.ctx.createGain(); g.gain.value = rev; n.connect(g); g.connect(A.rev); outs.push(g); }
    if (dly) { const g = A.ctx.createGain(); g.gain.value = dly; n.connect(g); g.connect(A.dly); outs.push(g); }
    if (A._active) A._active.push({ stop: A._lastStop + 0.15, outs });
  }

  const SY = {
    pad(A, t, dur, notes, gain = 0.05, o = {}) {
      const c = A.ctx, att = o.att ?? 1.6, rel = o.rel ?? 2.6;
      const g = c.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(gain, t + att);
      g.gain.setValueAtTime(gain, t + Math.max(att, dur));
      g.gain.linearRampToValueAtTime(0, t + dur + rel);
      const f = c.createBiquadFilter();
      f.type = 'lowpass';
      f.frequency.setValueAtTime(o.cut0 ?? 500, t);
      f.frequency.linearRampToValueAtTime(o.cut ?? 1800, t + att + dur * 0.4);
      f.Q.value = 0.7;
      f.connect(g);
      for (const m of notes) {
        for (const det of [-8, 7]) {
          const os = c.createOscillator();
          os.type = 'sawtooth';
          os.frequency.value = mtof(m);
          os.detune.value = det;
          os.connect(f);
          os.start(t);
          os.stop(t + dur + rel + 0.1);
        }
      }
      out(A, g, A.music, o.rev ?? 0.6, 0, o.pan || 0);
    },
    choir(A, t, dur, notes, gain = 0.03) {
      // 带共振峰的柔和人声感铺底
      const c = A.ctx;
      const g = c.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(gain, t + 2);
      g.gain.setValueAtTime(gain, t + dur);
      g.gain.linearRampToValueAtTime(0, t + dur + 3);
      const f1 = c.createBiquadFilter(); f1.type = 'bandpass'; f1.frequency.value = 700; f1.Q.value = 3;
      const f2 = c.createBiquadFilter(); f2.type = 'bandpass'; f2.frequency.value = 1150; f2.Q.value = 4;
      f1.connect(g); f2.connect(g);
      for (const m of notes) for (const det of [-11, 0, 12]) {
        const os = c.createOscillator();
        os.type = 'sawtooth';
        os.frequency.value = mtof(m);
        os.detune.value = det;
        os.connect(f1); os.connect(f2);
        os.start(t); os.stop(t + dur + 3.2);
      }
      out(A, g, A.music, 0.9);
    },
    pluck(A, t, m, gain = 0.1, o = {}) {
      const c = A.ctx, dec = o.dec ?? 0.7;
      const g = c.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(gain, t + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dec);
      const f = c.createBiquadFilter();
      f.type = 'lowpass';
      f.frequency.setValueAtTime(o.bright ?? 4200, t);
      f.frequency.exponentialRampToValueAtTime(500, t + dec * 0.6);
      f.connect(g);
      const o1 = c.createOscillator(); o1.type = 'triangle'; o1.frequency.value = mtof(m);
      const o2 = c.createOscillator(); o2.type = 'square'; o2.frequency.value = mtof(m) * 1.002;
      const g2 = c.createGain(); g2.gain.value = 0.18;
      o1.connect(f); o2.connect(g2); g2.connect(f);
      o1.start(t); o2.start(t); o1.stop(t + dec + 0.05); o2.stop(t + dec + 0.05);
      out(A, g, o.bus || A.music, o.rev ?? 0.35, o.dly ?? 0.25, o.pan || 0);
    },
    bell(A, t, m, gain = 0.08, o = {}) {
      const c = A.ctx, dur = o.dur ?? 3, fr = mtof(m);
      const g = c.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(gain, t + 0.003);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      const car = c.createOscillator(); car.type = 'sine'; car.frequency.value = fr;
      const mod = c.createOscillator(); mod.type = 'sine'; mod.frequency.value = fr * (o.ratio ?? 3.5);
      const mg = c.createGain();
      mg.gain.setValueAtTime(fr * (o.index ?? 2.2), t);
      mg.gain.exponentialRampToValueAtTime(fr * 0.01, t + dur * 0.6);
      mod.connect(mg); mg.connect(car.frequency);
      car.connect(g);
      car.start(t); mod.start(t); car.stop(t + dur + 0.05); mod.stop(t + dur + 0.05);
      let node = g;
      if (o.lp) { const f = c.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = o.lp; g.connect(f); node = f; }
      out(A, node, o.bus || A.sfx, o.rev ?? 0.7, o.dly ?? 0.15, o.pan || 0);
    },
    bass(A, t, dur, m, gain = 0.12, o = {}) {
      const c = A.ctx;
      const g = c.createGain();
      const att = o.att ?? 0.02, rel = o.rel ?? 0.3;
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(gain, t + att);
      g.gain.setValueAtTime(gain * (o.sus ?? 0.8), t + Math.max(att, dur - rel));
      g.gain.linearRampToValueAtTime(0, t + dur);
      const f = c.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = o.cut ?? 320;
      f.connect(g);
      const o1 = c.createOscillator(); o1.type = 'sine'; o1.frequency.value = mtof(m);
      const o2 = c.createOscillator(); o2.type = 'triangle'; o2.frequency.value = mtof(m);
      const g2 = c.createGain(); g2.gain.value = 0.5;
      o1.connect(f); o2.connect(g2); g2.connect(f);
      o1.start(t); o2.start(t); o1.stop(t + dur + 0.05); o2.stop(t + dur + 0.05);
      out(A, g, A.music, 0.05);
    },
    kick(A, t, gain = 0.5) {
      const c = A.ctx;
      const os = c.createOscillator(); os.type = 'sine';
      os.frequency.setValueAtTime(140, t);
      os.frequency.exponentialRampToValueAtTime(42, t + 0.14);
      const g = c.createGain();
      g.gain.setValueAtTime(gain, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.5);
      os.connect(g); os.start(t); os.stop(t + 0.55);
      out(A, g, A.music, 0.05);
    },
    hat(A, t, gain = 0.03, dec = 0.05) {
      const c = A.ctx;
      const s = c.createBufferSource(); s.buffer = A.noise;
      const f = c.createBiquadFilter(); f.type = 'highpass'; f.frequency.value = 7500;
      const g = c.createGain();
      g.gain.setValueAtTime(gain, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dec);
      s.connect(f); f.connect(g);
      s.start(t, (t * 7.31) % 1.5); s.stop(t + dec + 0.02);
      out(A, g, A.music, 0.1, 0.1, 0.25);
    },
    tick(A, t, gain = 0.05, freq = 3200, pan = 0) {
      const c = A.ctx;
      const s = c.createBufferSource(); s.buffer = A.noise;
      const f = c.createBiquadFilter(); f.type = 'bandpass'; f.frequency.value = freq; f.Q.value = 5;
      const g = c.createGain();
      g.gain.setValueAtTime(gain, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.03);
      s.connect(f); f.connect(g);
      s.start(t, (t * 3.17) % 1.5); s.stop(t + 0.05);
      out(A, g, A.sfx, 0.15, 0, pan);
    },
    whoosh(A, t, dur, f0, f1, gain = 0.12, p0 = -0.6, p1 = 0.6) {
      const c = A.ctx;
      const s = c.createBufferSource(); s.buffer = A.noise; s.loop = true;
      const f = c.createBiquadFilter(); f.type = 'bandpass'; f.Q.value = 1.3;
      f.frequency.setValueAtTime(f0, t);
      f.frequency.exponentialRampToValueAtTime(f1, t + dur);
      const g = c.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(gain, t + dur * 0.6);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      const p = c.createStereoPanner();
      p.pan.setValueAtTime(p0, t);
      p.pan.linearRampToValueAtTime(p1, t + dur);
      s.connect(f); f.connect(g); g.connect(p);
      s.start(t); s.stop(t + dur + 0.05);
      out(A, p, A.sfx, 0.4);
    },
    riser(A, t, dur, gain = 0.1) {
      const c = A.ctx;
      const s = c.createBufferSource(); s.buffer = A.noise; s.loop = true;
      const f = c.createBiquadFilter(); f.type = 'highpass';
      f.frequency.setValueAtTime(300, t);
      f.frequency.exponentialRampToValueAtTime(7000, t + dur);
      const g = c.createGain();
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(gain, t + dur * 0.95);
      g.gain.linearRampToValueAtTime(0, t + dur + 0.04);
      s.connect(f); f.connect(g);
      s.start(t); s.stop(t + dur + 0.1);
      out(A, g, A.sfx, 0.5);
      const os = c.createOscillator(); os.type = 'sawtooth';
      os.frequency.setValueAtTime(110, t);
      os.frequency.exponentialRampToValueAtTime(880, t + dur);
      const lp = c.createBiquadFilter(); lp.type = 'lowpass'; lp.frequency.value = 1800;
      const g2 = c.createGain();
      g2.gain.setValueAtTime(0.0001, t);
      g2.gain.exponentialRampToValueAtTime(gain * 0.25, t + dur * 0.95);
      g2.gain.linearRampToValueAtTime(0, t + dur + 0.04);
      os.connect(lp); lp.connect(g2);
      os.start(t); os.stop(t + dur + 0.1);
      out(A, g2, A.sfx, 0.5);
    },
    boom(A, t, gain = 0.5, dur = 3) {
      const c = A.ctx;
      const os = c.createOscillator(); os.type = 'sine';
      os.frequency.setValueAtTime(70, t);
      os.frequency.exponentialRampToValueAtTime(30, t + dur);
      const g = c.createGain();
      g.gain.setValueAtTime(gain, t);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      os.connect(g); os.start(t); os.stop(t + dur + 0.05);
      out(A, g, A.sfx, 0.3);
      const s = c.createBufferSource(); s.buffer = A.noise; s.loop = true;
      const f = c.createBiquadFilter(); f.type = 'lowpass';
      f.frequency.setValueAtTime(900, t);
      f.frequency.exponentialRampToValueAtTime(80, t + dur * 0.7);
      const g2 = c.createGain();
      g2.gain.setValueAtTime(gain * 0.5, t);
      g2.gain.exponentialRampToValueAtTime(0.0001, t + dur * 0.8);
      s.connect(f); f.connect(g2);
      s.start(t); s.stop(t + dur);
      out(A, g2, A.sfx, 0.6);
    },
    glitch(A, t, n = 10, gain = 0.07) {
      const c = A.ctx;
      for (let k = 0; k < n; k++) {
        const tt = t + k * 0.045 + M.hash(k + t) * 0.02;
        const os = c.createOscillator(); os.type = 'square';
        os.frequency.value = 120 + M.hash(k * 3 + t) * 2400;
        const g = c.createGain();
        g.gain.setValueAtTime(gain, tt);
        g.gain.setValueAtTime(0, tt + 0.03);
        os.connect(g); os.start(tt); os.stop(tt + 0.035);
        out(A, g, A.sfx, 0.1, 0, (M.hash(k * 7 + t) - 0.5) * 1.6);
      }
    },
    shimmer(A, t, dur, gain = 0.04) {
      // 高频颤动的噪声，用于“概率云”
      const c = A.ctx;
      const s = c.createBufferSource(); s.buffer = A.noise; s.loop = true;
      const f = c.createBiquadFilter(); f.type = 'bandpass'; f.frequency.value = 6000; f.Q.value = 6;
      const trem = c.createGain(); trem.gain.value = 0;
      const lfo = c.createOscillator(); lfo.frequency.value = 11;
      const lg = c.createGain(); lg.gain.value = 0.5;
      lfo.connect(lg); lg.connect(trem.gain);
      const g = c.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(gain, t + 0.8);
      g.gain.setValueAtTime(gain, t + dur - 0.8);
      g.gain.linearRampToValueAtTime(0, t + dur);
      s.connect(f); f.connect(trem); trem.connect(g);
      s.start(t); s.stop(t + dur + 0.05); lfo.start(t); lfo.stop(t + dur + 0.05);
      out(A, g, A.sfx, 0.6, 0.2);
    },
    blip(A, t, m0 = 81, m1 = 88, gain = 0.06) {
      const c = A.ctx;
      const os = c.createOscillator(); os.type = 'sine';
      os.frequency.setValueAtTime(mtof(m0), t);
      os.frequency.exponentialRampToValueAtTime(mtof(m1), t + 0.08);
      const g = c.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(gain, t + 0.005);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.25);
      os.connect(g); os.start(t); os.stop(t + 0.3);
      out(A, g, A.sfx, 0.4, 0.3);
    },
    buzz(A, t, gain = 0.05) {
      const c = A.ctx;
      for (const m of [49, 50]) {
        const os = c.createOscillator(); os.type = 'sawtooth'; os.frequency.value = mtof(m);
        const f = c.createBiquadFilter(); f.type = 'lowpass'; f.frequency.value = 900;
        const g = c.createGain();
        g.gain.setValueAtTime(0, t);
        g.gain.linearRampToValueAtTime(gain, t + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.45);
        os.connect(f); f.connect(g); os.start(t); os.stop(t + 0.5);
        out(A, g, A.sfx, 0.2);
      }
    },
    drone(A, t, dur, notes, gain = 0.08) {
      const c = A.ctx;
      const g = c.createGain();
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(gain, t + 3);
      g.gain.setValueAtTime(gain, t + dur - 2);
      g.gain.linearRampToValueAtTime(0, t + dur);
      for (const m of notes) {
        const os = c.createOscillator(); os.type = 'sine'; os.frequency.value = mtof(m);
        const o2 = c.createOscillator(); o2.type = 'sine'; o2.frequency.value = mtof(m) * 1.004;
        os.connect(g); o2.connect(g);
        os.start(t); o2.start(t); os.stop(t + dur + 0.05); o2.stop(t + dur + 0.05);
      }
      out(A, g, A.music, 0.4);
    },
  };

  // ---------------- 乐谱 ----------------
  const CH = {
    Dm9: { b: 38, p: [57, 60, 64, 65, 69] },
    Bb: { b: 34, p: [53, 57, 62, 64, 69] },
    F: { b: 41, p: [57, 60, 64, 67, 72] },
    C: { b: 36, p: [55, 62, 64, 67, 69] },
    Gm9: { b: 43, p: [58, 62, 65, 69, 70] },
    A7s: { b: 45, p: [55, 62, 64, 67, 69] },
    Dmaj: { b: 38, p: [57, 61, 64, 66, 69] },
  };
  const PROG = [
    [27, 'Dm9', 6], [33, 'Bb', 6], [39, 'F', 6], [45, 'C', 3],
    [48, 'Dm9', 6], [54, 'Bb', 6], [60, 'F', 6], [66, 'C', 3],
    [69, 'Dm9', 6], [75, 'Bb', 6], [81, 'F', 6], [87, 'C', 3],
    [90, 'Gm9', 6], [96, 'Bb', 6], [102, 'F', 3], [105, 'A7s', 3.6],
    [108.6, 'Dm9', 2.4], [111, 'Bb', 3], [114, 'F', 3], [117, 'C', 3],
    [120, 'Dm9', 6], [126, 'Bb', 6], [132, 'Gm9', 3], [135, 'A7s', 3],
    [138, 'Dm9', 6], [144, 'Bb', 4.5], [148.5, 'Dmaj', 4.5], [153, 'F', 3], [156, 'C', 3],
    [159, 'Dm9', 6], [165, 'Bb', 6], [171, 'F', 3], [174, 'C', 3],
    [177, 'Gm9', 3], [180, 'Bb', 3], [183, 'F', 3], [186, 'C', 3], [189, 'A7s', 3],
    [192, 'Dm9', 6], [198, 'Bb', 3], [201, 'Gm9', 3], [204, 'F', 3], [207, 'Bb', 3], [210, 'C', 4.5],
    [214.5, 'Dmaj', 8],
  ];
  const chordAt = (t) => {
    let c = PROG[0];
    for (const p of PROG) if (p[0] <= t + 1e-6) c = p;
    return CH[c[1]];
  };
  // 激活与网络中用于节点的五声音阶
  const PENTA = [62, 65, 67, 69, 72, 74, 77, 79, 81, 84];

  // 音乐总线低通的自动化关键帧
  const CUT = [[0, 900], [12, 500], [23.9, 3200], [24.0, 1200], [27, 4500], [30, 7000], [108.5, 7000], [109, 700], [110.5, 6500],
    [137.5, 6500], [139, 1000], [148.3, 1100], [149, 6000], [159, 7500], [200.2, 7500], [200.8, 350], [202.4, 380], [205.5, 9500], [225, 9500]];
  const cutAt = (t) => {
    for (let i = 1; i < CUT.length; i++) if (t <= CUT[i][0]) {
      const [a, fa] = CUT[i - 1], [b, fb] = CUT[i];
      return fa * Math.pow(fb / fa, (t - a) / Math.max(1e-6, b - a));
    }
    return CUT[CUT.length - 1][1];
  };
  M.audioCut = cutAt;

  function buildScore() {
    const ev = [];
    const add = (t, dur, fn) => ev.push({ t, dur, fn });
    const B = M.BEAT;

    // —— 序幕 ——
    add(0.3, 12.5, (A, w) => SY.drone(A, w, 12.5, [26, 33], 0.16));
    for (let t = 1.0; t < 11; t += 1.5) {
      add(t, 0.5, (A, w) => SY.kick(A, w, 0.28));
      add(t + 0.24, 0.5, (A, w) => SY.kick(A, w, 0.16));
    }
    add(3.2, 4, (A, w) => SY.bell(A, w, 81, 0.05, { dur: 5 }));
    add(7.6, 4, (A, w) => SY.bell(A, w, 77, 0.05, { dur: 5 }));
    add(10.6, 1.6, (A, w) => SY.riser(A, w, 1.5, 0.08));
    add(11.6, 1, (A, w) => SY.whoosh(A, w, 1.4, 600, 5000, 0.08, 0, 0));

    // —— 档案 ——
    add(12, 12, (A, w) => SY.pad(A, w, 11.6, [50, 51, 57], 0.03, { att: 3, rel: 0.3, cut0: 300, cut: 900 }));
    for (let t = 12; t < 23.6; t += B / 2) {
      const k = (t - 12) / 11.6;
      add(t, 0.4, (A, w) => SY.pluck(A, w, 38, 0.09 + 0.05 * k, { dec: 0.25, bright: 600 + 2200 * k, rev: 0.1, dly: 0 }));
    }
    TM.cellLand.forEach((tl, c) => { if (c % 2 === 0) add(tl, 0.05, (A, w) => SY.tick(A, w, 0.035, 2400 + M.hash(c) * 2400, (M.hash(c * 5) - 0.5) * 1.2)); });
    add(19.8, 3.6, (A, w) => SY.whoosh(A, w, 3.6, 800, 4000, 0.05, -0.8, 0.8));
    add(22.3, 1.7, (A, w) => SY.riser(A, w, 1.65, 0.12));
    add(23.55, 0.5, (A, w) => SY.glitch(A, w, 12, 0.06));
    add(24.0, 3, (A, w) => SY.boom(A, w, 0.7, 3.5));
    add(24.0, 3, (A, w) => SY.bell(A, w, 50, 0.08, { dur: 5, ratio: 1.41, index: 4 }));
    add(24.4, 3, (A, w) => SY.pad(A, w, 2.8, CH.Dm9.p, 0.025, { att: 1.5, rel: 2, cut: 1200 }));

    // —— 和声铺底与低音（贯穿 27–225） ——
    for (const [t, name, d] of PROG) {
      const c = CH[name];
      const night = t >= 138 && t < 148.5;
      const big = t >= 207;
      add(t, d + 3, (A, w) => SY.pad(A, w, d, c.p, big ? 0.04 : 0.032, { cut: night ? 900 : big ? 3200 : 2000 }));
      if (t >= 48 && !(t >= 120 && t < 138)) add(t, d, (A, w) => SY.bass(A, w, d, c.b, 0.14, { att: 0.3, rel: 1.2 }));
      if (t >= 177 && t < 192) add(t, d + 3, (A, w) => SY.choir(A, w, d, c.p.map((m) => m + 12).slice(1, 4), 0.02));
      if (t >= 214.5) add(t, d + 3, (A, w) => SY.choir(A, w, d, [69, 73, 76, 78], 0.025));
    }

    // —— 琶音 ——
    function arp(t0, t1, step, gain, o = {}) {
      const pat = o.pat || [0, 2, 4, 1, 3, 2, 4, 3];
      let n = 0;
      for (let t = t0; t < t1 - 1e-6; t += step, n++) {
        const c = chordAt(t);
        const notes = c.p.map((m) => m + (o.oct ?? 12));
        const m = notes[pat[n % pat.length] % notes.length] + (o.lift && n % 16 >= 8 ? 12 : 0);
        const acc = n % 4 === 0 ? 1.15 : 0.85;
        const gg = gain * acc * (o.ramp ? M.lerp(o.ramp[0], o.ramp[1], (t - t0) / (t1 - t0)) : 1);
        add(t, 0.8, (A, w) => SY.pluck(A, w, m, gg, { dec: o.dec ?? 0.6, dly: o.dly ?? 0.25, pan: o.pan ?? (n % 2 ? 0.25 : -0.25), bright: o.bright }));
      }
    }
    arp(27, 48, B, 0.07, { dec: 1.0 });
    arp(48, 69, B / 2, 0.065);
    arp(69, 90, B / 4, 0.05, { dly: 0.35, lift: true });
    arp(90, 108.6, B / 4, 0.05, { dly: 0.3, lift: true, ramp: [0.8, 1.3] });
    arp(111, 120, B / 2, 0.055);
    arp(148.5, 159, B / 2, 0.055, { dec: 0.9 });
    // 分支：三声部的复节奏
    arp(160.2, 177, B / 2, 0.045, { oct: 24, pat: [0, 2, 4, 2], pan: -0.5 });
    arp(160.9, 177, B * 0.75, 0.045, { oct: 12, pat: [4, 3, 1, 2], pan: 0.5 });
    arp(161.6, 177, B, 0.05, { oct: 0, pat: [0, 1, 2], pan: 0, dec: 1.0 });
    arp(177, 192, B / 4, 0.045, { pat: [0, 1, 2, 3, 4, 3, 2, 1], lift: true, dly: 0.3 });
    arp(192, 200.4, B / 2, 0.055);
    arp(203, 207, B / 4, 0.045, { ramp: [0.4, 1.2], lift: true });
    arp(207, 214.5, B / 4, 0.06, { lift: true, dly: 0.3 });
    // 低音脉冲与节奏
    for (let t = 69; t < 90; t += B / 2) add(t, 0.3, (A, w) => SY.bass(A, w, 0.3, chordAt(t).b, 0.06, { cut: 500 }));
    for (let t = 75; t < 90; t += B / 2) add(t + B / 4, 0.1, (A, w) => SY.hat(A, w, 0.022));
    for (let t = 81; t < 90; t += B * 2) add(t, 0.5, (A, w) => SY.kick(A, w, 0.3));
    for (let t = 96; t < 108; t += B) add(t, 0.5, (A, w) => SY.kick(A, w, 0.28 + 0.12 * (t - 96) / 12));
    for (let t = 96; t < 108.5; t += B / 2) add(t + B / 4, 0.1, (A, w) => SY.hat(A, w, 0.025));
    for (let t = 207; t < 214.4; t += B) add(t, 0.5, (A, w) => SY.kick(A, w, 0.42));
    for (let t = 207; t < 214.4; t += B / 2) add(t + B / 4, 0.1, (A, w) => SY.hat(A, w, 0.03));
    for (let t = 120; t < 138; t += B) add(t, 0.05, (A, w) => SY.tick(A, w, 0.03, 1800, 0));

    // —— 场景 2：来源与事件 ——
    M.sources.forEach((s, k) => {
      if (k % 3) return;
      const tt = 27 + 0.8 * M.hash(s.cell) + 3.2 + 0.4 * M.hash(s.cell * 2) - 0.2;
      add(tt, 1, (A, w) => SY.bell(A, w, PENTA[k % 5] + 12, 0.025, { dur: 1.6, index: 1.2, pan: (M.hash(k) - 0.5) }));
    });
    TM.evAppear.forEach((tt, i) => add(tt, 2, (A, w) => SY.bell(A, w, PENTA[i % PENTA.length], 0.03, { dur: 2.4, ratio: 2, index: 1.5, pan: (M.hash(i * 2) - 0.5) * 1.2 })));
    add(35.2, 0.3, (A, w) => SY.blip(A, w));
    add(40.5, 3, (A, w) => { SY.bell(A, w, 81, 0.06, { dur: 3, pan: -0.4 }); SY.bell(A, w, 88, 0.05, { dur: 3, pan: 0.4 }); });
    for (const t of [41.5, 42.7, 43.9, 45.1]) add(t, 2, (A, w) => SY.bell(A, w, 62, 0.035, { dur: 2, ratio: 1.41, index: 1 }));
    add(45.8, 1.5, (A, w) => SY.whoosh(A, w, 1.5, 3000, 600, 0.05));
    add(47.6, 2, (A, w) => SY.whoosh(A, w, 2.2, 400, 3000, 0.08));

    // —— 场景 3：记忆团 ——
    TM.hullT.forEach((t, j) => add(t, 4, (A, w) => SY.bell(A, w, [74, 77, 81, 84][j], 0.07, { dur: 4.5, pan: [-0.5, 0.5, 0.3, -0.3][j] })));
    add(56.4, 0.8, (A, w) => SY.riser(A, w, 0.7, 0.05));
    add(57.1, 3, (A, w) => { for (const m of [74, 78, 81]) SY.bell(A, w, m, 0.03, { dur: 3 }); });
    for (let t = 61.8; t < 64.6; t += 0.085) add(t, 0.05, (A, w) => SY.tick(A, w, 0.025, 4200, -0.4));
    add(65.4, 1, (A, w) => { SY.whoosh(A, w, 0.8, 1000, 4000, 0.04, -0.5, 0.3); });
    [0, 0.18, 0.36].forEach((d, q) => add(66.2 + d, 1, (A, w) => SY.pluck(A, w, [81, 84, 88][q], 0.07, { bus: A.sfx })));

    // —— 场景 4：关联网络 ——
    add(68.8, 4, (A, w) => SY.whoosh(A, w, 4, 200, 2500, 0.12, -0.8, 0.8));
    add(69.0, 3, (A, w) => SY.boom(A, w, 0.3, 3));
    const TYPE_NOTE = { sim: 76, seq: 79, prom: 81, corr: 83, ana: 86 };
    TM.linkReveal.forEach((tr, l) => add(tr + 0.1, 1, (A, w) => SY.pluck(A, w, TYPE_NOTE[TM.linkType[l]] + (l % 3 === 0 ? 5 : 0), 0.05, { bus: A.sfx, dly: 0.35, pan: (M.hash(l) - 0.5) * 1.4 })));
    add(83, 4, (A, w) => SY.boom(A, w, 0.22, 4));
    add(84.2, 3, (A, w) => SY.bell(A, w, 57, 0.05, { dur: 4, ratio: 1.41 }));

    // —— 场景 5：情境 · 激活 · 概率 ——
    for (const t0 of [91.6, 109.0]) {
      add(t0, 1.2, (A, w) => SY.whoosh(A, w, 1.1, 2500, 800, 0.06, 0.9, 0.1));
      add(t0 + 1.1, 0.3, (A, w) => SY.blip(A, w, 86, 93, 0.06));
    }
    add(93, 3, (A, w) => SY.boom(A, w, 0.3, 3));
    add(110.5, 3, (A, w) => SY.boom(A, w, 0.25, 3));
    for (const c of TM.actCross) {
      const m = PENTA[(c.i * 3) % PENTA.length] + (c.ctx === 'research' ? 0 : 0);
      add(c.t, 2, (A, w) => SY.bell(A, w, m, c.seed ? 0.07 : 0.045, { dur: 2.5, ratio: c.ctx === 'research' ? 2.01 : 3.5, pan: (M.hash(c.i) - 0.5) * 1.2 }));
    }
    add(99, 2, (A, w) => SY.riser(A, w, 2, 0.06));
    add(101.4, 4.8, (A, w) => SY.shimmer(A, w, 4.8, 0.05));
    add(113.6, 3.8, (A, w) => SY.shimmer(A, w, 3.8, 0.045));
    for (const ctxName of ['release', 'research']) TM.WS_T[ctxName].forEach((t, q) => {
      add(t, 1, (A, w) => { SY.bell(A, w, 88 + q * 2, 0.04, { dur: 1.2, index: 3 }); SY.bell(A, w, 93 + q * 2, 0.025, { dur: 1 }); });
      add(t + 0.8, 0.2, (A, w) => SY.tick(A, w, 0.06, 2000, 0.6));
    });
    add(106.4, 2, (A, w) => SY.bell(A, w, 74, 0.06, { dur: 3 }));
    add(106.9, 2, (A, w) => SY.bell(A, w, 79, 0.04, { dur: 3 }));
    add(108.55, 1.4, (A, w) => { SY.whoosh(A, w, 1.2, 5000, 300, 0.1, 0.5, -0.5); SY.glitch(A, w, 6, 0.04); });

    // —— 场景 6：四个量 ——
    [0, 1, 2, 3].forEach((j) => add(120.8 + j * 0.9, 3, (A, w) => { SY.bell(A, w, [62, 69, 74, 81][j], 0.06, { dur: 3.5 }); SY.kick(A, w, 0.12); }));
    add(126.3, 1.4, (A, w) => SY.whoosh(A, w, 1.3, 600, 2500, 0.05));
    TM.recall.forEach((t, q) => add(t, 2, (A, w) => SY.bell(A, w, 86, 0.035 + q * 0.012, { dur: 2, index: 1 + q * 0.4 })));
    add(127.6, 4, (A, w) => SY.bell(A, w, 50, 0.05, { dur: 6, ratio: 1.41, index: 0.5 }));
    add(133.4, 4, (A, w) => SY.boom(A, w, 0.25, 4));

    // —— 场景 7：巩固与修订 ——
    add(138.6, 7.5, (A, w) => SY.whoosh(A, w, 7.4, 300, 1400, 0.05, -0.9, 0.9));
    for (let t = 138.75; t < 148; t += B) add(t, 3, (A, w) => SY.bell(A, w, PENTA[Math.floor(M.hash(t) * 6)] + 12, 0.025, { dur: 3, lp: 2500 }));
    add(144.4, 0.3, (A, w) => SY.blip(A, w, 76, 81, 0.05));
    add(146.8, 1.7, (A, w) => SY.riser(A, w, 1.7, 0.1));
    add(148.5, 3, (A, w) => { SY.glitch(A, w, 6, 0.05); SY.boom(A, w, 0.4, 3); for (const m of [74, 78, 81, 85]) SY.bell(A, w, m, 0.04, { dur: 5 }); });
    add(150.2, 1.4, (A, w) => SY.whoosh(A, w, 1.4, 2000, 500, 0.04));
    add(151.4, 1, (A, w) => SY.pluck(A, w, 81, 0.06, { bus: A.sfx }));

    // —— 场景 8：分支 ——
    add(159.2, 1.6, (A, w) => SY.whoosh(A, w, 1.6, 300, 2000, 0.07, -0.9, 0.9));
    TM.branchDraw.forEach((t, j) => add(t, 3, (A, w) => SY.bell(A, w, [81, 76, 69][j], 0.05, { dur: 3, pan: [-0.3, 0.2, 0.4][j] })));
    add(164.8, 1.2, (A, w) => { for (let q = 0; q < 6; q++) SY.pluck(A, w + q * 0.12, 86 - q * 3, 0.04, { bus: A.sfx }); });
    add(165.8, 1, (A, w) => { SY.kick(A, w, 0.2); SY.bell(A, w, 62, 0.05, { dur: 3, ratio: 1.41 }); });
    add(166.4, 2, (A, w) => SY.bell(A, w, 74, 0.04, { dur: 2, lp: 900 }));
    add(169.45, 0.5, (A, w) => SY.buzz(A, w, 0.05));

    // —— 场景 9：投影 ——
    TM.projT.forEach((t, j) => add(t, 4, (A, w) => { SY.bell(A, w, [74, 79, 81, 86][j], 0.06, { dur: 4 }); SY.whoosh(A, w, 1.2, 400, 3500, 0.03, 0, 0); }));

    // —— 场景 10：最终形态 ——
    add(190.4, 1.6, (A, w) => SY.riser(A, w, 1.6, 0.1));
    add(192, 4, (A, w) => SY.boom(A, w, 0.45, 4));
    add(198.6, 2, (A, w) => SY.shimmer(A, w, 2.4, 0.03));
    add(200.3, 1, (A, w) => { SY.glitch(A, w, 8, 0.05); SY.whoosh(A, w, 1.6, 3000, 200, 0.08); });
    add(202.4, 2, (A, w) => SY.shimmer(A, w, 3.2, 0.035));
    for (let q = 0; q < 18; q++) add(203.2 + q * 0.18, 2, (A, w) => SY.bell(A, w, PENTA[q % PENTA.length], 0.03, { dur: 2, pan: (M.hash(q) - 0.5) }));
    add(205.4, 1.6, (A, w) => SY.riser(A, w, 1.6, 0.1));
    for (const t of [207.6, 209.0, 210.6]) add(t, 3, (A, w) => { SY.boom(A, w, 0.25, 2.5); SY.bell(A, w, 69, 0.04, { dur: 3 }); });
    add(214.6, 6, (A, w) => { SY.boom(A, w, 0.6, 5); for (const m of [62, 69, 73, 76, 78, 81]) SY.bell(A, w, m, 0.04, { dur: 7, index: 1.5 }); });
    add(220.6, 4, (A, w) => SY.bell(A, w, 86, 0.04, { dur: 4.5 }));

    ev.sort((a, b) => a.t - b.t);
    return ev;
  }

  // ---------------- 播放器 ----------------
  const AUD = (M.Audio = {});
  AUD.score = null;
  function ensureScore() { if (!AUD.score) AUD.score = buildScore(); return AUD.score; }

  function automate(A, t0, t1, when0, filmT0) {
    // 把 [t0,t1] 的低通关键帧排进 AudioParam（以 0.1s 步进近似）
    const p = A.musicLP.frequency;
    for (let t = t0; t <= t1 + 1e-6; t += 0.1) p.linearRampToValueAtTime(cutAt(t), when0 + (t - filmT0));
  }

  AUD.start = function (pos) {
    ensureScore();
    if (!AUD.ctx) AUD.ctx = new (window.AudioContext || window.webkitAudioContext)({ latencyHint: 'playback' });
    const ctx = AUD.ctx;
    if (ctx.state === 'suspended') ctx.resume();
    AUD.stop();
    const A = buildBus(ctx, ctx.destination);
    const now = ctx.currentTime + 0.08;
    A.master.gain.setValueAtTime(0, ctx.currentTime);
    A.master.gain.linearRampToValueAtTime(0.9, now + 0.25);
    A.musicLP.frequency.setValueAtTime(cutAt(pos), now);
    AUD.cur = { A, ctxT0: now, pos0: pos, schedT: pos, idx: 0 };
    // 已开始但尚未结束的长音也要排入（Web Audio 会从正确位置开始包络）
    const S = AUD.score;
    let i = 0;
    while (i < S.length && S[i].t < pos) {
      const e = S[i];
      if (e.t + e.dur > pos && e.dur > 0.6) e.fn(A, now + (e.t - pos));
      i++;
    }
    AUD.cur.idx = i;
    AUD.tick();
    AUD.timer = setInterval(AUD.tick, 100);
  };
  AUD.tick = function () {
    const c = AUD.cur;
    if (!c) return;
    const ctx = AUD.ctx, S = AUD.score;
    const film = c.pos0 + (ctx.currentTime - c.ctxT0);
    const until = film + 1.5;
    while (c.idx < S.length && S[c.idx].t < until) {
      const e = S[c.idx];
      e.fn(c.A, c.ctxT0 + (e.t - c.pos0));
      c.idx++;
    }
    automate(c.A, c.schedT, until, c.ctxT0, c.pos0);
    c.schedT = until + 0.1;
  };
  AUD.time = function () {
    const c = AUD.cur;
    if (!c) return null;
    return c.pos0 + (AUD.ctx.currentTime - c.ctxT0);
  };
  AUD.stop = function () {
    clearInterval(AUD.timer);
    const c = AUD.cur;
    AUD.cur = null;
    if (!c) return;
    const ctx = AUD.ctx, g = c.A.master.gain;
    g.cancelScheduledValues(ctx.currentTime);
    g.setValueAtTime(g.value, ctx.currentTime);
    g.linearRampToValueAtTime(0, ctx.currentTime + 0.12);
    setTimeout(() => { try { c.A.master.disconnect(); } catch (e) { /* 已断开 */ } }, 300);
  };

  // 离线渲染，返回交错的 16 位 PCM（供导出）
  AUD.renderOffline = async function (dur = M.DURATION, sr = 48000) {
    const S = ensureScore();
    const ctx = new OfflineAudioContext(2, Math.ceil(dur * sr), sr);
    const A = buildBus(ctx, ctx.destination);
    A._active = [];
    A._lastStop = 0;
    const origOsc = ctx.createOscillator.bind(ctx);
    const origBuf = ctx.createBufferSource.bind(ctx);
    const trackStop = (node) => {
      const origStop = node.stop.bind(node);
      node.stop = (when = 0) => {
        if (when > A._lastStop) A._lastStop = when;
        return origStop(when);
      };
      return node;
    };
    ctx.createOscillator = () => trackStop(origOsc());
    ctx.createBufferSource = () => trackStop(origBuf());

    A.master.gain.setValueAtTime(0, 0);
    A.master.gain.linearRampToValueAtTime(0.9, 0.3);
    A.master.gain.setValueAtTime(0.9, dur - 2.4);
    A.master.gain.linearRampToValueAtTime(0, dur - 0.05);
    A.musicLP.frequency.setValueAtTime(cutAt(0), 0);
    automate(A, 0, dur, 0, 0);

    const STEP = 4;
    let idx = 0;
    const stepAt = (now) => {
      A._active = A._active.filter((v) => {
        if (v.stop <= now) {
          for (const o of v.outs) { try { o.disconnect(); } catch (e) { /* ignore */ } }
          return false;
        }
        return true;
      });
      const tEnd = Math.min(dur, now + STEP + 0.05);
      while (idx < S.length && S[idx].t < tEnd) {
        A._lastStop = S[idx].t;
        S[idx].fn(A, S[idx].t);
        idx++;
      }
    };
    stepAt(0);
    for (let t = STEP; t < dur; t += STEP) {
      const snap = t;
      ctx.suspend(snap).then(() => {
        stepAt(snap);
        ctx.resume();
      });
    }
    const buf = await ctx.startRendering();
    const L = buf.getChannelData(0), Rr = buf.getChannelData(1);
    let peak = 0;
    for (let i = 0; i < L.length; i++) peak = Math.max(peak, Math.abs(L[i]), Math.abs(Rr[i]));
    const pcm = new Int16Array(L.length * 2);
    for (let i = 0; i < L.length; i++) {
      pcm[i * 2] = Math.max(-32768, Math.min(32767, Math.round(L[i] * 32767)));
      pcm[i * 2 + 1] = Math.max(-32768, Math.min(32767, Math.round(Rr[i] * 32767)));
    }
    AUD._pcm = pcm;
    return { samples: L.length, sr, peak, events: S.length };
  };
  AUD.pcmChunk = function (start, len) {
    const p = AUD._pcm.subarray(start, start + len);
    const u8 = new Uint8Array(p.buffer, p.byteOffset, p.byteLength);
    let s = '';
    for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    return btoa(s);
  };
})();
