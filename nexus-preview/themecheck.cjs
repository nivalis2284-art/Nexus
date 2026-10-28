const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect, killOwned } = require('../tests/browser_smoke.cjs');
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const ART = path.resolve(__dirname, '../.artifacts/nexus-preview');
(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-theme-'));
  let out = '';
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--remote-debugging-port=0', '--user-data-dir=' + path.join(profile, 'p'), '--no-first-run', '--hide-scrollbars', 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', b => out += b);
  let page;
  const problems = [];
  try {
    const ws = await until(() => {
      const line = out.split(String.fromCharCode(10)).find(l => l.includes('DevTools listening on '));
      return line ? line.slice(line.indexOf('ws:')).trim() : '';
    }, 'no chrome');
    const browser = await connect(ws);
    const target = await browser.call('Target.createTarget', { url: 'about:blank' });
    const ep = new URL(ws); ep.protocol = 'http:'; ep.pathname = '/json/list';
    const targets = await (await fetch(ep)).json();
    page = await connect(targets.find(t => t.id === target.targetId).webSocketDebuggerUrl);
    await page.call('Runtime.enable'); await page.call('Page.enable');
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 980, deviceScaleFactor: 1, mobile: false });
    const ev = e => page.evaluate(e);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const shot = async n => { const r = await page.call('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(ART, n + '.png'), Buffer.from(r.data, 'base64')); };
    const click = async s => { await ev(`document.querySelector(${JSON.stringify(s)})?.click()`); await wait(100); };
    await page.call('Page.navigate', { url: 'http://127.0.0.1:8777/' });
    await until(() => ev('Boolean(window.N)'), 'boot');
    await wait(800);
    await ev(`N.go('settings')`); await wait(1200);
    await shot('theme-settings-day');

    // 主题切换
    await click('[data-act="palette"][data-id="cool"]');
    await wait(1100);
    const cool = await ev(`JSON.stringify({ palette: document.documentElement.dataset.palette, jade: getComputedStyle(document.documentElement).getPropertyValue('--jade').trim(), accent: getComputedStyle(document.documentElement).getPropertyValue('--cinnabar').trim(), paper: getComputedStyle(document.documentElement).getPropertyValue('--paper').trim(), active: document.querySelector('.theme-card.is-active')?.dataset.id })`);
    if (JSON.parse(cool).palette !== 'cool') problems.push('palette not applied');
    await shot('theme-cool-day');

    // 深色
    await click('[data-act="mode"][data-v="night"]');
    await wait(1100);
    const night = await ev(`JSON.stringify({ theme: document.documentElement.dataset.theme, jade: getComputedStyle(document.documentElement).getPropertyValue('--jade').trim(), onPrimary: getComputedStyle(document.documentElement).getPropertyValue('--on-primary').trim() })`);
    if (JSON.parse(night).theme !== 'night') problems.push('night not applied');
    await shot('theme-cool-night');

    // 快
    await click('[data-act="motion"][data-v="fast"]');
    await wait(300);
    const fast = await ev(`JSON.stringify({ motion: document.documentElement.dataset.motion, css: getComputedStyle(document.documentElement).getPropertyValue('--motion').trim(), spring: getComputedStyle(document.documentElement).getPropertyValue('--sp-soft-d').trim() })`);
    if (JSON.parse(fast).motion !== 'fast') problems.push('motion fast not applied');
    await shot('theme-settings-fast');

    // 慢
    await click('[data-act="motion"][data-v="slow"]');
    await wait(300);
    const slow = await ev(`JSON.stringify({ motion: document.documentElement.dataset.motion, css: getComputedStyle(document.documentElement).getPropertyValue('--motion').trim(), spring: getComputedStyle(document.documentElement).getPropertyValue('--sp-soft-d').trim() })`);
    await shot('theme-settings-slow');

    // 动效倍率实测：同一页面切换的时长与错峰延迟
    await ev(`N.setMotion('fast', true); N.go('chat')`); await wait(500);
    await ev(`N.go('work')`); await wait(280);
    const fastAnim = await ev(`(() => { const v = document.querySelector('#view-work'); const a = v.getAnimations()[0]; const s = v.querySelector('[data-s]')?.getAnimations()[0]; return JSON.stringify({ dur: a?.effect.getTiming().duration, delay: s?.effect.getTiming().delay }); })()`);
    await ev(`N.setMotion('slow', true); N.go('chat')`); await wait(900);
    await ev(`N.go('work')`); await wait(460);
    const slowAnim = await ev(`(() => { const v = document.querySelector('#view-work'); const a = v.getAnimations()[0]; const s = v.querySelector('[data-s]')?.getAnimations()[0]; return JSON.stringify({ dur: a?.effect.getTiming().duration, delay: s?.effect.getTiming().delay }); })()`);
    if (!(JSON.parse(fastAnim).dur < JSON.parse(slowAnim).dur)) problems.push('motion speed did not scale page enter: ' + fastAnim + ' / ' + slowAnim);

    // 持久化
    await page.call('Page.reload');
    await until(() => ev('Boolean(window.N)'), 'boot');
    await wait(900);
    const saved = await ev(`JSON.stringify({ palette: document.documentElement.dataset.palette, theme: document.documentElement.dataset.theme, motion: document.documentElement.dataset.motion })`);
    if (JSON.parse(saved).palette !== 'cool' || JSON.parse(saved).theme !== 'night' || JSON.parse(saved).motion !== 'slow') problems.push('persistence failed: ' + saved);
    await shot('theme-reload');

    // 回到浅色 + 默认，检查每个主题无异常
    await ev(`N.applyTheme('day'); N.applyPalette('jade'); N.setMotion('normal', true)`); await wait(600);
    for (const id of ['jade','cool']) {
      await ev(`N.applyPalette(${JSON.stringify(id)})`); await wait(250);
      const info = await ev(`JSON.stringify({ p: getComputedStyle(document.documentElement).getPropertyValue('--jade').trim(), a: getComputedStyle(document.documentElement).getPropertyValue('--cinnabar').trim() })`);
      if (!JSON.parse(info).p) problems.push('palette missing ' + id);
    }
    console.log(JSON.stringify({ cool: JSON.parse(cool), night: JSON.parse(night), fast: JSON.parse(fast), slow: JSON.parse(slow), fastAnim: JSON.parse(fastAnim), slowAnim: JSON.parse(slowAnim), saved: JSON.parse(saved) }, null, 1));
    console.log(problems.length ? 'PROBLEMS:/n' + problems.join('\n') : 'OK: theme checks passed');
    if (page.errors.length) console.log('errors', page.errors.map(e => e.exception?.description).join('\n').slice(0, 1000));
  } catch (e) {
    console.error('FAILED', e.message);
    if (page) console.error(await page.evaluate('document.body.innerText.slice(0,500)'));
  } finally { killOwned(chrome); }
})();
