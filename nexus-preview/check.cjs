/* 本地预览的关键流程检查：无后端、无模型。 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect, killOwned } = require('../tests/browser_smoke.cjs');

const TARGET_URL = process.env.PREVIEW_URL || 'http://127.0.0.1:8777/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const ART = path.resolve(__dirname, '../.artifacts/nexus-preview');

async function main() {
  fs.mkdirSync(ART, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-preview-'));
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
    const shot = async (name, full = false) => {
      const res = await page.call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: full });
      fs.writeFileSync(path.join(ART, name + '.png'), Buffer.from(res.data, 'base64'));
    };
    const click = async sel => { await ev(`document.querySelector(${JSON.stringify(sel)})?.click()`); await wait(80); };
    const text = sel => ev(`document.querySelector(${JSON.stringify(sel)})?.textContent || ''`);

    await page.call('Page.navigate', { url: TARGET_URL });
    await until(() => ev('Boolean(window.N && document.querySelector("#thread .msg"))'), 'bootstrap failed', 12000);
    await wait(700);
    if (page.errors.length) problems.push('runtime exceptions: ' + JSON.stringify(page.errors).slice(0, 800));

    // 1. 主交流
    const msgs = await ev('document.querySelectorAll("#thread .msg, #thread .phase").length');
    const slots = await ev('document.querySelectorAll("#island .slot").length');
    if (msgs < 5) problems.push('transcript messages: ' + msgs);
    if (slots !== 3) problems.push('island slots: ' + slots);
    if (!(await text('#stat-turn')).includes('轮')) problems.push('turn stat missing');
    await shot('01-chat');

    // 2. 请求与回答
    const reqBefore = await ev('document.querySelectorAll(".req-card").length');
    if (!reqBefore) problems.push('request rail empty');
    await ev(`document.querySelector('.req-card .req-free').value = '侧栏展开'`);
    await click('.req-card [data-act="answer"]');
    await wait(2600);
    if (await ev('document.querySelectorAll(".req-card").length')) problems.push('request not resolved');
    if (!(await ev('N.state.works.find(w=>w.id==="w-detail").state === "running"'))) problems.push('work not resumed');

    // 3. 工作页与详情
    await click('.dock-btn[data-view="work"]');
    await wait(1100);
    if (!(await ev('document.querySelectorAll("#view-work .work-item").length'))) problems.push('work list empty');
    await shot('02-work');
    await click('#view-work .work-item');
    await wait(900);
    if (await ev('document.querySelector("#island").mode') !== 'detail') problems.push('work detail did not open');
    await shot('03-work-detail');
    await click('#island [data-isl="compact"]');
    await wait(400);

    // 4. 上下文
    await click('.dock-btn[data-view="context"]');
    await wait(1200);
    if (!(await ev('document.querySelectorAll("#view-context .ctx-owner-row").length'))) problems.push('context owners missing');
    await click('#view-context [data-act="ctx-tab"][data-k="history"]');
    await wait(500);
    if (!(await ev('document.querySelectorAll("#view-context .ctx-msg").length'))) problems.push('context messages missing');
    await shot('04-context');

    // 5. 技能
    await click('.dock-btn[data-view="skills"]');
    await wait(1100);
    if (!(await ev('document.querySelectorAll("#view-skills .skill").length'))) problems.push('skills empty');
    await click('#view-skills [data-act="view-skill"]');
    await wait(400);
    if (!(await ev('document.querySelector("dialog[open]#skill-dialog") !== null'))) problems.push('skill dialog did not open');
    await click('#skill-cancel');
    await shot('05-skills');

    // 6. 插件
    await click('.dock-btn[data-view="plugins"]');
    await wait(1200);
    if (!(await ev('document.querySelectorAll("#view-plugins .plug-item").length'))) problems.push('plugins empty');
    await click('#view-plugins .plug-item:nth-child(2)');
    await wait(700);
    if (!(await ev('document.querySelectorAll("#view-plugins .plug-settings .pfield").length'))) problems.push('plugin settings missing');
    await shot('06-plugins');
    await click('#view-plugins [data-act="plug-tab"][data-k="requests"]');
    await wait(500);
    await click('#view-plugins .preq');
    await wait(400);
    if (!(await ev('document.querySelectorAll("#view-plugins .preq-json").length'))) problems.push('request detail missing');
    await shot('07-plugin-requests');

    // 7. 设置与提供商
    await click('.dock-btn[data-view="settings"]');
    await wait(1100);
    await click('[data-act="edit-provider"]');
    await wait(500);
    if (!(await ev('document.querySelectorAll("#p-models-list .model-row").length'))) problems.push('provider models missing');
    await shot('08-settings-provider');
    await click('#provider-dialog [data-close]');
    await wait(300);
    await click('[data-act="add-mcp"]');
    await wait(400);
    await ev('document.querySelector("#mcp-transport").value="http"; document.querySelector("#mcp-transport").dispatchEvent(new Event("change"))');
    if (await ev('document.querySelector("#mcp-http-fields").hidden')) problems.push('mcp http fields not shown');
    await click('#mcp-dialog [data-close]');
    await shot('09-settings');

    // 8. 夜间 + 手机
    await click('#theme-btn');
    await wait(1500);
    if (await ev('document.documentElement.dataset.theme') !== 'night') problems.push('theme not switched');
    await click('.dock-btn[data-view="chat"]');
    await wait(700);
    await shot('10-night');

    await page.call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
    await wait(600);
    await shot('11-mobile');
    await click('.dock-btn[data-view="plugins"]');
    await wait(600);
    await shot('12-mobile-plugins');
    if (await ev('document.documentElement.scrollWidth > window.innerWidth + 1')) problems.push('mobile horizontal overflow');

    console.log(problems.length ? 'PROBLEMS:\n' + problems.join('\n') : 'OK: preview checks passed');
  } catch (error) {
    console.error('FAILED:', error.message);
    if (page) {
      try {
        const res = await page.call('Page.captureScreenshot', { format: 'png' });
        fs.writeFileSync(path.join(ART, 'failure.png'), Buffer.from(res.data, 'base64'));
        console.error(await page.evaluate('document.body.innerText.slice(0,1200)'));
      } catch (_) {}
    }
    process.exitCode = 1;
  } finally {
    killOwned(chrome);
  }
}
main();
