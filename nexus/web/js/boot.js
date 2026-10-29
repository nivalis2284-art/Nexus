/* ==========================================================================
   启动：先上界面语言，再读取服务端快照
   ========================================================================== */
'use strict';

(function (N) {
  const { $, $$, state } = N;
  const fresh = N.seed();
  for (const key of Object.keys(fresh)) {
    if (state[key] === undefined) state[key] = fresh[key];
  }
  state.files = [];
  if (!Array.isArray(state.items)) state.items = [];
  if (!Array.isArray(state.works)) state.works = [];
  if (!Array.isArray(state.queue)) state.queue = [];

  document.documentElement.dataset.theme = state.theme === 'night' ? 'night' : 'day';
  if (state.palette === 'blue') state.palette = 'cool';
  else if (!['jade', 'cool'].includes(state.palette)) state.palette = 'jade';
  document.documentElement.dataset.palette = state.palette;
  N.setMotion?.(N.MOTION_ALIAS?.[state.motion] || state.motion || '1', true);
  N.refreshScene?.();

  /* 快照到达前只上静态骨架；记录区等真实数据一次画出，避免空态闪一下 */
  N.renderIsland();
  N.renderRequests();
  N.renderQueue();
  N.updateStats();
  N.renderModelLabel();
  N.renderEffortLabel();
  N.syncTargets(true);
  N.updateSend?.();

  const view = ['chat', 'work', 'context', 'skills', 'plugins', 'settings'].includes(state.view) ? state.view : 'chat';
  state.view = view;
  $$('.view').forEach(v => { v.hidden = v.id !== 'view-' + view; });
  $$('.dock-btn').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  N.markEnter?.(view);
  N.renderView(view);
  N.syncDock(true);
  N.refreshIsland();

  function paint() {
    N.renderChat(true);
    N.reqSig?.();
    N.renderRequests();
    N.renderQueue();
    N.updateStats();
    N.renderModelLabel();
    N.renderEffortLabel();
    N.syncTargets(true);
    N.updateSend?.();
    N.refreshIsland();
    N.renderStatus?.();
    N.renderView(state.view);
    requestAnimationFrame(() => {
      const tabs = document.querySelector('.tabs');
      if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x', true);
      const perm = document.querySelector('.perm');
      if (perm) N.moveInk($('.tab-ink', perm), $('button.active', perm), 'x', true);
      if (state.view === 'chat') N.scrollEnd?.(false);
    });
  }

  N.setConnection = ok => {
    document.documentElement.classList.toggle('is-offline', !ok);
    if (!ok && state.connected) N.notify('与服务的连接中断，正在重试…', 'cinnabar');
  };

  async function start() {
    try {
      await N.sync.all();
    } catch (error) {
      state.connectionError = error?.message || '未能读取服务端状态';
    }
    paint();
    if (state.connectionError) N.notify(state.connectionError, 'cinnabar', 4200);
    N.connectStream?.();
  }

  start();

  requestAnimationFrame(() => {
    N.syncDock(true);
    N.syncTargets(true);
    const tabs = document.querySelector('.tabs');
    if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x', true);
  });
  document.fonts?.ready.then(() => {
    N.syncDock(true);
    N.syncTargets(true);
    const tabs = document.querySelector('.tabs');
    if (tabs) N.moveInk($('.tab-ink', tabs), $('button.active', tabs), 'x', true);
  });

  /* 初始场景：光球跟随指针，轻微倾斜 */
  let raf = 0, move = null, tx = 0, ty = 0, x = 0, y = 0;
  function stopOrb() {
    cancelAnimationFrame(raf);
    if (move) removeEventListener('pointermove', move);
    move = null;
  }
  function startOrb() {
    stopOrb();
    const stage = $('#orb-stage');
    if (!stage || N.REDUCED) return;
    move = e => {
      const r = stage.getBoundingClientRect();
      tx = Math.max(-18, Math.min(18, (e.clientX - (r.left + r.width / 2)) * 0.035));
      ty = Math.max(-14, Math.min(14, (e.clientY - (r.top + r.height / 2)) * 0.035));
    };
    addEventListener('pointermove', move, { passive: true });
    const loop = () => {
      x += (tx - x) * 0.06; y += (ty - y) * 0.06;
      const sq = Math.min(0.06, Math.abs(tx - x) * 0.02);
      stage.style.transform = `translate(${x.toFixed(2)}px,${y.toFixed(2)}px) rotate(${(x * 0.3).toFixed(2)}deg) scale(${1 + sq},${1 - sq})`;
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
  }
  function syncOrb() {
    if (state.view === 'chat' && !state.items.length && !state.replying) startOrb();
    else stopOrb();
  }
  syncOrb();
  new MutationObserver(syncOrb).observe($('#thread'), { childList: true });
  $$('.dock-btn').forEach(b => b.addEventListener('click', () => setTimeout(syncOrb, 400)));
})(window.N);
