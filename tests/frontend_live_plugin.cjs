/* 插件加载：命令返回后状态仍是 loading，界面要自己盯到 ready */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect } = require('./browser_smoke.cjs');

const TARGET_URL = process.env.NEXUS_URL || 'http://127.0.0.1:8766/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const PLUGIN = process.env.NEXUS_PLUGIN || 'web-browser';

async function main() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-plugin-'));
  let output = '';
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--remote-debugging-port=0',
    '--user-data-dir=' + path.join(profile, 'p'), '--no-first-run', '--hide-scrollbars', 'about:blank'],
    { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', b => { output += b; });
  let page;
  const problems = [];
  try {
    const ws = await until(() => output.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1], 'chrome did not start');
    const browser = await connect(ws);
    const target = await browser.call('Target.createTarget', { url: 'about:blank' });
    const endpoint = new URL(ws); endpoint.protocol = 'http:'; endpoint.pathname = '/json/list';
    const targets = await (await fetch(endpoint)).json();
    page = await connect(targets.find(t => t.id === target.targetId).webSocketDebuggerUrl);
    await page.call('Runtime.enable');
    await page.call('Page.enable');
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

    const ev = expr => page.evaluate(expr);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const state = () => ev(`(() => {
      const p = (N.state.plugins || []).find(x => x.id === ${JSON.stringify(PLUGIN)});
      const label = document.querySelector('.plug-detail .pstate')?.textContent || '';
      return JSON.stringify({ status: p && p.status, enabled: p && p.enabled, label });
    })()`);

    await page.call('Page.navigate', { url: TARGET_URL });
    await until(() => ev('Boolean(window.N && N.state.sessionId)'), 'bootstrap failed', 20000);
    await ev('document.querySelector(".dock-btn[data-view=\\"plugins\\"]").click()');
    await wait(900);
    await ev(`document.querySelector('.plug-item[data-id="${PLUGIN}"]')?.click()`);
    await wait(700);
    console.log('start:', await state());

    // 先停用，回到 unloaded
    const beforeUnload = JSON.parse(await state());
    if (beforeUnload.enabled) {
      await ev('document.querySelector(\'[data-act="plug-op"][data-op="unload"]\')?.click()');
      await until(() => ev(`(() => {
        const p = (N.state.plugins || []).find(x => x.id === ${JSON.stringify(PLUGIN)});
        return Boolean(p && p.status === 'unloaded');
      })()`), 'unload did not settle', 40000);
      await wait(400);
      console.log('after unload:', await state());
    }

    // 点加载，然后连续采样，看界面是否从"加载中"走到"已启用"
    await ev('document.querySelector(\'[data-act="plug-op"][data-op="load"]\')?.click()');
    const seen = [];
    let finalLabel = '';
    for (let i = 0; i < 60; i++) {
      await wait(300);
      const raw = await state();
      const now = JSON.parse(raw);
      const line = `${now.status}/${now.label}`;
      if (seen[seen.length - 1] !== line) seen.push(line);
      finalLabel = now.label;
      if (now.status === 'ready' && now.label.includes('已启用')) break;
    }
    console.log('transition:', seen.join(' -> '));

    const last = JSON.parse(await state());
    if (last.status !== 'ready') problems.push('plugin never reached ready: ' + JSON.stringify(last));
    if (!last.label.includes('已启用')) problems.push('card still shows: ' + last.label);
    if (!seen.some(s => s.includes('加载中'))) problems.push('loading state never rendered');

    if (page.errors.length) problems.push('exceptions: ' + JSON.stringify(page.errors).slice(0, 600));
  } finally {
    try { page?.close?.(); } catch (_) {}
    try { chrome.kill(); } catch (_) {}
  }
  if (problems.length) {
    console.log('PROBLEMS:');
    for (const p of problems) console.log(' -', p);
    process.exitCode = 1;
  } else {
    console.log('OK: plugin load settles');
  }
}

main().catch(e => { console.error(e); process.exitCode = 1; });
