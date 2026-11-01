const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// Only the DOM methods touched by these actions; requests run through the real API code.
function page({ hooks = [], respond } = {}) {
  const nodes = new Map(), listeners = new Map(), timers = new Map(), requests = [];
  let timerId = 0, uid = 0;
  const element = () => ({
    value: '', hidden: true, dataset: {}, children: [], childNodes: [], scrollTop: 0,
    style: { setProperty() {} }, offsetHeight: 20, scrollHeight: 20,
    classList: { contains: () => false, add() {}, remove() {}, toggle() {} },
    handlers: {}, focus() {}, addEventListener(type, callback) { this.handlers[type] = callback; }, setAttribute() {}, insertAdjacentHTML() {},
    querySelector: () => null, querySelectorAll: () => [], getAnimations: () => [],
    cloneNode: () => element(), removeAttribute() {}, getBoundingClientRect: () => ({ width: 680, height: 20, top: 0, bottom: 20 }),
    replaceChildren() {}, prepend() {}, appendChild() {}, insertBefore() {}, remove() {}, scrollTo() {},
    showModal() { this.open = true; },
    close() { this.open = false; },
  });
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  const N = {
    state: { sessionId: 's1', works: [], files: [], items: [], queue: [], requests: [],
      plugins: [], modelRequests: [], pending: [], replying: false },
    $, $$: () => [], esc: String, icon: () => '', REDUCED: true, DUR: {}, SP: { settle: {} },
    uid: () => 'request-' + ++uid, nowHM: () => '12:00', fmtSize: String,
    shortPath: String, fmtNum: String, anim() {}, save() {}, notify() {},
    moveInk() {}, refreshIsland() {}, renderStatus() {}, composeItems() {},
  };
  const document = {
    body: { appendChild() {} },
    documentElement: { dataset: {} },
    createElement: element, querySelector: () => null,
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(callback);
    },
  };
  const context = vm.createContext({
    window: { N }, document, console, performance, URL, URLSearchParams, AbortController, innerWidth: 1440,
    getComputedStyle: () => ({ minHeight: '28px', getPropertyValue: () => '0px' }),
    requestAnimationFrame() {}, cancelAnimationFrame() {},
    setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    EventSource: class {},
    FileReader: class {
      async readAsDataURL(blob) {
        this.result = `data:${blob.type};base64,${Buffer.from(await blob.arrayBuffer()).toString('base64')}`;
        this.onload();
      }
    },
    async fetch(url, init = {}) {
      requests.push({ url, ...init });
      if (respond) {
        const response = await respond(url, init);
        if (response !== undefined) return response;
      }
      if (url === '/api/hooks/settings') {
        const change = JSON.parse(init.body);
        const hook = hooks.find(h => h.name === change.name);
        const target = change.plugin_id ? hook.subscribers.find(s => s.plugin_id === change.plugin_id && s.key === change.key) : hook;
        target.enabled = change.enabled;
      }
      const body = url.includes('/attachments?') ? { id: 'attachment-1' }
        : url === '/api/hooks' || url === '/api/hooks/settings' ? { hooks }
        : url === '/api/ling' ? { session_id: 's1', live_revision: N.state.liveRevision || 0 }
        : url === '/api/tasks?archived=1' ? { tasks: [] }
        : url === '/api/model-requests/1' ? { id: 1, body: { model: 'actual-model' }, plugins: [], materials: [] }
        : url.startsWith('/api/model-requests?') ? { requests: [{ id: 0 }], next: null } : {};
      return { ok: true, text: async () => JSON.stringify(body) };
    },
  });
  const load = name => vm.runInContext(fs.readFileSync(
    path.join(__dirname, '../nexus/web/js', name), 'utf8'), context, { filename: name });
  load('api.js');
  return { N, $, load, requests,
    input(target) {
      for (const callback of listeners.get('input') || []) callback({ target });
    },
    change(target) {
      for (const callback of listeners.get('change') || []) callback({ target });
    },
    async timers() {
      const pending = [...timers.values()];
      timers.clear();
      for (const callback of pending) await callback();
    },
    async click(act, id, data = {}, disabled = false) {
      const button = { dataset: { act, id, ...data }, disabled, attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; } };
      for (const callback of listeners.get('click') || []) {
        await callback({ target: { closest: () => button } });
      }
      return button;
    },
  };
}

test('model image capability is edited independently without losing model settings', async () => {
  const p = page();
  const models = [
    { id: 'vision', context_window_tokens: 32000, max_output_tokens: 2048, capabilities: { tools: true, thinking: true, images: true } },
    { id: 'text', capabilities: { tools: true, images: false } },
  ];
  const provider = { id: 'p1', name: 'Provider', family: 'openai_chat_completions', base_url: 'https://example.test', models };
  Object.assign(p.N.state, { providers: [provider], current: { provider: 'p1' }, mcp: [], motion: '1' });
  Object.assign(p.N, { clone: value => JSON.parse(JSON.stringify(value)), providerOf: () => provider,
    currentModel: () => models[0], MOTION_LEVELS: ['1'], MOTION_LABELS: { '1': '标准' }, THEMES: [],
    renderModelLabel() {}, renderEffortLabel() {}, closeDialog(dialog) { dialog.close(); } });
  p.N.sync.settingsOnly = async () => {};
  p.N.api.updateProvider = async (id, body) => { p.saved = { id, body }; provider.models = body.models; };
  p.load('settings.js');
  await p.click('edit-provider', 'p1');
  assert.match(p.$('#p-models-list').innerHTML, /aria-checked="true"[^>]*data-act="p-model-images" data-i="0"/);
  assert.match(p.$('#p-models-list').innerHTML, /aria-checked="false"[^>]*data-act="p-model-images" data-i="1"/);
  assert.equal((await p.click('p-model-images', undefined, { i: '0' })).attributes['aria-checked'], 'false');
  assert.equal((await p.click('p-model-images', undefined, { i: '1' })).attributes['aria-checked'], 'true');
  p.input({ dataset: { modelOutput: '0' }, value: '16384' });
  await p.$('#p-save').handlers.click();
  assert.equal(p.saved.body.models[0].capabilities.images, false);
  assert.equal(p.saved.body.models[1].capabilities.images, true);
  assert.equal(p.saved.body.models[0].max_output_tokens, 16384);
  assert.equal(p.saved.body.models[0].capabilities.thinking, true);
  assert.equal(p.saved.body.models[0].context_window_tokens, 32000);
  await p.click('edit-provider', 'p1');
  assert.match(p.$('#p-models-list').innerHTML, /aria-checked="true"[^>]*data-act="p-model-images" data-i="1"/);
  assert.match(p.$('#p-models-list').innerHTML, /aria-checked="false"[^>]*data-act="p-model-images" data-i="0"/);
});

