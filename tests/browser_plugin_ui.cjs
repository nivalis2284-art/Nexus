/* Focused UI checks on a local mock host. No live data, plugins or models. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {spawn, execFileSync} = require('node:child_process');
const {until, connect, killOwned} = require('./browser_smoke.cjs');

const root = path.resolve(__dirname, '..');
const ids = ['mobile-bridge', 'temp-memory', 'temp-persona', 'web-browser', 'sdk-example'];
const manifests = ids.map(id => ({...JSON.parse(fs.readFileSync(path.join(root,
  id === 'sdk-example' ? 'mobile-bridge/examples/sdk-example/plugin.json' : `plugins/${id}/plugin.json`))),
  enabled: true, status: 'ready', settings: {}, active_calls: 0}));
const settings = {}, calls = [], notes = [];
let revision = 1, ownerText = 'Owner identity', personaText = 'Persona identity';
let baseline = false;
const previous = new Map(['api.js', 'plugins.js'].map(name => [name,
  execFileSync('git', ['show', 'HEAD:nexus/web/js/' + name], {cwd: root})]));

function snapshot(id) {
  const value = settings[id] || {};
  if (id === 'mobile-bridge') return {addresses: ['192.168.1.2:18767'], port: 18767, devices: [],
    settings: {bind: '0.0.0.0', port: 18767, ...value}};
  if (id === 'temp-memory') return {settings: {auto_recall: true, top_k: 6, context_chars: 6000, ...value},
    stats: {notes: notes.length, sources: 3}, embedding: {}, export_ready: true};
  if (id === 'temp-persona') return {current: {revision, owner_text: ownerText, persona_text: personaText},
    settings: {schedule_time: '04:00', ...value}, status: {state: 'idle'}, history: [], reflections: []};
  if (id === 'sdk-example') return {session_id: 'ui-test-session', running: false};
  return {settings: {search_provider: 'bing', ...value}, browser: {running: false}, history: []};
}

function pluginCall(id, action, values) {
  calls.push({id, action, values});
  if (action === 'snapshot') return snapshot(id);
  if (action === 'configure') {settings[id] = {...settings[id], ...values}; return snapshot(id);}
  if (action === 'pair') return {...snapshot(id), pair_code: '123456', pair_expires_at: 1793000000};
  if (action === 'list') return {notes};
  if (action === 'upsert') {notes.push({id: 'note-1', content: values.content, tags: []}); return {};}
  if (action === 'forget') {notes.length = 0; return {};}
  if (action === 'search') return {results: [{text: '记忆命中', title: '搜索命中', url: 'https://example.com/', snippet: '结果正文'}]};
  if (action === 'save') {ownerText = values.owner_text; personaText = values.persona_text; revision++; return snapshot(id);}
  if (action === 'tabs') return {tabs: [{id: 'tab-1', title: '测试网页'}]};
  if (action === 'navigate' || action === 'read') return {page: {id: 'tab-1', title: '测试网页',
    url: 'https://example.com/', text: '网页正文', elements: [{ref: 'element-1', tag: 'input', name: '示例字段'}]}};
  if (action === 'export') return {path: '/mock/backup.zip'};
  return {};
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    const route = decodeURIComponent(url.pathname);
    const json = value => {res.writeHead(200, {'Content-Type': 'application/json'}); res.end(JSON.stringify(value));};
    if (route === '/api/stream') {res.writeHead(200, {'Content-Type': 'text/event-stream'}); res.write(': connected\n\n'); return;}
    if (route === '/api/state') return json({providers: [], workspace: 'UI test', context_window: 32000});
    if (route === '/api/ling') return json({session_id: 'ui-test-session', history_version: 'test', tasks: [],
      items: [{index: 0, role: 'user', text: '检查界面', at: '2026-10-29T03:25:00Z'},
        {index: 1, role: 'assistant', text: '界面已准备好。', at: '2026-10-29T03:25:00Z'}], running: false, inputs: []});
    if (route === '/api/context') return json({owner_instructions: [], roles: [], sections: [], messages: [], version: 'test'});
    if (route === '/api/skills') return json({skills: []});
    if (route === '/api/mcp') return json({servers: []});
    if (route === '/api/instructions') return json({text: ''});
    if (route === '/api/tasks') return json({tasks: []});
    if (route === '/api/model-requests') return json({requests: []});
    if (route === '/api/plugins') return json({root: 'plugins', plugins: manifests});
    const api = /^\/api\/plugins\/([^/]+)\/(call|commands)$/.exec(route);
    if (api) {
      let text = ''; for await (const part of req) text += part;
      const body = JSON.parse(text);
      return json(api[2] === 'commands' ? {} : pluginCall(api[1], body.action, body.values || {}));
    }
    let file;
    const plugin = /^\/plugins\/([^/]+)\/(.+)$/.exec(route);
    if (plugin) file = path.join(root, plugin[1] === 'sdk-example' ? 'mobile-bridge/examples/sdk-example' : 'plugins/' + plugin[1], plugin[2]);
    else file = path.join(root, 'nexus/web', route === '/' ? 'index.html' : route);
    const body = baseline && route.startsWith('/js/') && previous.has(path.basename(file))
      ? previous.get(path.basename(file)) : fs.readFileSync(file);
    const mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.ttf': 'font/ttf', '.woff2': 'font/woff2'}[path.extname(file)] || 'application/octet-stream';
    res.writeHead(200, {'Content-Type': mime + (mime.startsWith('text/') ? '; charset=utf-8' : '')}); res.end(body);
  } catch (error) {res.writeHead(500); res.end(String(error));}
});

(async () => {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-plugin-ui-'));
  const art = path.join(root, '.artifacts/plugin-ui'); fs.mkdirSync(art, {recursive: true});
  let chrome, browser, page, chromeOutput = '';
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const base = `http://127.0.0.1:${server.address().port}/`;
    chrome = spawn(process.env.NEXUS_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe',
      ['--headless=new', '--remote-debugging-port=0', '--user-data-dir=' + profile, '--no-first-run', '--no-default-browser-check', 'about:blank'],
      {windowsHide: true, stdio: ['ignore', 'ignore', 'pipe']});
    chrome.stderr.on('data', value => chromeOutput += value);
    const ws = await until(() => chromeOutput.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1], 'Chrome startup');
    browser = await connect(ws);
    const target = await browser.call('Target.createTarget', {url: 'about:blank'});
    const endpoint = new URL(ws); endpoint.protocol = 'http:'; endpoint.pathname = '/json/list';
    const targets = await (await fetch(endpoint)).json();
    page = await connect(targets.find(t => t.id === target.targetId).webSocketDebuggerUrl);
    await page.call('Runtime.enable'); await page.call('Page.enable');
    await page.call('Emulation.setTimezoneOverride', {timezoneId: 'UTC'});
    const ev = expression => page.evaluate(expression);
    const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
    const shot = async name => {
      await wait(250);
      if (name.startsWith('host-')) await ev('document.getAnimations().forEach(a=>{if(a.effect.getTiming().iterations===Infinity){a.pause();a.currentTime=0;}else a.finish();})');
      const png = Buffer.from((await page.call('Page.captureScreenshot', {format: 'png'})).data, 'base64');
      fs.writeFileSync(path.join(art, name + '.png'), png); return png;
    };
    const size = (width, height = 1000) => page.call('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width < 600});
    async function load() {
      await page.call('Page.navigate', {url: base});
      await until(() => ev('Boolean(window.N&&N.state.sessionId&&N.state.plugins.length===5)'), 'Host bootstrap');
      await ev('document.fonts.ready'); await wait(300);
    }
    async function click(selector) {
      await until(() => ev(`Boolean(document.querySelector(${JSON.stringify(selector)}))`), 'Missing ' + selector);
      await ev(`document.querySelector(${JSON.stringify(selector)}).click()`);
    }
    async function open(id) {
      await click('.plug-item[data-id="' + id + '"]');
      await click('[data-act="plug-page"]');
      await until(() => ev('Boolean(document.getElementById("plugin-frame")?.contentDocument?.querySelector("body[data-nexus-plugin]"))'), 'Plugin body ' + id);
      await until(() => ev('document.getElementById("plugin-frame").contentDocument.readyState==="complete"'), 'Plugin load ' + id);
      await ev('document.getElementById("plugin-frame").contentDocument.fonts.ready');
    }
    const inside = code => ev(`(()=>{const d=document.getElementById('plugin-frame').contentDocument;${code}})()`);

    await size(1440);
    await page.call('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'reduce'}]});
    baseline = true; await load(); await shot('host-before');
    baseline = false; await load(); await shot('host-after');
    for (const file of ['index.html', 'css/ling.css', 'css/app.css', 'css/theme.css', 'js/core.js', 'js/shell.js', 'js/dialogs.js', 'js/chat.js']) {
      const normalize = value => value.toString('utf8').replace(/\r\n/g, '\n');
      assert.equal(normalize(fs.readFileSync(path.join(root, 'nexus/web', file))), normalize(execFileSync('git', ['show', 'HEAD:nexus/web/' + file], {cwd: root})), 'Frozen source changed: ' + file);
    }
    await page.call('Emulation.setTimezoneOverride', {timezoneId: 'Asia/Hong_Kong'});
    assert.deepEqual(await ev('[N.clock("2026-10-29T03:25:00Z"),N.clock("2026-10-29T03:25:00+02:00"),N.clock("2026-10-28T20:25:00Z"),N.clock("9:05"),N.clock("09:05:30")]'), ['11:25', '09:25', '04:25', '09:05', '09:05']);
    await click('.dock-btn[data-view="plugins"]');
    const scales = {'0.5': .675, '0.75': 1.0125, '1': 1.35, '1.25': 1.6875, '1.5': 2.025};
    for (const id of ids) {
      await open(id);
      for (const theme of ['day', 'night']) for (const palette of ['jade', 'cool']) {
        await ev(`N.setTheme(${JSON.stringify(theme)});N.setPalette(${JSON.stringify(palette)})`);
        await wait(50);
        assert.equal(await inside(`const host=getComputedStyle(document.documentElement),child=d.defaultView.getComputedStyle(d.documentElement);return ['--paper','--card','--ink','--jade','--font','--sp-settle'].every(key=>host.getPropertyValue(key).trim()===child.getPropertyValue(key).trim());`), true, id + ' theme');
      }
      for (const [motion, scale] of Object.entries(scales)) {
        await ev(`N.setMotion(${JSON.stringify(motion)},true)`); await wait(20);
        assert.equal(Number(await inside("return d.defaultView.getComputedStyle(d.documentElement).getPropertyValue('--motion')")), scale);
      }
      await size(390, 844);
      assert.equal(await inside('return d.documentElement.scrollWidth<=d.documentElement.clientWidth'), true, id + ' mobile overflow');
      await shot(id + '-mobile');
      await size(1440);
      await ev('N.setTheme("day");N.setPalette("jade")');
      await shot(id + '-desktop');
      assert.equal(await inside("return [...d.querySelectorAll('button')].every(b=>b.classList.contains('btn'))"), true, id + ' button styles');
    }

    // Ordinary updates retain the iframe's document, focus, draft and scroll.
    await open('mobile-bridge');
    await ev(`window.savedFrame=document.getElementById('plugin-frame');window.savedDocument=savedFrame.contentDocument;const input=savedDocument.getElementById('bind');input.value='UNSAVED';input.focus();savedDocument.scrollingElement.scrollTop=120;window.savedScroll=savedDocument.scrollingElement.scrollTop;N.state.plugins.find(p=>p.id==='temp-persona').progress='Other plugin updated';N.renderPlugins();`);
    assert.equal(await ev("savedFrame===document.getElementById('plugin-frame')&&savedDocument===savedFrame.contentDocument&&savedDocument.getElementById('bind').value==='UNSAVED'&&savedDocument.activeElement.id==='bind'&&savedDocument.scrollingElement.scrollTop===savedScroll"), true);
    await click('[data-act="plug-refresh"]');
    await until(() => ev('document.querySelector("[data-act=plug-refresh]").disabled===false'), 'Host refresh');
    assert.equal(await ev('savedFrame===document.getElementById("plugin-frame")&&savedDocument===savedFrame.contentDocument'), true);
    await click('[data-act="plug-close-frame"]');
    assert.equal(await ev('document.getElementById("plugin-frame")'), null);
    await click('[data-act="plug-page"]');
    await until(() => ev('Boolean(document.getElementById("plugin-frame")?.contentDocument?.getElementById("bind"))'), 'Reopen');
    assert.equal(await ev('savedDocument===document.getElementById("plugin-frame").contentDocument'), false);

    // Business controls use only the in-memory mock API.
    await inside("d.getElementById('pair').click()");
    await until(() => inside("return d.getElementById('code').textContent==='123456'"), 'Pair control');
    await inside("d.getElementById('bind').value='127.0.0.1';d.getElementById('save').click()");
    await until(() => settings['mobile-bridge']?.bind === '127.0.0.1', 'Mobile settings');
    await open('temp-memory');
    await inside("d.getElementById('new-note').click();d.getElementById('content').value='测试笔记';d.getElementById('note-form').requestSubmit()");
    await until(() => inside("return d.getElementById('notes').textContent.includes('测试笔记')"), 'Note save');
    assert.equal(await inside("return [...d.querySelectorAll('#notes button')].every(b=>b.classList.contains('btn'))"), true);
    await inside("d.getElementById('query').value='记忆';d.getElementById('search-form').requestSubmit()");
    await until(() => inside("return d.getElementById('results').textContent.includes('记忆命中')"), 'Memory search');
    await open('temp-persona');
    await inside("d.getElementById('owner-text').value='新的人设';d.getElementById('persona-form').requestSubmit()");
    await until(() => ownerText === '新的人设', 'Persona save');
    await open('web-browser');
    await inside("d.getElementById('query').value='搜索';d.getElementById('search-form').requestSubmit()");
    await until(() => inside("return d.getElementById('results').textContent.includes('搜索命中')"), 'Web search');
    await inside("d.getElementById('url').value='https://example.com/';d.getElementById('navigate-form').requestSubmit()");
    await until(() => inside("return d.getElementById('elements').textContent.includes('示例字段')"), 'Browser controls');
    assert.equal(await inside("return [...d.querySelectorAll('#elements button')].every(b=>b.classList.contains('btn'))"), true);
    await open('sdk-example');
    await inside("d.getElementById('read').click()");
    await until(() => inside("return d.getElementById('session').value==='ui-test-session'"), 'SDK query');

    // Compare motion at the two speed endpoints, and reduced-motion behavior.
    await page.call('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'no-preference'}]});
    for (const speed of ['0.5', '1.5']) {
      await ev(`N.setMotion(${JSON.stringify(speed)},true)`); await wait(50);
      const time = await inside("return parseFloat(d.defaultView.getComputedStyle(d.getElementById('read')).transitionDuration)");
      assert.ok(Math.abs(time - .24 * scales[speed]) < .001, 'Motion scale ' + speed);
    }
    await click('[data-act="plug-close-frame"]');
    await click('[data-act="plug-page"]');
    await until(() => inside("return Boolean(d.getElementById('read'))"), 'SDK reopen');
    assert.equal(await inside('return d.querySelector("main").getAnimations({subtree:true}).length>0'), true, 'Plugin entry motion');
    await page.call('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'reduce'}]});
    await click('[data-act="plug-close-frame"]'); await click('[data-act="plug-page"]');
    await until(() => inside("return Boolean(d.getElementById('read'))"), 'Reduced reopen');
    assert.equal(await inside('return d.querySelector("main").getAnimations({subtree:true}).length'), 0);
    assert.deepEqual(page.errors, []);
    console.log('PASS: frozen host sources unchanged; comparison screenshots captured; local times; five themed plugin pages; 4 theme variants, 5 speeds, mobile, entry/reduced motion; iframe drafts/focus/scroll; mocked business controls and SDK.');
  } finally {
    if (page) page.close();
    if (browser) {try {await browser.call('Browser.close');} catch {} browser.close();}
    killOwned(chrome);
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
    const resolved = fs.realpathSync(profile);
    assert.ok(path.dirname(resolved) === fs.realpathSync(os.tmpdir()) && path.basename(resolved).startsWith('nexus-plugin-ui-'));
    fs.rmSync(resolved, {recursive: true, force: true, maxRetries: 3, retryDelay: 100});
  }
})().catch(error => {console.error(error.stack); process.exitCode = 1;});
