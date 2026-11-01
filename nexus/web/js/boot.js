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

  document.documentElement.dataset.theme = state.theme === 'day' ? 'day' : 'night';
  if (state.palette === 'blue') state.palette = 'cool';
  else if (!['jade', 'cool'].includes(state.palette)) state.palette = 'cool';
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
  N.syncDock(true);
  N.refreshIsland();

  function paint() {
    if (state.view === 'chat') N.takeEnter?.('chat');
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
      if (perm) N.syncSettingsUi?.(true);
      if (state.view === 'chat') N.scrollEnd?.(false);
      if (state.view === 'chat') N.enterChat?.();
      else N.stagger?.($('#view-' + state.view));
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

  N.syncPivot?.();
})(window.N);