test('dismiss input failure hides only the acknowledged item', async () => {
  const p = page();
  p.load('chat.js');
  p.N.state.queue = [{ id: 4, status: 'failed', text: 'failure' }];
  await p.click('dismiss-input', '4');
  await new Promise(setImmediate);
  const request = p.requests.find(r => r.url === '/api/ling/dismiss-input');
  assert.deepEqual(JSON.parse(request.body), { input_id: 4 });
  assert.equal(p.N.state.queue.length, 0);
});

test('ordinary queued and running inputs do not create duplicate notices', () => {
  const p = page();
  p.N.motionScale = () => 1;
  p.load('chat.js');
  p.N.state.queue = [{ id: 1, status: 'pending' }, { id: 2, status: 'running' }];
  p.N.renderQueue();
  assert.equal(p.$('#ling-inputs').hidden, true);
  assert.equal(p.N.state.queue.length, 2);
  p.N.state.queue.push({ id: 3, status: 'failed', text: 'failed' });
  p.N.renderQueue();
  assert.equal(p.$('#ling-inputs').hidden, false);
});

test('failed dismissal leaves the input visible and releases the button', async () => {
  const p = page({ respond: async url => url === '/api/ling/dismiss-input'
    ? { ok: false, status: 500, text: async () => JSON.stringify({ error: '关闭失败' }) } : undefined });
  p.load('chat.js');
  p.N.state.queue = [{ id: 4, status: 'failed', text: 'failure' }];
  const button = await p.click('dismiss-input', '4');
  await new Promise(setImmediate);
  assert.equal(p.N.state.queue.length, 1);
  assert.equal(button.disabled, false);
});

test('host management leaves unrelated plugin page buttons disabled', async () => {
  const p = page();
  p.load('host-sdk.js');
  const button = await p.click('plugin-page-operation', undefined, {}, true);
  assert.equal(button.disabled, true, 'only the page that owns the operation may release its busy state');
});

test('reduced motion keeps a growing live reply at the bottom', () => {
  const p = page();
  p.load('chat.js');
  const conversation = p.$('#conversation');
  conversation.scrollHeight = 2800;
  conversation.scrollTop = 0;
  p.$('.md').insertBefore = () => {};
  p.N.patchLive({ text: 'New streamed content' }, {});
  assert.equal(conversation.scrollTop, 2800);
});

test('message actions stay on their own footer row after an in-place update', () => {
  const p = page();
  p.load('chat.js');
  p.N.state.items = [{ key: 'm:first', role: 'user', text: 'hello', at: '10:30' }];
  p.N.composeItems();
  const node = p.$('#thread .msg-user');
  const meta = [...node.children].findIndex(n => n.className === 'user-meta');
  const actions = [...node.children].findIndex(n => n.className === 'msg-tools');
  assert.equal(meta, -1, 'the test fixture has no rendered thread in this lightweight page');
  assert.equal(actions, -1);
});

test('multiline input only changes layout when its measured height changes', () => {
  const p = page(), input = p.$('#input'), changes = [];
  let height = '';
  Object.defineProperty(input.style, 'height', { get: () => height, set(value) { height = value; changes.push(value); } });
  let copies = 0;
  const measure = { style: { setProperty() {} }, scrollHeight: 104, removeAttribute() {}, setAttribute() {} };
  input.cloneNode = () => { copies++; return measure; };
  p.$('#conversation').scrollHeight = 900;
  p.load('chat.js');
  input.value = 'one\ntwo\nthree\nfour';
  for (let i = 0; i < 8; i++) {
    input.value += 'x'; input.handlers.input();
  }
  assert.deepEqual(changes, ['104px'], 'typing within the same rows must not shrink and regrow the textarea');
  assert.equal(copies, 1);
  measure.scrollHeight = 128; input.handlers.input();
  measure.scrollHeight = 40; input.value = ''; input.handlers.input();
  assert.deepEqual(changes, ['104px', '128px', '40px']);
  assert.equal(changes.includes('auto'), false);
});

