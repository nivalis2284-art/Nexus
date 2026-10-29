/* 归档、恢复、删除的真实链路 */
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
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-archive-'));
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
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

    const ev = expr => page.evaluate(expr);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const shot = async name => {
      const res = await page.call('Page.captureScreenshot', { format: 'png' });
      fs.writeFileSync(path.join(ART, name + '.png'), Buffer.from(res.data, 'base64'));
    };

    await page.call('Page.navigate', { url: TARGET_URL });
    await until(() => ev('Boolean(window.N && N.state.sessionId)'), 'bootstrap failed', 20000);
    await ev('document.querySelector(".dock-btn[data-view=\\"work\\"]").click()');
    await wait(1200);
    await shot('50-groups');

    const before = await ev('JSON.stringify({ended: document.querySelectorAll(\'[data-g="ended"] .work-item\').length, archived: document.querySelectorAll(\'[data-g="archived"] .work-item\').length})');
    console.log('before:', before);

    const archivedId = await ev(`(() => {
      const btn = document.querySelector('#view-work [data-act="work-archive"]');
      if (!btn) return '';
      const id = btn.dataset.id;
      btn.click();
      return id;
    })()`);
    if (!archivedId) problems.push('no archive button');
    await wait(1600);
    const afterArchive = await ev(`JSON.stringify({
      id: ${JSON.stringify(archivedId)},
      ended: document.querySelectorAll('[data-g="ended"] .work-item').length,
      archivedSection: Boolean(document.querySelector('[data-g="archived"]')),
      archivedHidden: document.querySelector('[data-g="archived"] .work-group-body')?.hidden,
      inState: N.state.works.find(w => w.id === ${JSON.stringify(archivedId)})?.archived,
    })`);
    console.log('after archive:', afterArchive);
    const parsed = JSON.parse(afterArchive);
    if (!parsed.archivedSection) problems.push('archived section missing');
    if (parsed.inState !== true) problems.push('archived flag not set in state');
    if (parsed.archivedHidden !== true) problems.push('archived group should start collapsed');
    await shot('51-archived-collapsed');

    await ev('document.querySelector(\'[data-g="archived"] [data-act="group-toggle"]\')?.click()');
    await wait(900);
    await shot('52-archived-open');

    await page.call('Page.reload');
    await until(() => ev('Boolean(window.N && N.state.sessionId)'), 'reload failed', 20000);
    await ev('document.querySelector(".dock-btn[data-view=\\"work\\"]").click()');
    await wait(1200);
    const afterReload = await ev(`JSON.stringify({
      open: document.querySelector('[data-g="archived"] .work-group-h')?.getAttribute('aria-expanded'),
      hidden: document.querySelector('[data-g="archived"] .work-group-body')?.hidden,
      pref: N.state.workGroups?.archived,
    })`);
    console.log('after reload:', afterReload);
    if (JSON.parse(afterReload).hidden !== false) problems.push('group open state not persisted');
    await shot('52b-archived-reload');

    await ev('document.querySelector(\'[data-act="work-unarchive"]\')?.click()');
    await wait(1600);
    const afterRestore = await ev(`JSON.stringify({
      archived: document.querySelectorAll('[data-g="archived"] .work-item').length,
      ended: document.querySelectorAll('[data-g="ended"] .work-item').length,
      inState: N.state.works.find(w => w.id === ${JSON.stringify(archivedId)})?.archived,
    })`);
    console.log('after restore:', afterRestore);
    if (JSON.parse(afterRestore).inState !== false) problems.push('restore did not clear archived');
    await shot('53-restored');

    await ev('document.querySelector(\'[data-act="work-archive"]\')?.click()');
    await wait(1500);
    await ev('document.querySelector(\'[data-g="archived"] [data-act="group-toggle"]\')?.click()');
    await wait(800);
    await ev('document.querySelector(\'[data-act="work-delete"]\')?.click()');
    await wait(700);
    await ev('document.querySelector("#editor-save")?.click()');
    await wait(1800);
    const afterDelete = await ev(`JSON.stringify({
      exists: N.state.works.some(w => w.id === ${JSON.stringify(archivedId)}),
      archived: document.querySelectorAll('[data-g="archived"] .work-item').length,
      rows: document.querySelectorAll('#view-work .work-item').length,
    })`);
    console.log('after delete:', afterDelete);
    if (JSON.parse(afterDelete).exists) problems.push('task still in state after delete');
    await shot('54-deleted');

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
    console.log('OK: archive flow passed');
  }
}

main().catch(e => { console.error(e); process.exitCode = 1; });
