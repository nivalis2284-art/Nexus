/* 动效检查：弹窗、浮层、折叠都要有可见的进出动画 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect } = require('./browser_smoke.cjs');

const TARGET_URL = process.env.NEXUS_URL || 'http://127.0.0.1:8766/';
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';

async function main() {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-motion-'));
  let output = '';
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--remote-debugging-port=0',
    '--user-data-dir=' + path.join(profile, 'p'), '--no-first-run', '--hide-scrollbars', 'about:blank'],
    { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', b => { output += b; });
  let page;
  const problems = [];
  const note = (name, ok, detail) => {
    console.log((ok ? 'ok   ' : 'FAIL ') + name + (detail ? ' · ' + detail : ''));
    if (!ok) problems.push(name + (detail ? ': ' + detail : ''));
  };
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

    await page.call('Page.navigate', { url: TARGET_URL });
    await until(() => ev('Boolean(window.N && N.state.sessionId)'), 'bootstrap failed', 20000);
    await wait(600);

    // 1. 技能弹窗：打开有动画
    await ev('document.querySelector(".dock-btn[data-view=\\"skills\\"]").click()');
    await wait(700);
    await ev('document.querySelector(\'[data-act="new-skill"]\').click()');
    await wait(70);
    const dlgOpen = await ev(`JSON.stringify({
      open: document.querySelector('#skill-dialog').open,
      anims: document.querySelector('#skill-dialog').getAnimations().length,
      opacity: getComputedStyle(document.querySelector('#skill-dialog')).opacity,
    })`);
    note('dialog opens with motion', JSON.parse(dlgOpen).anims > 0, dlgOpen);

    // 2. 关闭有退场：点击后同一帧里仍然 open，并已挂上退场过渡
    const dlgClosing = await ev(`(() => {
      const d = document.querySelector('#skill-dialog');
      document.querySelector('#skill-cancel').click();
      return JSON.stringify({
        open: d.open,
        transition: d.style.transition,
        cls: d.className,
        anims: d.getAnimations().length,
      });
    })()`);
    const closing = JSON.parse(dlgClosing);
    note('dialog closes with motion',
      closing.open === true && Boolean(closing.transition) && closing.cls.includes('is-closing'), dlgClosing);
    await wait(320);
    const dlgGone = await ev('document.querySelector("#skill-dialog").open');
    note('dialog finally closes', dlgGone === false, String(dlgGone));

    // 3. 用量浮层
    await ev('document.querySelector(".dock-btn[data-view=\\"chat\\"]").click()');
    await wait(500);
    await ev('document.querySelector("#stat-tokens").click()');
    await wait(70);
    const popOpen = await ev(`JSON.stringify({
      hidden: document.querySelector('#usage-panel').hidden,
      anims: document.querySelector('#usage-panel').getAnimations().length,
    })`);
    note('usage pop opens with motion', JSON.parse(popOpen).hidden === false && JSON.parse(popOpen).anims > 0, popOpen);
    const popClose = await ev(`(() => {
      const p = document.querySelector('#usage-panel');
      document.querySelector('#stat-tokens').click();
      return JSON.stringify({ hidden: p.hidden, anims: p.getAnimations().length });
    })()`);
    const pc = JSON.parse(popClose);
    note('usage pop closes with motion', pc.hidden === false && pc.anims > 0, popClose);
    await wait(300);

    // 4. 工作分组折叠
    await ev('document.querySelector(".dock-btn[data-view=\\"work\\"]").click()');
    await wait(900);
    const hasEnded = await ev('Boolean(document.querySelector(\'[data-g="ended"] [data-act="group-toggle"]\'))');
    if (hasEnded) {
      await ev('document.querySelector(\'[data-g="ended"] [data-act="group-toggle"]\').click()');
      await wait(60);
      const fold = await ev(`JSON.stringify({
        anims: document.querySelector('[data-g="ended"] .work-group-body').getAnimations().length,
        expanded: document.querySelector('[data-g="ended"] .work-group-h').getAttribute('aria-expanded'),
      })`);
      const f = JSON.parse(fold);
      note('work group folds with motion', f.anims > 0 && f.expanded === 'false', fold);
      await wait(400);
      await ev('document.querySelector(\'[data-g="ended"] [data-act="group-toggle"]\').click()');
      await wait(500);
    } else {
      note('work group present', false, 'no ended group');
    }

    // 5. 停止按钮与最新按钮的入场由真实回合触发，这里只验证存在性
    const hasStop = await ev('Boolean(document.querySelector("#stop-reply"))');
    note('stop button exists', hasStop);

    // 6. 菜单
    await ev('document.querySelector("#scene-btn").click()');
    await wait(60);
    const menuOpen = await ev(`JSON.stringify({
      hidden: document.querySelector('#menu').hidden,
      anims: document.querySelector('#menu').getAnimations().length,
    })`);
    note('menu opens with motion', JSON.parse(menuOpen).hidden === false && JSON.parse(menuOpen).anims > 0, menuOpen);
    const menuClose = await ev(`(() => {
      const m = document.querySelector('#menu');
      document.body.click();
      return JSON.stringify({ hidden: m.hidden, anims: m.getAnimations().length });
    })()`);
    note('menu closes with motion', JSON.parse(menuClose).hidden === false && JSON.parse(menuClose).anims > 0, menuClose);

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
    console.log('OK: motion checks passed');
  }
}

main().catch(e => { console.error(e); process.exitCode = 1; });
