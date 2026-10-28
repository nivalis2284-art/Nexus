/* 动效探针：页面交叉淡入、折叠高度过渡、弹窗关闭 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect, killOwned } = require('../tests/browser_smoke.cjs');
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const ART = path.resolve(__dirname, '../.artifacts/nexus-preview');
(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-motion-'));
  let out = '';
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--remote-debugging-port=0', '--user-data-dir=' + path.join(profile, 'p'), '--no-first-run', '--hide-scrollbars', 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', b => out += b);
  let page;
  try {
    const ws = await until(() => out.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1], 'no chrome');
    const browser = await connect(ws);
    const target = await browser.call('Target.createTarget', { url: 'about:blank' });
    const ep = new URL(ws); ep.protocol = 'http:'; ep.pathname = '/json/list';
    const targets = await (await fetch(ep)).json();
    page = await connect(targets.find(t => t.id === target.targetId).webSocketDebuggerUrl);
    await page.call('Runtime.enable'); await page.call('Page.enable');
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 980, deviceScaleFactor: 1, mobile: false });
    const ev = e => page.evaluate(e);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    await page.call('Page.navigate', { url: 'http://127.0.0.1:8777/' });
    await until(() => ev('Boolean(window.N)'), 'boot');
    await wait(800);

    // 气场换色是否真的在插值
    await ev(`document.body.dataset.mood='calm'`); await wait(300);
    const aura0 = await ev(`getComputedStyle(document.body).getPropertyValue('--aura-a')`);
    await ev(`document.body.dataset.mood='waiting'`);
    const aura1 = await ev(`getComputedStyle(document.body).getPropertyValue('--aura-a')`);
    await wait(500);
    const aura2 = await ev(`getComputedStyle(document.body).getPropertyValue('--aura-a')`);
    await wait(900);
    const aura3 = await ev(`getComputedStyle(document.body).getPropertyValue('--aura-a')`);

    // 灵岛形变：检查残留 ghost
    await ev(`N.morphIsland('overview')`); await wait(900);
    const ov = await ev(`JSON.stringify({ ghosts: document.querySelectorAll('.island-ghost').length, mode: document.querySelector('#island').mode, scrim: !document.querySelector('#island-scrim').hidden })`);
    await ev(`N.morphIsland('detail',{id:N.state.works[0].id})`); await wait(900);
    const det = await ev(`JSON.stringify({ ghosts: document.querySelectorAll('.island-ghost').length, mode: document.querySelector('#island').mode, scrim: !document.querySelector('#island-scrim').hidden, hasCompact: !!document.querySelector('#island [data-isl="compact"]') })`);
    await ev(`N.morphIsland('compact')`); await wait(500);

    // 页面切换：80ms 时两页都在场
    await ev(`N.go('work')`);
    await wait(240);
    const mid = await ev(`(() => {
      const a = document.querySelector('#view-chat'), b = document.querySelector('#view-work');
      return JSON.stringify({ chat: { hidden: a.hidden, opacity: getComputedStyle(a).opacity, z: a.style.zIndex }, work: { hidden: b.hidden, opacity: getComputedStyle(b).opacity, z: b.style.zIndex } });
    })()`);
    const shot = await page.call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(ART, 'motion-page-mid.png'), Buffer.from(shot.data, 'base64'));
    await wait(700);
    const settled = await ev(`JSON.stringify({ chat: document.querySelector('#view-chat').hidden, work: document.querySelector('#view-work').hidden, z: document.querySelector('#view-work').style.zIndex })`);

    // 折叠高度过渡
    await ev(`N.go('chat')`); await wait(600);
    const h = await ev(`(async () => {
      const d = document.querySelector('#thread .phase');
      const before = d.open;
      d.open = false;
      await new Promise(r => setTimeout(r, 400));
      const closed = d.getBoundingClientRect().height;
      d.open = true;
      const h0 = d.getBoundingClientRect().height;
      await new Promise(r => setTimeout(r, 120));
      const h1 = d.getBoundingClientRect().height;
      await new Promise(r => setTimeout(r, 260));
      const h2 = d.getBoundingClientRect().height;
      return JSON.stringify({ before, closed, h0, h1, h2 });
    })()`);

    // 弹窗关闭动画
    await ev(`N.go('settings')`); await wait(700);
    await ev(`document.querySelector('[data-act="add-mcp"]').click()`); await wait(450);
    await ev(`document.querySelector('#mcp-dialog [data-close]').click()`);
    await wait(60);
    const dlgMid = await ev(`(() => { const d = document.querySelector('#mcp-dialog'); return JSON.stringify({ open: d.open, opacity: getComputedStyle(d).opacity }); })()`);
    await wait(300);
    const dlgEnd = await ev(`document.querySelector('#mcp-dialog').open`);

    console.log(JSON.stringify({ aura: [aura0, aura1, aura2, aura3], island: { overview: JSON.parse(ov), detail: JSON.parse(det) }, mid: JSON.parse(mid), settled: JSON.parse(settled), details: JSON.parse(h), dialog: { mid: JSON.parse(dlgMid), end: dlgEnd } }, null, 1));
  } catch (e) {
    console.error('FAILED', e.message);
  } finally { killOwned(chrome); }
})();
