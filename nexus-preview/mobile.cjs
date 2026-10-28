/* 移动端溢出与减少动效检查 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect, killOwned } = require('../tests/browser_smoke.cjs');
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-mobile-'));
  let out = '';
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--remote-debugging-port=0', '--user-data-dir=' + path.join(profile, 'p'), '--no-first-run', '--hide-scrollbars', 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', b => out += b);
  let page;
  const problems = [];
  try {
    const ws = await until(() => out.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1], 'no chrome');
    const browser = await connect(ws);
    const target = await browser.call('Target.createTarget', { url: 'about:blank' });
    const ep = new URL(ws); ep.protocol = 'http:'; ep.pathname = '/json/list';
    const targets = await (await fetch(ep)).json();
    page = await connect(targets.find(t => t.id === target.targetId).webSocketDebuggerUrl);
    await page.call('Runtime.enable'); await page.call('Page.enable');
    await page.call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    const ev = e => page.evaluate(e);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    await page.call('Page.navigate', { url: 'http://127.0.0.1:8777/' });
    await until(() => ev('Boolean(window.N)'), 'boot');
    await wait(700);
    for (const v of ['chat', 'work', 'context', 'skills', 'plugins', 'settings']) {
      await ev(`N.go(${JSON.stringify(v)})`);
      await wait(650);
      const info = await ev(`(() => {
        const view = document.querySelector('#view-${v}');
        const inScroller = n => {
          let p = n.parentElement;
          while (p && p !== document.body) {
            const o = getComputedStyle(p).overflowX;
            if (o === 'auto' || o === 'scroll') return true;
            p = p.parentElement;
          }
          return false;
        };
        const over = [...document.querySelectorAll('#view-${v} *')].filter(n => {
          const r = n.getBoundingClientRect();
          return r.width > 0 && r.right > innerWidth + 2 && getComputedStyle(n).position !== 'fixed' && !inScroller(n);
        }).slice(0, 4).map(n => ({ tag: n.tagName, cls: String(n.className).slice(0, 30), right: Math.round(n.getBoundingClientRect().right) }));
        return JSON.stringify({ vw: innerWidth, doc: document.documentElement.scrollWidth, view: view.scrollWidth, over });
      })()`);
      const data = JSON.parse(info);
      if (data.doc > data.vw + 2 || data.view > data.vw + 2) problems.push(`${v}: overflow doc=${data.doc} view=${data.view}`);
      if (data.over.length) problems.push(`${v}: elements past edge ${JSON.stringify(data.over)}`);
    }
    await ev(`N.go('settings')`); await wait(1100);
    { const r = await page.call('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(__dirname, '../.artifacts/nexus-preview/mobile-settings.png'), Buffer.from(r.data, 'base64')); }
    await ev(`N.go('chat')`); await ev(`N.scenario('running')`); await wait(900);
    { const r = await page.call('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(__dirname, '../.artifacts/nexus-preview/mobile-running.png'), Buffer.from(r.data, 'base64')); }

    // 减少动效
    await page.call('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
    await page.call('Page.reload');
    await until(() => ev('Boolean(window.N)'), 'boot');
    await wait(500);
    if (!(await ev(`document.querySelectorAll('#thread .msg, #thread .phase').length`))) problems.push('reduced motion: transcript empty');
    await ev(`N.go('context')`); await wait(300);
    if (!(await ev(`document.querySelectorAll('#view-context .ctx-owner-row').length`))) problems.push('reduced motion: context empty');
    console.log(problems.length ? 'PROBLEMS:\n' + problems.join('\n') : 'OK: mobile + reduced motion checks passed');
    if (page.errors.length) console.log('errors', page.errors.map(e => e.exception?.description).join('\n').slice(0, 800));
  } catch (e) {
    console.error('FAILED', e.message);
    if (page) console.error(page.errors.map(x => x.exception?.description).join('\n'));
    process.exitCode = 1;
  } finally { killOwned(chrome); }
})();
