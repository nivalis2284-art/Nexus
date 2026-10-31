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
    replaceChildren() {}, prepend() {}, appendChild() {}, remove() {}, scrollTo() {},
    showModal() { this.open = true; },
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
    documentElement: { dataset: {} },
    createElement: element, querySelector: () => null,
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(callback);
    },
  };
  const context = vm.createContext({
    window: { N }, document, console, URL, URLSearchParams, AbortController,
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
    async timers() {
      const pending = [...timers.values()];
      timers.clear();
      for (const callback of pending) await callback();
    },
    async click(act, id, data = {}, disabled = false) {
      const button = { dataset: { act, id, ...data }, disabled };
      for (const callback of listeners.get('click') || []) {
        await callback({ target: { closest: () => button } });
      }
      return button;
    },
  };
}

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
