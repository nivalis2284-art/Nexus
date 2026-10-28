'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (n, cls = 'i') => `<svg class="${cls}" aria-hidden="true"><use href="#i-${n}"/></svg>`;
const wait = ms => new Promise(r => setTimeout(r, ms));
const EASE_OUT = 'cubic-bezier(.22,1,.36,1)';
const rmq = matchMedia('(prefers-reduced-motion: reduce)');
let REDUCED = rmq.matches;
rmq.addEventListener?.('change', e => { REDUCED = e.matches; buildSprings(); });

/* ------------------------------------------------------------------ 弹簧 */
const LINEAR_OK = CSS.supports('animation-timing-function', 'linear(0, 0.5, 1)');
function spring({ stiffness = 200, damping = 20, mass = 1 } = {}) {
  const dt = 1 / 600; let x = 0, v = 0, t = 0; const pts = [[0, 0]];
  while (t < 2.4) {
    const a = (-stiffness * (x - 1) - damping * v) / mass;
    v += a * dt; x += v * dt; t += dt; pts.push([t, x]);
    if (Math.abs(x - 1) < 0.0006 && Math.abs(v) < 0.006) break;
  }
  const dur = t, N = 56, vals = [];
  for (let i = 0, j = 0; i <= N; i++) {
    const tt = (i / N) * dur;
    while (j < pts.length - 1 && pts[j + 1][0] < tt) j++;
    vals.push(+pts[j][1].toFixed(4));
  }
  vals[vals.length - 1] = 1;
  return { e: LINEAR_OK ? `linear(${vals.join(',')})` : 'cubic-bezier(.3,1.25,.5,1)', d: Math.round(dur * 1000) };
}
const SP = {};
function buildSprings() {
  Object.assign(SP, {
    bouncy: spring({ stiffness: 320, damping: 19 }),
    snappy: spring({ stiffness: 460, damping: 34 }),
    soft: spring({ stiffness: 170, damping: 24 }),
    island: spring({ stiffness: 340, damping: 27 }),
  });
  if (REDUCED) for (const k in SP) SP[k] = { e: 'ease', d: 1 };
  const root = document.documentElement.style;
  for (const k of ['bouncy', 'snappy', 'soft']) { root.setProperty(`--sp-${k}`, SP[k].e); root.setProperty(`--sp-${k}-d`, SP[k].d + 'ms'); }
}
buildSprings();
const anim = (el, kf, o) => el.animate(kf, REDUCED ? { ...o, duration: 1, delay: 0 } : o);

function moveInk(ink, target, axis = 'x', instant = false) {
  if (!ink || !target) return;
  const P = axis === 'x' ? 'translateX' : 'translateY', S = axis === 'x' ? 'width' : 'height', O = axis === 'x' ? 'height' : 'width';
  const pos = axis === 'x' ? target.offsetLeft : target.offsetTop;
  const size = axis === 'x' ? target.offsetWidth : target.offsetHeight;
  const p0 = ink._axis === axis ? ink._pos : null, s0 = ink._size;
  ink.style.transform = `${P}(${pos}px)`; ink.style[S] = size + 'px'; ink.style[O] = '';
  ink._pos = pos; ink._size = size; ink._axis = axis;
  if (instant || p0 == null || REDUCED || (p0 === pos && s0 === size)) return;
  ink.getAnimations().forEach(a => a.cancel());
  // 前缘先行、后缘追随，像一滴水被拉过去
  const fwd = pos > p0, lead = fwd ? pos + size : pos, a0 = p0, a1 = p0 + s0;
  const midStart = fwd ? a0 + (pos - a0) * 0.12 : lead;
  const midEnd = fwd ? lead : a1 + (pos + size - a1) * 0.12;
  ink.animate([
    { transform: `${P}(${p0}px)`, [S]: s0 + 'px', easing: 'cubic-bezier(.45,0,.2,1)' },
    { offset: 0.34, transform: `${P}(${midStart}px)`, [S]: (midEnd - midStart) + 'px', easing: SP.bouncy.e },
    { transform: `${P}(${pos}px)`, [S]: size + 'px' },
  ], { duration: Math.max(520, SP.bouncy.d * 0.9) });
}

