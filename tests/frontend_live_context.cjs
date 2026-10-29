/* 上下文页：进入时自动刷新，聊完回来数字要跟着变 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect } = require('./browser_smoke.cjs');

const TARGET_URL = process.env.NEXUS_URL || 'http://127.0.0.1:8766/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const TEXT = process.env.NEXUS_PROMPT || '只回复两个字：明白';

async function main() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-ctx-'));
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

    await page.call('Page.addScriptToEvaluateOnNewDocument', {
      source: `(() => {
        window.__ctx = [];
        const orig = window.fetch;
        window.fetch = function (...args) {
          const url = String(args[0]);
          if (url.indexOf('/api/context') === 0) window.__ctx.push(Date.now());
          return orig.apply(this, args);
        };
      })();`,
    });

    const ev = expr => page.evaluate(expr);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const counts = () => ev(`(() => {
      const strong = [...document.querySelectorAll('#view-context .ctx-metrics strong')].map(n => n.textContent.trim());
      const legend = [...document.querySelectorAll('#view-context .ctx-legend b')].map(n => n.textContent.trim());
      return JSON.stringify({ messages: strong[2] || '', dataMsgs: (N.state.contextData?.messages || []).length, legend });
    })()`);

    await page.call('Page.navigate', { url: TARGET_URL });
    await until(() => ev('Boolean(window.N && N.state.sessionId)'), 'bootstrap failed', 20000);
    await wait(700);

    await ev('document.querySelector(".dock-btn[data-view=\\"context\\"]").click()');
    await wait(1600);
    const first = JSON.parse(await counts());
    console.log('first visit:', JSON.stringify(first));
    const requestsOnEnter = await ev('(window.__ctx || []).length');
    if (requestsOnEnter < 1) problems.push('no /api/context request when entering the page');

    // 回主交流发一条，再回来
    await ev('document.querySelector(".dock-btn[data-view=\\"chat\\"]").click()');
    await wait(500);
    await ev(`(() => {
      const el = document.querySelector('#input');
      el.value = ${JSON.stringify(TEXT)};
      el.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('#send').click();
    })()`);
    await until(() => ev('N.state.replying === true'), 'no running state', 25000);
    await until(() => ev('N.state.replying === false'), 'reply did not finish', 120000);
    await wait(700);

    await ev('window.__ctx = []');
    await ev('document.querySelector(".dock-btn[data-view=\\"context\\"]").click()');
    await wait(1800);
    const second = JSON.parse(await counts());
    console.log('after a turn:', JSON.stringify(second));
    const requestsBack = await ev('(window.__ctx || []).length');
    if (requestsBack < 1) problems.push('no refresh when coming back to the page');
    if (second.dataMsgs <= first.dataMsgs) problems.push('message count did not grow: ' + first.dataMsgs + ' -> ' + second.dataMsgs);
    if (second.messages === first.messages) problems.push('label stayed the same: ' + second.messages);
    if (JSON.stringify(second.legend) === JSON.stringify(first.legend)) problems.push('token split did not change: ' + JSON.stringify(second.legend));

    // 手动刷新仍然有效
    await ev('window.__ctx = []');
    await ev('document.querySelector(\'#view-context [data-act="ctx-refresh"]\').click()');
    await wait(1000);
    const manual = await ev('(window.__ctx || []).length');
    if (manual < 1) problems.push('manual refresh did not fire');

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
    console.log('OK: context page refreshes');
  }
}

main().catch(e => { console.error(e); process.exitCode = 1; });
