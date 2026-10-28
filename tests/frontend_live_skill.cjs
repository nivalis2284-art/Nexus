/* 技能新建与删除的真实链路 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect } = require('./browser_smoke.cjs');

const TARGET_URL = process.env.NEXUS_URL || 'http://127.0.0.1:8766/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const ART = path.resolve(__dirname, '../.artifacts/nexus-live');
const NAME = 'nexus-smoke-skill';

async function main() {
  fs.mkdirSync(ART, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-skill-'));
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
    await ev('document.querySelector(".dock-btn[data-view=\\"skills\\"]").click()');
    await wait(900);

    await ev('document.querySelector("[data-act=\\"new-skill\\"]")?.click()');
    await wait(700);
    await ev(`(() => {
      document.querySelector('#skill-name').value = ${JSON.stringify(NAME)};
      document.querySelector('#skill-description').value = '冒烟测试创建的临时技能';
      document.querySelector('#skill-body').value = '# 临时技能\\n\\n仅用于验证保存链路。';
      document.querySelector('#skill-source').value = 'personal';
      document.querySelector('#skill-form').requestSubmit();
    })()`);
    await wait(1600);
    const created = await ev(`N.state.skills.some(s => s.name === ${JSON.stringify(NAME)})`);
    if (!created) problems.push('skill not created');
    await shot('40-skill-created');

    await ev('document.querySelector(".dock-btn[data-view=\\"skills\\"]").click()');
    await wait(500);
    await shot('41-skill-list');

    await ev(`(() => {
      const list = [...document.querySelectorAll('#view-skills .skill')];
      const card = list.find(c => c.querySelector('h3')?.textContent.trim() === ${JSON.stringify(NAME)});
      card?.querySelector('[data-act="del-skill"]')?.click();
    })()`);
    await wait(700);
    await ev('document.querySelector("#editor-form .btn.solid")?.click()');
    await wait(1500);
    const removed = await ev(`!N.state.skills.some(s => s.name === ${JSON.stringify(NAME)})`);
    if (!removed) problems.push('skill not removed');
    await shot('42-skill-removed');

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
    console.log('OK: skill flow passed');
  }
}

main().catch(e => { console.error(e); process.exitCode = 1; });
