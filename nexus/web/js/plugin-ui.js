/* Opt-in styles for same-origin Nexus plugin pages. No host DOM is changed. */
'use strict';

(() => {
  const root = document.documentElement;
  const tokens = [
    'paper', 'paper-2', 'card', 'card-glass', 'raised', 'ink', 'ink-2', 'ink-3', 'ink-4',
    'line', 'line-2', 'ink-glass', 'jade', 'jade-hi', 'jade-deep', 'jade-ink', 'jade-soft',
    'cinnabar', 'cinnabar-deep', 'cinnabar-soft', 'on-primary', 'on-accent', 'danger',
    'font', 'mono', 'sh-1', 'sh-2', 'sh-3', 'motion', 'ease-out', 'ease-in',
    'd-0', 'd-1', 'd-2', 'd-3', 'd-4',
    'sp-bouncy', 'sp-bouncy-d', 'sp-snappy', 'sp-snappy-d', 'sp-soft', 'sp-soft-d',
    'sp-settle', 'sp-settle-d',
  ];
  const embedded = parent !== window && parent.location.origin === location.origin;
  let observer;
  function sync() {
    if (!embedded) return;
    const host = parent.document.documentElement;
    const style = parent.getComputedStyle(host);
    for (const key of ['theme', 'palette', 'motion']) root.dataset[key] = host.dataset[key] || '';
    for (const name of tokens) {
      const value = style.getPropertyValue('--' + name).trim();
      if (value) root.style.setProperty('--' + name, value);
    }
    root.style.colorScheme = style.colorScheme;
  }
  function enter() {
    sync();
    const host = embedded ? parent.N : null;
    if (host && !host.REDUCED) host.cascade(document.querySelector('main') || document.body);
  }
  if (embedded) {
    sync();
    observer = new MutationObserver(sync);
    observer.observe(parent.document.documentElement, {
      attributes: true, attributeFilter: ['style', 'data-theme', 'data-palette', 'data-motion'],
    });
    addEventListener('pagehide', () => observer.disconnect(), {once: true});
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', enter, {once: true});
  else enter();
})();
