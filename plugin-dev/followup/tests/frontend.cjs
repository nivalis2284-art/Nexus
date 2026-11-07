// Host renderer with actual followup page descriptions and isolated HTTP fixtures.
const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const test = require('node:test');
const {chromium} = require('playwright');

test('followup host page, forms and drafts fit both palettes and narrow screens', async () => {
  const root = path.resolve(__dirname, '../../..');
  const python = process.env.NEXUS_TEST_PYTHON || path.join(root, '.venv/Scripts/python.exe');
  const views = JSON.parse(execFileSync(python, [path.join(__dirname, 'preview.py')], {cwd:root, encoding:'utf8'}));
  const web = path.join(root, 'nexus/web');
  let actionGate = null;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/api/plugins/followup/pages/main') {
      let raw = ''; for await (const chunk of req) raw += chunk;
      const request = JSON.parse(raw || '{}');
      if (actionGate && request.event === 'action') await actionGate;
      res.writeHead(200, {'Content-Type':'application/json'});
      return res.end(JSON.stringify(views[request.action || 'open'] || views.open));
    }
    const file = path.resolve(web, '.' + (url.pathname === '/' ? '/index.html' : url.pathname));
    if (!file.startsWith(web + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
    const types = {'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css'};
    res.writeHead(200, {'Content-Type':types[path.extname(file)] || 'application/octet-stream'});
    const content = fs.readFileSync(file);
    res.end(file.endsWith('index.html') ? content.toString().replace('<script src="js/boot.js"></script>', '') : content);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless:true});
    const page = await browser.newPage({viewport:{width:1440,height:1000}, reducedMotion:'reduce'});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.evaluate(() => {
      const N = window.N;
      N.state.view = 'plugins';
      N.state.plugins = [{id:'followup',name:'约定与关注',version:'0.1.0',status:'ready',enabled:true,
        pages:[{id:'main',title:'约定与关注',renderer:'host'}]}];
      document.querySelectorAll('.view').forEach(view => { view.hidden = view.id !== 'view-plugins'; });
      N.renderPlugins();
    });
    await page.locator('[data-act="plug-page"][data-page-id="main"]').click();
    const outer = page.locator('#plugin-page-dialog');
    await outer.getByText('关注项目', {exact:true}).waitFor();
    for (const palette of ['jade', 'cool']) {
      await page.evaluate(value => window.N.setPalette(value), palette);
      for (const theme of ['day', 'night']) {
        await page.evaluate(value => window.N.setTheme(value), theme);
        await page.waitForFunction(() => !document.documentElement.classList.contains('is-theme-transitioning'));
        assert.ok(await outer.getByText('关注项目', {exact:true}).isVisible());
        if (process.env.NEXUS_UI_ARTIFACTS) {
          fs.mkdirSync(process.env.NEXUS_UI_ARTIFACTS, {recursive:true});
          await page.screenshot({path:path.join(process.env.NEXUS_UI_ARTIFACTS, `followup-${palette}-${theme}.png`)});
        }
      }
    }
    await page.emulateMedia({reducedMotion:'no-preference'});
    const geometry = () => page.evaluate(() => {
      const dialog = document.querySelector('.hui-dialog[open]') || document.querySelector('#plugin-page-dialog');
      const content = dialog.querySelector('.hui-components');
      const d = dialog.getBoundingClientRect(), c = content.getBoundingClientRect();
      return {top:d.top, height:d.height, contentTop:c.top, scroll:dialog.querySelector('.sheet-body')?.scrollTop || 0};
    });
    const pendingAction = async button => {
      const stable = await geometry();
      let release;
      actionGate = new Promise(resolve => { release = resolve; });
      let pending;
      try {
        await button.click();
        await page.waitForFunction(() => document.querySelector('.hui-page').getAttribute('aria-busy') === 'true');
        pending = await geometry();
        assert.equal(await button.getAttribute('aria-busy'), 'true');
        const otherButton = outer.getByRole('button', {name:'刷新', exact:true});
        assert.equal(await otherButton.evaluate(node => getComputedStyle(node).opacity), '1');
      } finally { release(); actionGate = null; }
      assert.deepEqual(pending, stable, 'pending actions must not move or resize the page or editor');
    };
    await pendingAction(outer.getByRole('button', {name:'新建安排', exact:true}));
    const editor = page.locator('.hui-dialog[open]');
    await editor.waitFor();
    assert.deepEqual(errors, []);
    assert.equal(await editor.count(), 1, await outer.innerText());
    assert.equal(await editor.getByLabel('触发方式').count(), 1);
    await editor.evaluate(async node => { await Promise.all(node.getAnimations({subtree:true}).map(a => a.finished.catch(() => {}))); });
    await editor.getByLabel('触发方式').selectOption({label:'每周'});
    await pendingAction(editor.getByRole('button', {name:'更新触发字段', exact:true}));
    await editor.getByLabel('周一', {exact:true}).waitFor();
    await editor.getByLabel('名称', {exact:true}).fill('保留的草稿');
    await editor.getByRole('button', {name:'保存安排', exact:true}).click();
    await editor.getByText('请填写任务说明', {exact:true}).waitFor();
    assert.equal(await editor.getByLabel('名称', {exact:true}).inputValue(), '保留的草稿');
    await page.setViewportSize({width:390,height:844});
    await page.waitForFunction(() => {
      const d = document.querySelector('.hui-dialog[open]');
      const r = d.getBoundingClientRect();
      return r.left >= 0 && r.right <= innerWidth + 1 && r.top >= 0 && r.bottom <= innerHeight + 1;
    });
    await editor.locator('.sheet-body').evaluate(node => { node.scrollTop = 0; });
    const overflow = await editor.locator('input,select,textarea').evaluateAll(nodes => nodes.filter(node => {
      const r = node.getBoundingClientRect(); return r.right > innerWidth + 1 || r.left < 0;
    }).map(node => node.name));
    assert.deepEqual(overflow, []);
    if (process.env.NEXUS_UI_ARTIFACTS) await page.screenshot({path:path.join(process.env.NEXUS_UI_ARTIFACTS,'followup-editor-mobile.png')});
    const box = await editor.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 391 && box.y >= 0 && box.y + box.height <= 845, JSON.stringify(box));
    await page.keyboard.press('Escape');
    await editor.waitFor({state:'detached'});
    await page.emulateMedia({reducedMotion:'reduce'});
    await pendingAction(outer.getByRole('button', {name:'新建安排', exact:true}));
    await editor.getByLabel('触发方式').waitFor();
    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
});
