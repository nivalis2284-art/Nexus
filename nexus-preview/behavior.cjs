/* 交互行为回归：发送、停止、审批、附件、插件选择、弹窗 */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const { until, connect, killOwned } = require('../tests/browser_smoke.cjs');
const CHROME = process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-behavior-'));
  let out = '';
  const chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--remote-debugging-port=0', '--user-data-dir=' + path.join(profile, 'p'), '--no-first-run', '--hide-scrollbars', 'about:blank'], { windowsHide: true, stdio: ['ignore', 'ignore', 'pipe'] });
  chrome.stderr.on('data', b => out += b);
  let page;
  const problems = [];
  try {
    const ws = await until(() => out.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1], 'no chrome');
    const browser = await connect(ws);
    const target = await browser.call('Target.createTarget', { url: 'about:blank' });
    const ep = new URL(ws); ep.protocol = 'http:'; ep.pathname = '/json/list';
    const targets = await (await fetch(ep)).json();
    page = await connect(targets.find(t => t.id === target.targetId).webSocketDebuggerUrl);
    await page.call('Runtime.enable'); await page.call('Page.enable');
    await page.call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 980, deviceScaleFactor: 1, mobile: false });
    const ev = e => page.evaluate(e);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const click = async s => { await ev(`document.querySelector(${JSON.stringify(s)})?.click()`); await wait(90); };
    await page.call('Page.navigate', { url: 'http://127.0.0.1:8777/' });
    await until(() => ev('Boolean(window.N)'), 'boot');
    await wait(700);

    // 1. 空态发送 + 停止
    await ev(`N.scenario('empty')`); await wait(500);
    await ev(`document.querySelector('#input').value='整理一下插件页的字段'; document.querySelector('#input').dispatchEvent(new Event('input'))`);
    await ev(`document.querySelector('#composer').requestSubmit()`);
    await wait(700);
    if (await ev(`!N.state.replying`)) problems.push('stream did not start');
    await click('#stop-reply');
    await wait(700);
    if (await ev(`N.state.replying`)) problems.push('stop did not settle');
    if (!(await ev(`N.state.items.some(i=>i.role==='notice')`))) problems.push('stop notice missing');
    if (await ev(`document.querySelectorAll('#thread .caret').length`)) problems.push('caret left after stop');

    // 2. 审批
    await ev(`N.scenario('approval')`); await wait(500);
    await click('.req-card [data-act="approve"][data-allow="1"]');
    await wait(2500);
    if (await ev(`N.state.requests.length`)) problems.push('approval not cleared');
    if (!(await ev(`N.state.items.some(i=>i.text&&i.text.includes('允许'))`))) problems.push('approval user message missing');

    // 3. 待核对
    await ev(`N.scenario('unknown')`); await wait(500);
    await click('.req-card [data-act="review-unknown"]');
    await wait(500);
    if (await ev(`N.state.requests.length`)) problems.push('unknown request not cleared');

    // 4. 附件
    await ev(`N.scenario('empty')`); await wait(400);
    await ev(`(() => { const dt = new DataTransfer(); dt.items.add(new File(['hello'], 'note.txt', {type:'text/plain'})); const i = document.querySelector('#file-input'); i.files = dt.files; i.dispatchEvent(new Event('change', {bubbles:true})); return true; })()`);
    await wait(200);
    if (!(await ev(`document.querySelectorAll('#files .file').length`))) problems.push('attachment chip missing');
    await ev(`document.querySelector('#input').value='看这个文件'; document.querySelector('#input').dispatchEvent(new Event('input'))`);
    await ev(`document.querySelector('#composer').requestSubmit()`);
    await wait(400);
    if (!(await ev(`N.state.items.some(i=>i.attachments&&i.attachments.length)`))) problems.push('attachment not attached to message');
    await click('#stop-reply'); await wait(500);

    // 5. 插件选择局部更新
    await ev(`N.go('plugins')`); await wait(800);
    const before = await ev(`document.querySelectorAll('#view-plugins .plug-item').length`);
    await click('#view-plugins .plug-item:nth-child(2)');
    await wait(400);
    const after = await ev(`document.querySelectorAll('#view-plugins .plug-item').length`);
    if (before !== after) problems.push('plugin list rebuilt on selection');
    if (!(await ev(`document.querySelector('#view-plugins .plug-detail h2').textContent.includes('检索增强')`))) problems.push('plugin detail did not switch');

    // 6. 页面切换后返回，入场动画标记复位
    await ev(`N.scenario('conversation')`); await wait(600);
    await ev(`N.go('chat')`); await wait(500);
    await ev(`N.go('work')`); await wait(600);
    if (!(await ev(`document.querySelectorAll('#view-work .work-item').length`))) problems.push('work page empty after re-entry');

    // 7. 消息编辑重发 + 撤销
    await ev(`N.go('chat')`); await wait(500);
    const firstUser = await ev(`N.state.items.find(i=>i.role==='user').key`);
    await ev(`N.$('[data-act="resend"][data-key="${firstUser}"]')?.click()`);
    await wait(400);
    if (!(await ev(`document.querySelector('#editor').open`))) problems.push('resend dialog did not open');
    await ev(`document.querySelector('#editor-text').value='改写后的消息'; document.querySelector('#editor-form').requestSubmit()`);
    await wait(900);
    if (!(await ev(`document.querySelector('.history-undo')`))) problems.push('undo bar missing after resend');
    await click('[data-act="undo-history"]');
    await wait(400);
    if (await ev(`document.querySelector('.history-undo')`)) problems.push('undo bar not removed');

    console.log(problems.length ? 'PROBLEMS:\n' + problems.join('\n') : 'OK: behavior checks passed');
    if (page.errors.length) console.log('runtime errors:\n' + page.errors.map(e => e.exception?.description).join('\n').slice(0, 1200));
  } catch (e) {
    console.error('FAILED', e.message);
    if (page) {
      console.error(await page.evaluate('document.body.innerText.slice(0,600)'));
      console.error(page.errors.map(x => x.exception?.description).join('\n'));
    }
    process.exitCode = 1;
  } finally { killOwned(chrome); }
})();
