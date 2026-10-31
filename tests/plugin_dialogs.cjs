// Real-browser checks against the shipped UI, with isolated HTTP fixtures.
// Run with Playwright available on NODE_PATH: node --test tests/plugin_dialogs.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const test = require('node:test');
const {chromium} = require('playwright');

test('plugin introductions and both page renderers use disposable dialogs', async () => {
  const root = path.resolve(__dirname, '../nexus/web');
  let readme = '# 插件介绍\n\n使用方法与配置说明。\n\n- 第一项\n- 第二项\n\n| 配置 | 说明 |\n| --- | --- |\n| 地址 | 本机地址 |\n\n```python\nprint("hello")\n```\n\n[文档](docs/help.md)\n\n![图示](docs/example.svg)\n\n<script>window.readmeScriptRan=true</script>';
  let readmeStatus = 200, delay = 0, pageDelay = 0, iframeLoads = 0;
  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url, 'http://localhost');
    const json = (status, value) => { res.writeHead(status, {'Content-Type':'application/json'}); res.end(JSON.stringify(value)); };
    if (url.pathname === '/api/plugins/fixture/readme') {
      const value = readme, status = readmeStatus;
      await new Promise(resolve => setTimeout(resolve, delay));
      return json(status, status === 200 ? {content:value} : {error:'读取错误'});
    }
    if (url.pathname === '/api/plugins/fixture/pages/native') {
      await new Promise(resolve => setTimeout(resolve, pageDelay));
      return json(200, {ui_version:1, components:[
        {id:'draft',type:'field',name:'draft',label:'草稿'},
        {id:'note',type:'text',text:'宿主渲染功能页'},
      ]});
    }
    if (url.pathname === '/plugins/fixture/web/index.html') {
      iframeLoads++;
      res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'});
      return res.end('<label>插件草稿<input id="draft"></label>');
    }
    if (url.pathname === '/plugins/fixture/docs/example.svg') {
      res.writeHead(200, {'Content-Type':'image/svg+xml'});
      return res.end('<svg xmlns="http://www.w3.org/2000/svg" width="240" height="56"><rect width="240" height="56" fill="#e7eaff"/></svg>');
    }
    const file = path.resolve(root, '.' + (url.pathname === '/' ? '/index.html' : url.pathname));
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404); return res.end();
    }
    const types = {'.html':'text/html; charset=utf-8','.css':'text/css','.js':'application/javascript','.svg':'image/svg+xml'};
    res.writeHead(200, {'Content-Type':types[path.extname(file)] || 'application/octet-stream'});
    // Exercise the real shell without starting the application or its runtime stream.
    res.end(file.endsWith('index.html') ? fs.readFileSync(file, 'utf8').replace('<script src="js/boot.js"></script>', '') : fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({channel:process.env.PLAYWRIGHT_CHANNEL || 'msedge', headless:true});
    const page = await browser.newPage({viewport:{width:1440,height:1000}, reducedMotion:'reduce'});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(base);
    await page.evaluate(() => {
      const N = window.N;
      N.state.view = 'plugins';
      N.state.plugins = [{id:'fixture',name:'测试插件',version:'1.0',status:'ready',enabled:true,
        pages:[{id:'html',title:'HTML 页面',path:'web/index.html'},
          {id:'native',title:'宿主页面',renderer:'host'}]}];
      document.querySelectorAll('.view').forEach(view => { view.hidden = view.id !== 'view-plugins'; });
      N.renderPlugins();
    });
    const readmeButton = page.locator('[data-act="plug-readme"]');
    const readmeDialog = page.locator('#plugin-readme-dialog');
    const pageDialog = page.locator('#plugin-page-dialog');
    const close = async dialog => { await dialog.locator('[data-close]').click(); await dialog.waitFor({state:'hidden'}); };
    const screenshot = async name => {
      if (!process.env.NEXUS_UI_ARTIFACTS) return;
      fs.mkdirSync(process.env.NEXUS_UI_ARTIFACTS, {recursive:true});
      await page.screenshot({path:path.join(process.env.NEXUS_UI_ARTIFACTS, name + '.png')});
    };
    await readmeButton.click();
    await readmeDialog.locator('table').waitFor();
    assert.equal(await readmeDialog.locator('h1').textContent(), '插件介绍');
    assert.equal(await readmeDialog.locator('li').count(), 2);
    assert.equal(await readmeDialog.locator('pre code').count(), 1);
    assert.equal(await readmeDialog.locator('a').getAttribute('href'), base + '/plugins/fixture/docs/help.md');
    assert.equal(await readmeDialog.locator('img').getAttribute('src'), base + '/plugins/fixture/docs/example.svg');
    assert.equal(await page.evaluate(() => window.readmeScriptRan), undefined);
    assert.equal(await page.locator('.plug-detail .plug-actions').count(), 1);
    await screenshot('readme-desktop');
    await page.keyboard.press('Escape');
    await readmeDialog.waitFor({state:'hidden'});
    assert.equal(await readmeButton.evaluate(node => node === document.activeElement), true);

    readme = null;
    await readmeButton.click();
    await readmeDialog.getByText('此插件未提供介绍文档').waitFor();
    await close(readmeDialog);
    readmeStatus = 500;
    await readmeButton.click();
    await readmeDialog.locator('[role="alert"]').waitFor();
    readmeStatus = 200; readme = '# 已恢复';
    await readmeDialog.getByText('重新读取').click();
    await readmeDialog.getByText('已恢复', {exact:true}).waitFor();
    await close(readmeDialog);
    delay = 250;
    await readmeButton.click();
    await readmeDialog.getByText('正在读取介绍…').waitFor();
    await close(readmeDialog);
    await page.waitForTimeout(300);
    assert.equal(await readmeDialog.locator('#plugin-readme-body').innerHTML(), '');
    delay = 0;

    const openHtml = page.locator('[data-act="plug-page"][data-page-id="html"]');
    await openHtml.click();
    await page.frameLocator('#plugin-frame').locator('#draft').fill('保留草稿');
    const firstLoads = iframeLoads;
    await page.evaluate(() => window.N.renderPlugins());
    assert.equal(await page.frameLocator('#plugin-frame').locator('#draft').inputValue(), '保留草稿');
    assert.equal(iframeLoads, firstLoads);
    await screenshot('page-desktop');
    await close(pageDialog);
    assert.equal(await page.locator('#plugin-frame').count(), 0);
    assert.equal(await openHtml.evaluate(node => node === document.activeElement), true);
    await openHtml.click();
    assert.equal(await page.frameLocator('#plugin-frame').locator('#draft').inputValue(), '');
    assert.equal(iframeLoads, firstLoads + 1);
    await page.evaluate(() => { window.N.state.plugins[0].status = 'unloaded'; window.N.renderPlugins(); });
    await pageDialog.waitFor({state:'hidden'});
    assert.equal(await page.locator('#plugin-frame').count(), 0);
    await page.evaluate(() => { window.N.state.plugins[0].status = 'ready'; window.N.renderPlugins(); });

    const openNative = page.locator('[data-act="plug-page"][data-page-id="native"]');
    await openNative.click();
    await pageDialog.getByLabel('草稿').fill('宿主草稿');
    await page.evaluate(() => window.N.renderPlugins());
    assert.equal(await pageDialog.getByLabel('草稿').inputValue(), '宿主草稿');
    await close(pageDialog);
    await openNative.click();
    assert.equal(await pageDialog.getByLabel('草稿').inputValue(), '');
    await close(pageDialog);
    pageDelay = 250;
    await openNative.click();
    await close(pageDialog);
    await page.waitForTimeout(300);
    assert.equal(await page.locator('#plugin-host-page').count(), 0);
    pageDelay = 0;

    await page.setViewportSize({width:390,height:844});
    readme = '# 窄屏介绍\n\n' + '插件使用说明。'.repeat(200);
    await readmeButton.click();
    await readmeDialog.locator('h1').waitFor();
    const box = await readmeDialog.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844);
    assert.equal(await readmeDialog.locator('#plugin-readme-body').evaluate(node => node.scrollHeight > node.clientHeight), true);
    await screenshot('readme-mobile');
    await close(readmeDialog);
    await openNative.click();
    await pageDialog.getByLabel('草稿').waitFor();
    await page.waitForFunction(() => getComputedStyle(document.querySelector('#plugin-host-page')).paddingLeft === '16px');
    await screenshot('page-mobile');
    // Clicking the exposed backdrop uses the shell's existing close behavior.
    await page.mouse.click(2, 2);
    await pageDialog.waitFor({state:'hidden'});
    assert.deepEqual(errors, []);
  } finally {
    await browser?.close();
    await new Promise(resolve => server.close(resolve));
  }
});
