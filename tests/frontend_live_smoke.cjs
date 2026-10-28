/* 真实后端下的界面冒烟：只读操作，不发消息。 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect } = require('./browser_smoke.cjs');

const TARGET_URL = process.env.NEXUS_URL || 'http://127.0.0.1:8766/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const ART = path.resolve(__dirname, '../.artifacts/nexus-live');

async function main() {
  fs.mkdirSync(ART, { recursive: true });
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-live-'));
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
    const click = async sel => { await ev(`document.querySelector(${JSON.stringify(sel)})?.click()`); await wait(700); };
    const text = sel => ev(`document.querySelector(${JSON.stringify(sel)})?.textContent || ''`);

    await page.call('Page.navigate', { url: TARGET_URL });
    try {
      await until(() => ev('Boolean(window.N && window.N.state && window.N.state.sessionId)'), 'bootstrap failed', 20000);
    } catch (error) {
      console.log('DEBUG readyState:', await ev('document.readyState'));
      console.log('DEBUG typeof N:', await ev('typeof window.N'));
      console.log('DEBUG N keys:', await ev('window.N ? Object.keys(window.N).slice(0, 20).join(",") : "-"'));
      console.log('DEBUG state:', await ev('window.N && window.N.state ? JSON.stringify({session:N.state.sessionId, err:N.state.connectionError, view:N.state.view}) : "-"'));
      console.log('DEBUG errors:', JSON.stringify(page.errors).slice(0, 1500));
      throw error;
    }
    await wait(900);
    if (page.errors.length) problems.push('exceptions: ' + JSON.stringify(page.errors).slice(0, 900));

    const snapshot = await ev(`JSON.stringify({
      session: N.state.sessionId,
      providers: N.state.providers.length,
      works: N.state.works.length,
      items: N.state.items.length,
      roles: (N.state.contextData.roles||[]).length,
      sections: (N.state.contextData.sections||[]).length,
      plugins: N.state.plugins.length,
      skills: N.state.skills.length,
      mcp: N.state.mcp.length,
      scene: document.querySelector('#scene-label')?.textContent,
      connected: N.state.connected,
      err: N.state.connectionError,
    })`);
    console.log('state:', snapshot);
    const info = JSON.parse(snapshot);
    if (!info.session) problems.push('session id missing');
    if (!info.providers) problems.push('no providers loaded');
    if (!info.roles) problems.push('no context roles');
    if (info.err) problems.push('connection error: ' + info.err);

    if (!(await ev('document.querySelector("#view-chat").classList.contains("is-empty")')) && info.items === 0) {
      problems.push('chat empty state not applied');
    }
    if (!(await text('#stat-turn')).includes('轮')) problems.push('turn stat missing');
    await shot('01-chat');

    for (const [name, ready] of [
      ['work', 'document.querySelector("#view-work .page")'],
      ['context', 'document.querySelector("#view-context .ctx-page")'],
      ['skills', 'document.querySelector("#view-skills .page")'],
      ['plugins', 'document.querySelector("#view-plugins .plugins-page")'],
      ['settings', 'document.querySelector("#view-settings .set-page")'],
    ]) {
      await click(`.dock-btn[data-view="${name}"]`);
      await wait(900);
      if (!(await ev(`Boolean(${ready})`))) problems.push(name + ': page not rendered');
      await shot('0' + (['work', 'context', 'skills', 'plugins', 'settings'].indexOf(name) + 2) + '-' + name);
    }

    // 上下文页签切换
    await click('.dock-btn[data-view="context"]');
    await wait(500);
    await click('#view-context [data-act="ctx-tab"][data-k="history"]');
    await wait(600);
    if (!(await ev('Boolean(document.querySelector("#view-context [data-panel=history]"))'))) problems.push('context history tab failed');
    await shot('07-context-history');

    // 设置页：主题与动效切换
    await click('.dock-btn[data-view="settings"]');
    await wait(500);
    await click('#view-settings [data-act="palette"][data-id="cool"]');
    await wait(700);
    const palette = await ev('document.documentElement.dataset.palette');
    if (palette !== 'cool') problems.push('palette switch failed: ' + palette);
    await shot('08-settings-cool');
    await click('#view-settings [data-act="palette"][data-id="jade"]');
    await click('#theme-btn');
    await wait(900);
    const night = await ev('document.documentElement.dataset.theme');
    if (night !== 'night') problems.push('night theme failed: ' + night);
    await shot('08b-settings-night');
    await click('#theme-btn');
    await wait(700);

    // 工作详情（灵岛）
    await click('.dock-btn[data-view="work"]');
    await wait(700);
    const hasWork = await ev('Boolean(document.querySelector("#view-work .work-item"))');
    if (hasWork) {
      await ev('(() => { const list = document.querySelectorAll("#view-work .work-item"); list[list.length - 1]?.click(); })()');
      await wait(900);
      await wait(1400);
      const detailTitle = await ev('document.querySelector("#island .isl-title")?.textContent || ""');
      console.log('detail title:', detailTitle);
      if (!detailTitle) problems.push('work detail empty');
      await shot('09-work-detail');
      await ev('(() => { const s = document.querySelector("#island .isl-scroll"); if (s) s.scrollTop = s.scrollHeight; })()');
      await wait(600);
      await shot('10-work-detail-bottom');
      await click('#island [data-isl="compact"]');
      await wait(600);
    }
    await click('.dock-btn[data-view="chat"]');
    await wait(600);
    await click('#island .isl-main');
    await wait(700);
    const islandMode = await ev('document.querySelector("#island").dataset.mode');
    console.log('island mode:', islandMode);
    await shot('11-island');

    if (page.errors.length > 2) problems.push('exceptions after nav: ' + JSON.stringify(page.errors.slice(-3)).slice(0, 900));
  } finally {
    try { page?.close?.(); } catch (_) {}
    try { chrome.kill(); } catch (_) {}
  }
  if (problems.length) {
    console.log('PROBLEMS:');
    for (const p of problems) console.log(' -', p);
    process.exitCode = 1;
  } else {
    console.log('OK: live smoke passed');
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
