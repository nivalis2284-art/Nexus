/* 主交流：发送不重排、思考默认折叠且流式不重建 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect } = require('./browser_smoke.cjs');

const TARGET_URL = process.env.NEXUS_URL || 'http://127.0.0.1:8766/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const TEXT = process.env.NEXUS_PROMPT || '只回复两个字：收到';
const ART = path.resolve(__dirname, '../.artifacts/nexus-live');

async function main() {
  fs.mkdirSync(ART, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-chat-'));
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
    const shot = async name => {
      const res = await page.call('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(ART, name + '.png'), Buffer.from(res.data, 'base64'));
    };

    await page.call('Page.navigate', { url: TARGET_URL });
    await until(() => ev('Boolean(window.N && N.state.sessionId)'), 'bootstrap failed', 20000);
    await wait(800);

    // 监听消息节点被移除的次数：发送不应该把整段对话拆掉重画
    await ev(`(() => {
      window.__probe = { removedMsgs: 0, removedPhase: 0, addedMsgs: 0 };
      const thread = document.querySelector('#thread');
      window.__obs = new MutationObserver(list => {
        for (const m of list) {
          for (const n of m.removedNodes) {
            if (n.nodeType !== 1 || !n.dataset || !n.dataset.key) continue;
            if (n.dataset.key.startsWith('m:')) window.__probe.removedMsgs++;
            if (n.classList && n.classList.contains('phase')) window.__probe.removedPhase++;
          }
          for (const n of m.addedNodes) {
            if (n.nodeType === 1 && n.dataset && n.dataset.key && n.dataset.key.startsWith('m:')) window.__probe.addedMsgs++;
          }
        }
      });
      window.__obs.observe(thread, { childList: true });
      return true;
    })()`);

    const before = await ev(`(() => {
      const nodes = [...document.querySelectorAll('#thread > [data-key]')];
      const last = nodes[nodes.length - 1];
      if (last) last.__keep = true;
      window.__kept = last || null;
      return nodes.length;
    })()`);

    await ev(`(() => {
      const el = document.querySelector('#input');
      el.value = ${JSON.stringify(TEXT)};
      el.dispatchEvent(new Event('input', { bubbles: true }));
      document.querySelector('#send').click();
    })()`);

    await wait(500);
    const afterSend = await ev(`JSON.stringify({
      probe: window.__probe,
      keptAlive: Boolean(window.__kept && window.__kept.isConnected),
    })`);
    console.log('right after send:', afterSend);

    await until(() => ev('N.state.replying === true'), 'no running state', 25000);

    // 流式期间盯住思考节点：它应该一直是同一个 DOM 节点
    await ev(`(() => {
      window.__think = document.querySelector('.phase .step[data-key^="t:"]');
      window.__thinkSeen = Boolean(window.__think);
      window.__thinkOpen = document.querySelector('.phase')?.open;
      return true;
    })()`);

    await wait(1400);
    const streaming = await ev(`JSON.stringify({
      seen: window.__thinkSeen,
      alive: Boolean(window.__think && window.__think.isConnected),
      same: document.querySelector('.phase .step[data-key^="t:"]') === window.__think,
      phaseOpen: document.querySelector('.phase')?.open,
      probe: window.__probe,
    })`);
    console.log('while streaming:', streaming);
    await shot('60-streaming');

    await until(() => ev('N.state.replying === false'), 'reply did not finish', 120000);
    await wait(900);
    await shot('61-done');

    const done = await ev(`JSON.stringify({
      probe: window.__probe,
      keptAlive: Boolean(window.__kept && window.__kept.isConnected),
      phaseOpen: document.querySelector('.phase')?.open,
      before: ${before},
      after: document.querySelectorAll('#thread > [data-key]').length,
    })`);
    console.log('after reply:', done);

    const parsed = JSON.parse(done);
    const stream = JSON.parse(streaming);
    if (stream.seen && !stream.same) problems.push('thinking node was rebuilt while streaming');
    if (!JSON.parse(afterSend).keptAlive) problems.push('previous messages were torn down on send');
    if (!parsed.keptAlive) problems.push('previous messages were torn down after reply');
    if (stream.phaseOpen === true) problems.push('phase expanded during streaming');
    if (parsed.phaseOpen === true) problems.push('phase stays expanded after reply');
    // 发送只应新增消息，不应把已有消息整段拆掉
    if (parsed.probe.removedMsgs > 4) problems.push('too many nodes removed: ' + parsed.probe.removedMsgs);
    if (parsed.after < parsed.before + 2) problems.push('reply not appended');

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
    console.log('OK: chat motion passed');
  }
}

main().catch(e => { console.error(e); process.exitCode = 1; });