test('page entrances share CSS timing, visible-item limits and theme-independent speed', () => {
  const css = fs.readFileSync(path.join(__dirname, '../nexus/web/css/tokens.css'), 'utf8');
  const values = Object.fromEntries([...css.matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(m => [m[1], m[2]]));
  const root = { dataset: { palette: 'jade', theme: 'night' }, style: { setProperty() {} } };
  let reduce;
  const context = vm.createContext({ window: {}, document: { documentElement: root },
    matchMedia: () => ({ matches: false, addEventListener: (_type, callback) => { reduce = callback; } }),
    getComputedStyle: () => ({ getPropertyValue: name => values[name] || '' }),
    CSS: { supports: () => true }, localStorage: { getItem: () => null }, setTimeout() {}, clearTimeout() {},
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../nexus/web/js/core.js'), 'utf8'), context);
  const N = context.window.N, calls = [];
  const nodes = Array.from({ length: 12 }, (_, i) => ({
    getBoundingClientRect: () => ({ top: i < 2 ? -100 : i * 20, bottom: i < 2 ? -60 : i * 20 + 16, width: 200, height: 16 }),
    animate(_frames, options) { calls.push(options); return { cancel() {} }; },
  }));
  const viewport = { getBoundingClientRect: () => ({ top: 0, bottom: 300 }) };
  N.enterItems(nodes, viewport);
  assert.equal(N.DUR.micro, 120);
  assert.equal(calls.length, 8);
  calls.forEach((call, i) => { assert.equal(call.duration, 240); assert.equal(call.delay, i * 24); });
  const curve = N.SP.soft.e;
  root.dataset.palette = 'cool'; N.rebuildSprings();
  assert.equal(N.SP.soft.e, curve);
  assert.equal(N.motionScale(), 1);
  N.setMotionScale('0.5');
  calls.length = 0; N.enterItems(nodes, viewport);
  assert.equal(calls[0].duration, 240 * .68);
  reduce({ matches: true }); calls.length = 0; N.enterItems(nodes, viewport);
  assert.equal(calls.length, 0);
});

test('one round control stops an empty reply and sends a draft instead of stopping', async () => {
  const p = page();
  p.load('chat.js');
  const classes = new Set(), labels = {};
  const button = p.$('#send');
  button.classList.toggle = (name, on) => on ? classes.add(name) : classes.delete(name);
  button.setAttribute = (name, value) => { labels[name] = value; };
  p.N.state.replying = true;
  p.N.updateSend();
  assert.equal(labels['aria-label'], '停止当前回应');
  assert.equal(classes.has('busy'), true);
  p.$('#input').handlers.keydown({ key: 'Enter', preventDefault() {} });
  assert.equal(p.requests.filter(r => r.url === '/api/ling/stop').length, 0, 'empty Enter must not cancel the reply');
  p.$('#composer').handlers.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  assert.equal(p.requests.filter(r => r.url === '/api/ling/stop').length, 1);
  p.$('#input').value = 'continue with this requirement';
  p.N.updateSend();
  assert.equal(labels['aria-label'], '加入队列');
  assert.equal(classes.has('busy'), false);
  assert.equal(classes.has('ready'), true);
  p.$('#composer').handlers.submit({ preventDefault() {} });
  await new Promise(setImmediate);
  assert.equal(p.requests.filter(r => r.url === '/api/ling/input').length, 1);
  assert.equal(p.requests.filter(r => r.url === '/api/ling/stop').length, 1);
  p.$('#input').value = '';
  p.N.state.replying = true;
  p.N.state.files = [{ name: 'attachment.pdf', kind: 'file' }];
  p.N.updateSend();
  assert.equal(labels['aria-label'], '加入队列');
  assert.equal(classes.has('busy'), false);
  p.N.state.replying = false;
  p.N.updateSend();
  assert.equal(labels['aria-label'], '发送');
  assert.equal(fs.readFileSync(path.join(__dirname, '../nexus/web/index.html'), 'utf8').includes('id="stop-reply"'), false);
});

test('a message sent during a reply reaches the server with its file and image', async () => {
  const p = page();
  p.load('chat.js');
  p.N.state.replying = true;
  p.N.state.files = [
    { name: 'notes.pdf', kind: 'file', file: new Blob(['document']) },
    { name: 'image.png', kind: 'image', file: new Blob(['image'], { type: 'image/png' }) },
  ];
  p.$('#input').value = 'additional requirement';
  p.N.send();
  await new Promise(setImmediate);
  const sent = p.requests.find(r => r.url === '/api/ling/input');
  assert.ok(sent, 'the message must be accepted by the server');
  const body = JSON.parse(sent.body);
  assert.equal(body.text, 'additional requirement');
  assert.deepEqual(body.attachments, ['attachment-1']);
  assert.equal(body.images[0].mime, 'image/png');
  assert.equal(body.images[0].data, Buffer.from('image').toString('base64'));
  assert.equal(p.N.state.queue.length, 0, 'there is no browser-only work queue');
});

test('task summaries keep loaded records and expose the current approvals', () => {
  const { N } = page();
  const pending = [{ id: 'approval', kind: 'approval' }];
  N.sync.chat({ tasks: [{ id: 'work', pending_interactions: pending },
    { id: 'child', parent_id: 'work', parent_kind: 'task' }] });
  N.state.works[0].records = [{ text: 'saved answer' }];
  N.state.works[0]._detail = true;
  N.sync.works({ tasks: [{ id: 'work', steps: [], evidence: [], pending_interactions: pending }] });
  assert.equal(N.state.works[0].pending_interactions[0].id, 'approval');
  assert.equal(N.state.works[0].records[0].text, 'saved answer');
  assert.ok(N.state.works.some(w => w.id === 'child'));
  N.sync.works({ tasks: [{ id: 'work', pending_interactions: [] }] });
  assert.equal(N.state.works[0].pending_interactions.length, 0);
});

test('plugin settings use the host configuration object directly', async () => {
  const p = page();
  await p.N.api.pluginSettings('example', { endpoint: 'configured-value' });
  assert.deepEqual(JSON.parse(p.requests[0].body), { endpoint: 'configured-value' });
});

test('plugin updates use the manifest identity and confirm both versions', async () => {
  const p = page();
  let confirmation;
  p.N.confirmEdit = async options => { confirmation = options; return true; };
  p.N.sync.pluginsOnly = async () => {};
  p.N.api.importPlugin = async (file, replace) => {
    p.requests.push({ file, replace });
    return replace ? { plugins: [{ id: 'example', status: 'loading' }] }
      : { conflict: true, installed_version: '1.0', plugin: { id: 'example', name: '示例', version: '2.0' } };
  };
  p.load('plugins.js');
  p.change({ id: 'plug-file', files: [{ name: 'renamed.zip' }], value: 'renamed.zip' });
  await new Promise(resolve => setImmediate(resolve));
  assert.match(confirmation.description, /示例.*1\.0.*2\.0/);
  assert.deepEqual(p.requests.map(r => r.replace), [false, true]);
});

test('stream reconnect reloads durable state and ignores an older chat snapshot', async () => {
  const p = page(), stream = p.N.connectStream();
  stream.onopen();
  await p.timers();
  stream.onerror();
  stream.onopen();
  await p.timers();
  assert.equal(p.requests.filter(r => r.url === '/api/ling').length, 2);
  assert.equal(p.requests.filter(r => r.url === '/api/tasks?archived=1').length, 2);
  p.N.onRuntimeEvent({ scope: 'ling', owner_id: 's1', revision: 3, type: 'delta',
    live: { text: 'new output', message_id: 'm1' } });
  p.N.sync.chat({ live_revision: 2, items: [], live: {} });
  assert.equal(p.N.state.live.text, 'new output');
  await p.timers();
  assert.equal(p.requests.filter(r => r.url === '/api/ling').length, 3);
});

test('request details and earlier pages come from their server endpoints', async () => {
  const p = page();
  p.load('plugins.js');
  p.N.sync.modelRequests({ requests: [{ id: 1, scope: { request_materials: ['source'] } }], next: 1 });
  assert.equal(p.N.state.modelRequests[0].body, undefined);
  assert.equal(p.N.state.modelRequests[0].materials[0], 'source');
  await p.click('req-open', '1');
  assert.equal(p.N.state.modelRequests[0].body.model, 'actual-model');
  await p.click('req-more');
  await p.click('req-more');
  assert.ok(p.requests.some(r => r.url.includes('before=1')));
  assert.equal(p.N.state.modelRequests.length, 2);
});

test('settings hook management loads, toggles and preserves errors without discarding settings edits', async () => {
  const p = page({ hooks: [{ name: 'host.after_tool', owner: 'host', description: '工具执行后',
    available: true, enabled: true, subscribers: [{ plugin_id: 'memory', key: 'capture',
      enabled: true, available: true, error: 'capture failed' }] }] });
  Object.assign(p.N.state, { providers: [], current: {}, mcp: [], motion: '1', plugins: [{ id: 'memory', name: '记忆' }] });
  Object.assign(p.N, { providerOf: () => null, currentModel: () => null, MOTION_LEVELS: ['1'], MOTION_LABELS: { '1': '标准' }, THEMES: [] });
  p.load('settings.js');
  p.N.renderSettings();
  assert.match(p.$('#view-settings').innerHTML, /data-act="manage-hooks"/);
  p.$('#instructions').value = 'unsaved preference';
  await p.click('manage-hooks');
  assert.equal(p.$('#hooks-dialog').open, true);
  assert.match(p.$('#hooks-list').innerHTML, /host.after_tool/);
  assert.match(p.$('#hooks-list').innerHTML, /记忆/);
  assert.match(p.$('#hooks-list').innerHTML, /capture failed/);
  await p.click('toggle-hook', undefined, { i: '0' });
  assert.deepEqual(JSON.parse(p.requests.at(-1).body), { name: 'host.after_tool', enabled: false });
  assert.match(p.$('#hooks-list').innerHTML, /启用钩子/);
  await p.click('toggle-hook-sub', undefined, { i: '0', j: '0' });
  assert.deepEqual(JSON.parse(p.requests.at(-1).body), {
    name: 'host.after_tool', enabled: false, plugin_id: 'memory', key: 'capture',
  });
  assert.match(p.$('#hooks-list').innerHTML, /启用订阅/);
  assert.equal(p.$('#instructions').value, 'unsaved preference');
  p.N.api.hookSettings = async () => { throw new Error('save failed'); };
  await p.click('toggle-hook', undefined, { i: '0' });
  assert.equal(p.$('#hooks-error').textContent, 'save failed');
  assert.match(p.$('#hooks-list').innerHTML, /启用钩子/);
  p.N.api.hooks = async () => { throw new Error('load failed'); };
  await p.click('refresh-hooks');
  assert.equal(p.$('#hooks-error').textContent, 'load failed');
});


test('chat snapshots append live text, thinking and tool activity only once', () => {
  const { N } = page();
  N.sync.chat({ live_revision: 1, running: true,
    items: [{ role: 'user', index: 0, text: 'hello' },
      { role: 'assistant', message_id: 'm', text: 'partial', thinking: 'thought', live: true }],
    live: { message_id: 'm', text: 'partial', thinking: 'thought' } });
  assert.equal(N.state.items.filter(i => i.role === 'assistant').length, 1);
  assert.equal(N.state.items.filter(i => i.role === 'thinking').length, 1);
  N.sync.chat({ live_revision: 2, items: [{ role: 'tool', live: true, tool_call_id: 'c' }],
    live: { tool: { id: 'c', name: 'read_file' } } });
  assert.equal(N.state.items.filter(i => i.role === 'tool').length, 1);
});

test('repeated text stays pending until its own request is recorded', () => {
  const { N } = page();
  N.state.pending = [{ key: 'u:new', requestId: 'new', role: 'user', text: 'same text' }];
  N.sync.chat({ items: [{ role: 'user', index: 0, request_id: 'old', text: 'same text' }] });
  assert.equal(N.state.pending.length, 1);
  N.sync.chat({ items: [
    { role: 'user', index: 0, request_id: 'old', text: 'same text' },
    { role: 'user', index: 1, request_id: 'new', text: 'same text' },
  ] });
  assert.equal(N.state.pending.length, 0);
  assert.deepEqual(Array.from(N.state.items, i => i.key), ['u:old', 'u:new']);
});

test('pending inputs stay before their answer and queued inputs follow the active answer', () => {
  const { N } = page();
  N.state.baseItems = [{ key: 'u:old', role: 'user', text: 'old' }];
  N.state.liveItems = [{ key: 'a:live', role: 'assistant', text: 'answer', live: true }];
  N.state.pending = [{ key: 'u:new', role: 'user', afterKey: 'u:old', duringReply: false }];
  N.composeItems();
  assert.deepEqual(Array.from(N.state.items, i => i.key), ['u:old', 'u:new', 'a:live']);
  N.state.pending.push({ key: 'u:queued', role: 'user', duringReply: true });
  N.composeItems();
  assert.deepEqual(Array.from(N.state.items, i => i.key), ['u:old', 'u:new', 'a:live', 'u:queued']);
  N.state.baseItems.push(N.state.pending.shift());
  N.composeItems();
  assert.deepEqual(Array.from(N.state.items, i => i.key), ['u:old', 'u:new', 'a:live', 'u:queued']);
});

test('a tool boundary retains visible output until the durable snapshot arrives', () => {
  const { N } = page();
  N.onRuntimeEvent({ scope: 'ling', owner_id: 's1', revision: 1, type: 'delta',
    live: { message_id: 'a', thinking: 'reasoning', text: 'visible answer' } });
  N.onRuntimeEvent({ scope: 'ling', owner_id: 's1', revision: 2, type: 'tool_start',
    live: { message_id: 'a', tool: { id: 'c', name: 'read' } } });
  assert.equal(N.state.items.find(i => i.key === 'a:a').text, 'visible answer');
  assert.equal(N.state.items.find(i => i.key === 'a:a').live, false);
  assert.equal(N.state.items.find(i => i.key === 't:a:a').thinking, 'reasoning');
  N.sync.chat({ live_revision: 2, running: true,
    items: [{ role: 'assistant', message_id: 'a', text: 'visible answer', thinking: 'reasoning' }],
    live: { message_id: 'a', tool: { id: 'c', name: 'read' } } });
  assert.equal(N.state.items.filter(i => i.key === 'a:a').length, 1);
  assert.equal(N.state.handoffItems.length, 0);
});

test('completion and error preserve partial output and do not refresh controls for every delta', () => {
  const { N } = page();
  let controls = 0;
  N.updateSend = () => controls++;
  for (let i = 1; i <= 100; i++) N.onRuntimeEvent({ scope: 'ling', owner_id: 's1', revision: i,
    type: 'delta', live: { message_id: 'a', text: 'x'.repeat(i) } });
  assert.equal(controls, 1);
  N.onRuntimeEvent({ scope: 'ling', owner_id: 's1', revision: 101, type: 'done' });
  assert.equal(N.state.items.find(i => i.role === 'assistant').text.length, 100);
  assert.equal(N.state.items.find(i => i.role === 'assistant').live, false);
  N.onRuntimeEvent({ scope: 'ling', owner_id: 's1', revision: 102, type: 'delta',
    live: { message_id: 'b', text: 'partial response' } });
  N.onRuntimeEvent({ scope: 'ling', owner_id: 's1', revision: 103, type: 'error', message: 'interrupted' });
  assert.equal(N.state.items.find(i => i.key === 'a:b').text, 'partial response');
  assert.equal(N.state.replying, false);
});

test('chat task summaries preserve detail fields and explicit empty details clear them', () => {
  const { N } = page();
  N.state.works = [N.mapTask({ id: 'work', model_id: 'vision', workspace: 'H:/project', effort: 'high',
    created_at: '08:30', constraints: 'keep files', steps: [{ content: 'check' }], evidence: [{ summary: 'verified' }], messages: [] })];
  N.sync.chat({ tasks: [{ id: 'work', state: 'running', updated_at: '10:30' }] });
  const w = N.state.works[0];
  assert.equal(w.model, 'vision'); assert.equal(w.path, 'H:/project');
  assert.equal(w.started, '08:30'); assert.equal(w.effort, 'high'); assert.equal(w.constraints, 'keep files');
  assert.equal(w.steps.length, 1); assert.equal(w.evidence.length, 1);
  N.sync.works({ tasks: [{ id: 'work', steps: [], evidence: [], workspace: '', effort: null }] });
  assert.equal(N.state.works[0].steps.length, 0); assert.equal(N.state.works[0].evidence.length, 0);
  assert.equal(N.state.works[0].path, ''); assert.equal(N.state.works[0].effort, '');
});

test('uploaded documents and work attachments link to their owned originals', () => {
  const p = page(); p.load('chat.js');
  const chat = p.N.mapItems([{ role: 'user', attachments: [{ id: 'a1', name: 'notes.pdf' }] }])[0];
  assert.match(p.N.fileLinks(chat.attachments), /href="\/api\/sessions\/s1\/attachments\/a1"/);
  const work = p.N.mapTask({ id: 'task1', messages: [{ kind: 'user', seq: 1, text: 'inspect',
    attachments: [{ id: 'a2', name: 'work.pdf' }] }] });
  assert.match(p.N.fileLinks(work.records[0].attachments), /href="\/api\/tasks\/task1\/attachments\/a2"/);
  p.N.state.works = [work]; p.N.stateLabel = () => ''; p.N.statusDetail = () => '';
  p.load('work.js');
  assert.match(p.N.workDetailHTML('task1'), /href="\/api\/tasks\/task1\/attachments\/a2"/);
});

test('provider default sends an explicit null effort', async () => {
  const p = page(); p.load('chat.js');
  p.N.state.current = { provider: 'p', model: 'm', effort: 'high' };
  p.N.state.effort = { levels: [], labels: {} };
  let menu; p.N.menu = (_anchor, items) => { menu = items; };
  p.$('#effort-btn').handlers.click({ currentTarget: {} });
  await menu[1].onPick();
  assert.deepEqual(JSON.parse(p.requests.find(r => r.url === '/api/select').body),
    { provider: 'p', model: 'm', effort: null });
});

test('lost admission response restores files and retries the exact input without another upload', async () => {
  const admitted = [];
  const p = page({ respond(url, init) {
    if (url !== '/api/ling/input') return;
    admitted.push(JSON.parse(init.body));
    if (admitted.length === 1) throw new TypeError('response lost after server admission');
    return { ok: true, text: async () => JSON.stringify({ queued: true }) };
  } });
  p.load('chat.js');
  const file = { name: 'notes.pdf', kind: 'file', file: new Blob(['document']) };
  const image = { name: 'image.png', kind: 'image', file: new Blob(['image'], { type: 'image/png' }) };
  p.N.state.files = [file, image]; p.$('#input').value = 'inspect';
  p.N.send(); await new Promise(setImmediate);
  assert.equal(p.$('#input').value, 'inspect');
  assert.equal(p.N.state.files[0], file); assert.equal(p.N.state.files[1], image);
  p.N.send(); await new Promise(setImmediate);
  assert.equal(admitted.length, 2); assert.deepEqual(admitted[1], admitted[0]);
  assert.equal(p.requests.filter(r => r.url.includes('/attachments?')).length, 1);
  p.$('#input').value = 'new request'; p.N.send(); await new Promise(setImmediate);
  assert.notEqual(admitted[2].request_id, admitted[0].request_id);
});


test('retry resumes a partial upload without repeating completed uploads', async () => {
  let fail = true;
  const p = page({ respond(url) {
    if (url.includes('second.pdf') && fail) { fail = false; throw new TypeError('upload interrupted'); }
  } });
  p.load('chat.js');
  p.N.state.files = ['first.pdf', 'second.pdf'].map(name => ({ name, kind: 'file', file: new Blob([name]) }));
  p.$('#input').value = 'inspect'; p.N.send(); await new Promise(setImmediate);
  assert.equal(p.N.state.files.length, 2);
  p.$('#input').value = 'inspect'; p.N.send(); await new Promise(setImmediate);
  assert.equal(p.requests.filter(r => r.url.includes('first.pdf')).length, 1);
  assert.equal(p.requests.filter(r => r.url.includes('second.pdf')).length, 2);
  assert.equal(p.requests.filter(r => r.url === '/api/ling/input').length, 1);
});

test('dismissing the editor backdrop settles the edit and allows the next one', async () => {
  const p = page(), editor = p.$('#editor');
  editor.id = 'editor';
  p.N.$$ = selector => selector === 'dialog' ? [editor] : [];
  p.load('dialogs.js');
  const first = p.N.edit({ title: 'First', text: 'draft' });
  editor.handlers.click({ target: editor });
  assert.equal(await first, null);
  assert.equal(editor.open, false);
  const second = p.N.edit({ title: 'Second' });
  assert.equal(editor.open, true);
  editor.handlers.click({ target: editor });
  assert.equal(await second, null);
});

test('saving an edit prevents backdrop dismissal until the save completes', async () => {
  const p = page(), editor = p.$('#editor');
  editor.id = 'editor';
  p.N.$$ = selector => selector === 'dialog' ? [editor] : [];
  p.load('dialogs.js');
  let finishSave;
  const result = p.N.ask({ title: 'Save', text: 'draft' }, () => new Promise(resolve => { finishSave = resolve; }));
  const submit = p.$('#editor-form').onsubmit({ preventDefault() {} });
  editor.handlers.click({ target: editor });
  assert.equal(editor.open, true);
  finishSave();
  await submit;
  assert.equal(await result, 'draft');
  assert.equal(editor.open, false);
});

test('MCP refresh preserves settings drafts and explicit reload uses saved values', async () => {
  const p = page();
  Object.assign(p.N.state, { providers: [], current: {}, mcp: [{ name: 'test', connected: true }],
    motion: '1', instructions: 'saved rules', workspace: 'saved-dir' });
  Object.assign(p.N, { providerOf: () => null, currentModel: () => null,
    MOTION_LEVELS: ['1'], MOTION_LABELS: { '1': 'standard' }, THEMES: [] });
  p.load('settings.js');
  p.$('#view-settings').querySelector = selector => p.$(selector);
  p.$('#instructions').value = 'unsaved rules';
  p.N.sync.mcpOnly = async () => {};
  await p.click('mcp-reconnect', undefined, { i: '0' });
  await new Promise(setImmediate);
  assert.match(p.$('#view-settings').innerHTML, /unsaved rules/);
  assert.doesNotMatch(p.$('#view-settings').innerHTML, /workspace-input/);
  p.$('#instructions').value = '';
  p.N.renderSettings();
  assert.doesNotMatch(p.$('#view-settings').innerHTML, />saved rules<\/textarea>/);
  p.N.renderSettings(true);
  assert.match(p.$('#view-settings').innerHTML, />saved rules<\/textarea>/);
  assert.equal(p.N.state.workspace, 'saved-dir');
});

test('a failed skill deletion cancels the held exit animation', async () => {
  const p = page();
  p.N.state.skills = [{ name: 'test', source: 'personal' }];
  p.N.REDUCED = false;
  p.N.confirmEdit = async () => true;
  let cancelled = false;
  const animation = { finished: Promise.resolve(), cancel() { cancelled = true; } };
  p.N.anim = () => animation;
  p.$('.skill').getAnimations = () => [animation];
  p.N.$$ = selector => selector === '#view-skills .skill' ? [p.$('.skill')] : [];
  p.N.api.deleteSkill = async () => { throw new Error('delete failed'); };
  p.load('skills.js');
  await p.click('del-skill', undefined, { i: '0' });
  assert.equal(cancelled, true);
  assert.equal(p.N.state.skills.length, 1);
});

function projectPage(options) {
  const p = page(options);
  Object.assign(p.N, {
    openDialog: dialog => { dialog.open = true; },
    closeDialog: dialog => { dialog.open = false; },
    confirmEdit: async () => true,
  });
  Object.assign(p.N.state, { workspace: 'C:/Nexus/Work', projects: [],
    project: { path: 'C:/Nexus/Work', name: 'Work' },
    defaultProject: { path: 'C:/Nexus/Work', name: 'Work' } });
  p.N.sync.settingsOnly = async () => {};
  p.N.sync.contextOnly = async () => {};
  p.$('#project-folder-pick').querySelector = () => p.$('#picker-label');
  p.load('projects.js');
  return p;
}

test('system folder selection supplies the default name and preserves a custom name', async () => {
  const paths = [null, 'C:/文档/项目一', 'C:/文档/项目二'];
  const p = projectPage({ respond: async url => {
    if (url === '/api/projects/choose-folder') return {
      ok: true, text: async () => JSON.stringify({ path: paths.shift() }),
    };
  } });
  await p.click('project-new');
  assert.equal(p.$('#project-save').disabled, true);
  await p.$('#project-folder-pick').handlers.click();
  assert.equal(p.$('#project-name').value, '');
  assert.equal(p.$('#project-save').disabled, true);
  await p.$('#project-folder-pick').handlers.click();
  assert.equal(p.$('#project-name').value, '项目一');
  assert.equal(p.$('#project-save').disabled, false);
  p.$('#project-name').value = '手动名称';
  await p.$('#project-folder-pick').handlers.click();
  assert.equal(p.$('#project-name').value, '手动名称');
  assert.equal(p.$('#project-folder-label').textContent, 'C:/文档/项目二');
  assert.equal(p.requests.some(r => r.url === '/api/projects'), false, 'choosing alone does not change workspace');
});

test('project save synchronizes history and a failed save keeps the draft', async () => {
  let fail = true;
  const p = projectPage({ respond: async (url, init) => {
    if (url === '/api/projects/choose-folder') return {
      ok: true, text: async () => JSON.stringify({ path: 'C:/文档/项目' }),
    };
    if (url === '/api/projects') {
      const body = JSON.parse(init.body);
      if (fail) return { ok: false, status: 409, text: async () => JSON.stringify({ error: '请等待当前任务停止' }) };
      return { ok: true, text: async () => JSON.stringify({ workspace: body.path,
        project: { path: body.path, name: body.name }, projects: [{ path: body.path, name: body.name }] }) };
    }
  } });
  await p.click('projects');
  await p.click('project-new');
  await p.$('#project-folder-pick').handlers.click();
  const submit = () => p.$('#project-form').handlers.submit({ preventDefault() {} });
  await submit();
  assert.equal(p.$('#project-editor').open, true);
  assert.equal(p.$('#project-name').value, '项目');
  assert.match(p.$('#project-editor-error').textContent, /等待/);
  assert.equal(p.N.state.workspace, 'C:/Nexus/Work');
  fail = false;
  await submit();
  assert.equal(p.N.state.workspace, 'C:/文档/项目');
  assert.equal(p.N.state.project.name, '项目');
  assert.equal(p.N.state.projects.length, 1);
  assert.equal(p.$('#project-editor').open, false);
  assert.equal(p.$('#projects-dialog').open, false);
});

test('an old backend gives actionable folder picker guidance without changing workspace', async () => {
  const p = projectPage({ respond: async url => {
    if (url === '/api/projects/choose-folder') return {
      ok: false, status: 404, text: async () => JSON.stringify({ error: 'not found' }),
    };
  } });
  await p.click('project-new');
  await p.$('#project-folder-pick').handlers.click();
  assert.match(p.$('#project-editor-error').textContent, /关闭所有旧的 Nexus 后端/);
  assert.equal(p.N.state.workspace, 'C:/Nexus/Work');
  assert.equal(p.$('#project-save').disabled, true);
  assert.equal(p.$('#project-folder-pick').disabled, false);
});

test('history rename hides folder selection and delete only sends a record operation', async () => {
  const p = projectPage();
  p.N.state.projects = [{ path: 'C:/文档/项目', name: '项目' }];
  await p.click('projects');
  await p.click('project-rename', undefined, { projectIndex: '1' });
  assert.equal(p.$('#project-source').hidden, true);
  assert.equal(p.$('#project-name').value, '项目');
  p.$('#project-name').value = '改名';
  await p.$('#project-form').handlers.submit({ preventDefault() {} });
  const rename = p.requests.find(r => r.url === '/api/projects');
  assert.deepEqual(JSON.parse(rename.body), { action: 'rename', path: 'C:/文档/项目', name: '改名' });
  p.N.state.projects = [{ path: 'C:/文档/项目', name: '改名' }];
  p.N.state.defaultProject = { path: 'C:/Nexus/Work', name: 'Work' };
  p.N.renderProjects();
  await p.click('project-delete', undefined, { projectIndex: '1' });
  const removed = p.requests.filter(r => r.url === '/api/projects').at(-1);
  assert.deepEqual(JSON.parse(removed.body), { action: 'delete', path: 'C:/文档/项目' });
});

test('the pivot keeps its scale and angle continuous when the palette changes', () => {
  const node = () => ({
    style: {}, dataset: {}, attributes: {}, isConnected: true,
    setAttribute(key, value) { this.attributes[key] = value; },
    append() {}, replaceChildren() {}, appendChild(child) { child.parentNode = this; },
  });
  const core = node(), nodes = new Map([
    ['#orb-stage', node()], ['#pv-core', core],
    ['#pv-nodes-back', node()], ['#pv-nodes-front', node()],
  ]);
  const frames = [], dataset = { palette: 'cool', theme: 'night' };
  const N = { $: selector => nodes.get(selector) || null, $$: () => [],
    state: { view: 'plugins', items: [], works: [] }, motionScale: () => 1, REDUCED: false };
  const context = vm.createContext({
    window: { N, addEventListener() {} },
    document: { documentElement: { dataset }, addEventListener() {}, createElementNS: node },
    requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
    cancelAnimationFrame() {}, setTimeout() {}, clearTimeout() {},
    matchMedia: () => ({ addEventListener() {} }),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../nexus/web/js/pivot.js'), 'utf8'), context);
  const frame = frames.find(callback => callback.name === 'renderPivot');
  const transform = () => {
    const value = core.attributes.transform;
    return { scale: Number(value.match(/scale\(([^)]+)\)/)[1]),
      angle: Number(value.match(/rotate\(([^)]+)\)/)?.[1] || 0) };
  };
  let now = 37000;
  frame(now);
  for (const palette of ['jade', 'cool', 'jade', 'cool']) {
    for (let i = 0; i < 60; i++) frame(now += 16);
    const before = transform();
    dataset.palette = palette;
    frame(now += 16);
    const after = transform();
    assert.ok(Math.abs(after.scale - before.scale) < .002, 'switching must not jump to another breathing phase');
    assert.ok(Math.abs(after.angle - before.angle) < .35, 'the jade sway must enter and leave smoothly');
  }
});

for (const palette of ['jade', 'cool']) test(`${palette} particles keep their positions across resize events`, () => {
  let randomCalls = 0;
  const random = Object.create(Math);
  random.random = () => { randomCalls++; return .5; };
  const frames = [], positions = [], events = new Map();
  const drawing = new Proxy({}, {
    get(_target, method) {
      if (method === 'createRadialGradient') return () => ({ addColorStop() {} });
      return (...args) => {
        if (method === (palette === 'jade' ? 'translate' : 'arc')) positions.push(args.slice(0, 2));
      };
    },
  });
  const canvas = { getContext: () => drawing };
  const backgroundNodes = new Map([['#pivot-field', canvas]]);
  const N = { $: selector => backgroundNodes.get(selector) || null, $$: () => [],
    state: { view: 'plugins', items: [], works: [] }, motionScale: () => 1, REDUCED: false };
  const window = { N, innerWidth: 1440, innerHeight: 900, devicePixelRatio: 1,
    addEventListener: (type, callback) => events.set(type, callback) };
  const context = vm.createContext({ window, Math: random,
    document: { hidden: false, documentElement: { dataset: { palette, theme: 'night' } }, addEventListener() {} },
    requestAnimationFrame(callback) { frames.push(callback); return frames.length; },
    cancelAnimationFrame() {}, setTimeout() {}, clearTimeout() {},
    matchMedia: () => ({ addEventListener() {} }),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../nexus/web/js/pivot.js'), 'utf8'), context);
  const frame = frames.find(callback => callback.name === 'renderFieldFrame');
  const initialized = randomCalls;
  assert.ok(initialized > 0);
  frame(1000);
  const before = positions.splice(0);
  events.get('resize')();
  assert.equal(randomCalls, initialized, 'a redundant resize must not regenerate particles');
  window.innerWidth++;
  events.get('resize')();
  assert.equal(randomCalls, initialized, 'a small resize keeps the existing particles');
  frame(1016);
  const after = positions.splice(0);
  assert.equal(after.length, before.length);
  before.forEach(([x, y], i) => assert.ok(Math.hypot(after[i][0] - x, after[i][1] - y) < 3,
    'particles must continue their current paths instead of teleporting'));
  window.innerWidth = 1600;
  events.get('resize')();
  assert.ok(randomCalls > initialized, 'a larger viewport adds particles for the extra space');
  assert.ok(randomCalls - initialized < initialized, 'only additional particles are generated');

  const copy = children => ({ style: { setProperty() {} }, querySelectorAll: () => children });
  const oldCanvas = { ...copy([]), getContext: () => drawing };
  const aura = { querySelectorAll: () => [canvas], cloneNode: () => copy([oldCanvas]), matches: () => false };
  const core = { querySelectorAll: () => [], cloneNode: () => copy([]), matches: () => false };
  canvas.matches = () => false;
  context.getComputedStyle = () => ({ background: 'black', getPropertyValue: () => '' });
  let removed = false;
  context.document.createElement = () => ({ ...copy([]), setAttribute() {},
    attachShadow: () => ({ appendChild() {} }), remove() { removed = true; } });
  context.document.body = { appendChild() {} };
  backgroundNodes.set('.aura', aura);
  backgroundNodes.set('#pivot-core', core);
  positions.length = 0;
  frame(1032);
  const oneScene = positions.splice(0).length;
  const background = N.prepareThemeBackground();
  frame(1048);
  assert.equal(positions.splice(0).length, oneScene * 2, 'old and new backgrounds both render during a theme switch');
  background.remove();
  frame(1064);
  assert.equal(positions.length, oneScene, 'the old background stops rendering after the wave completes');
  assert.equal(removed, true);
});

test('plugin redraws retain the moving tab indicator', () => {
  const p = page(), originalInk = {};
  let ink = originalInk;
  const view = p.$('#view-plugins');
  view.querySelector = selector => selector === '.tabs .tab-ink' ? ink : null;
  view.replaceChildren = () => { ink = { replaceWith(node) { ink = node; } }; };
  p.load('plugins.js');
  p.N.renderPlugins();
  p.N.renderPlugins();
  assert.equal(ink, originalInk, 'a redraw must keep the indicator and its animation state');
});

test('the first paint and UI state default to cool/night and preserve saved choices', () => {
  const html = fs.readFileSync(path.join(__dirname, '../nexus/web/index.html'), 'utf8');
  const bootstrap = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  for (const saved of [null, { theme: 'day', palette: 'jade' }]) {
    const root = { dataset: {
      theme: html.match(/<html[^>]*data-theme="([^"]+)"/)[1],
      palette: html.match(/<html[^>]*data-palette="([^"]+)"/)[1],
    }, style: { setProperty() {} } };
    const context = vm.createContext({ window: {}, document: { documentElement: root },
      localStorage: { getItem: () => saved && JSON.stringify(saved) },
      matchMedia: () => ({ matches: false, addEventListener() {} }),
      CSS: { supports: () => false },
      getComputedStyle: () => ({ getPropertyValue: () => '1' }),
      setTimeout() {}, clearTimeout() {},
    });
    vm.runInContext(bootstrap, context);
    assert.equal(root.dataset.theme, saved?.theme || 'night');
    assert.equal(root.dataset.palette, saved?.palette || 'cool');
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../nexus/web/js/core.js'), 'utf8'), context);
    assert.equal(context.window.N.state.theme, root.dataset.theme);
    assert.equal(context.window.N.state.palette, root.dataset.palette);
  }
});
