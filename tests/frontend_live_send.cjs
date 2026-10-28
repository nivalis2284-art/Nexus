/* 真实发送一条短消息，验证流式回应与持久化。 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect } = require('./browser_smoke.cjs');

const TARGET_URL = process.env.NEXUS_URL || 'http://127.0.0.1:8766/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const ART = path.resolve(__dirname, '../.artifacts/nexus-live');
const TEXT = process.env.NEXUS_PROMPT || '只回复四个字：接入成功';

async function main() {
  fs.mkdirSync(ART, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-send-'));
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
    await page.call('Log.enable');
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 980, deviceScaleFactor: 1, mobile: false });

    const ev = expr => page.evaluate(expr);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const shot = async name => {
      const res = await page.call('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(ART, name + '.png'), Buffer.from(res.data, 'base64'));
    };

    await page.call('Page.navigate', { url: TARGET_URL });
    await until(() => ev('Boolean(window.N && N.state.sessionId)'), 'bootstrap failed', 20000);
    await wait(600);

    const before = await ev('N.state.items.length');
    await ev(`(() => {
      const el = document.querySelector('#input');
      el.value = ${JSON.stringify(TEXT)};
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return el.value;
    })()`);
    await wait(200);
    await ev('document.querySelector("#send").click()');
    await wait(1200);
    await shot('10-sending');

    await until(() => ev('N.state.replying === true'), 'no running state observed', 20000);
    console.log('running observed');
    await wait(1500);
    await shot('11-streaming');

    await until(() => ev('N.state.replying === false && N.state.items.length > 0'), 'reply did not finish', 120000);
    await wait(900);
    await shot('12-reply');

    const info = await ev(`JSON.stringify({
      items: N.state.items.length,
      roles: N.state.items.map(i => i.role).slice(-6),
      last: (N.state.items.filter(i => i.role === 'assistant').slice(-1)[0] || {}).text || '',
      usage: N.state.usage,
      turns: N.state.turns,
      statTokens: document.querySelector('#stat-tokens').textContent,
    })`);
    console.log('result:', info.slice(0, 900));
    const parsed = JSON.parse(info);
    if (!parsed.last) problems.push('assistant text empty');
    if (parsed.items <= before) problems.push('no new items');
    if (!parsed.usage || !parsed.usage.has_usage) problems.push('usage not reported');
    if (page.errors.length) problems.push('exceptions: ' + JSON.stringify(page.errors).slice(0, 700));
  } finally {
    try { page?.close?.(); } catch (_) {}
    try { chrome.kill(); } catch (_) {}
  }
  if (problems.length) {
    console.log('PROBLEMS:');
    for (const p of problems) console.log(' -', p);
    process.exitCode = 1;
  } else {
    console.log('OK: send/reply passed');
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
