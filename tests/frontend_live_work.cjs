/* 真实派发一项后台工作，检查工作列表与详情。 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect } = require('./browser_smoke.cjs');

const TARGET_URL = process.env.NEXUS_URL || 'http://127.0.0.1:8766/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const ART = path.resolve(__dirname, '../.artifacts/nexus-live');
const TEXT = process.env.NEXUS_PROMPT || '请用 work_start 工具派发一个后台任务：统计 nexus/web/js 目录下每个 js 文件的行数。只读，不要在主交流里直接做这件事。';

async function main() {
  fs.mkdirSync(ART, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-work-'));
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
    await wait(500);

    await ev(`(() => {
      const el = document.querySelector('#input');
      el.value = ${JSON.stringify(TEXT)};
      el.dispatchEvent(new Event('input', { bubbles: true }));
    })()`);
    await wait(150);
    await ev('document.querySelector("#send").click()');

    const before = await ev('N.state.works.length');
    await until(() => ev(`N.state.works.length > ${before}`), 'no work created', 150000);
    console.log('work created:', await ev('JSON.stringify(N.state.works.map(w => ({id:w.id,title:w.title,state:w.state})))'));
    await wait(1500);
    await shot('20-work-created');

    await ev('document.querySelector(".dock-btn[data-view=\\"work\\"]").click()');
    await wait(1200);
    await shot('21-work-page');
    const cards = await ev('document.querySelectorAll("#view-work .work-item").length');
    if (!cards) problems.push('work cards missing');

    // 打开灵岛详情
    await ev('document.querySelector("#view-work .work-item")?.click()');
    await wait(1600);
    const mode = await ev('document.querySelector("#island").dataset.mode');
    if (mode !== 'detail') problems.push('island detail not opened: ' + mode);
    const detail = await ev('document.querySelector("#island .isl-title")?.textContent || ""');
    console.log('detail title:', detail);
    if (!detail) problems.push('detail title empty');
    await shot('22-work-detail');

    // 详情滚动到底，确认步骤与记录
    await ev('(() => { const s = document.querySelector("#island .isl-scroll"); if (s) s.scrollTop = s.scrollHeight; })()');
    await wait(700);
    await shot('23-work-detail-bottom');

    await wait(2000);
    console.log('states:', await ev('JSON.stringify(N.state.works.map(w => ({id:w.id,state:w.state,steps:(w.steps||[]).length,records:(w.records||[]).length})))'));

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
    console.log('OK: work flow passed');
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
