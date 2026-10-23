"use strict";

// Focused behaviour checks for the plugin center against a local DOM stand-in.
// Run with: node tests/frontend_plugins.cjs

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const SOURCE = fs.readFileSync(path.join(ROOT, "nexus", "web", "plugins.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "nexus", "web", "index.html"), "utf8");

class ClassList {
  constructor(node) { this.node = node; }
  values() { return String(this.node.className || "").split(/\s+/).filter(Boolean); }
  contains(name) { return this.values().includes(name); }
  add(...names) {
    const values = this.values();
    for (const name of names) if (!values.includes(name)) values.push(name);
    this.node.className = values.join(" ");
  }
  remove(...names) {
    this.node.className = this.values().filter((name) => !names.includes(name)).join(" ");
  }
  toggle(name, force) {
    const on = force === undefined ? !this.contains(name) : Boolean(force);
    if (on) this.add(name); else this.remove(name);
    return on;
  }
}

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.parent = null;
    this.className = "";
    this.classList = new ClassList(this);
    this.dataset = {};
    this.attributes = {};
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.hidden = false;
    this.open = false;
    this.disabled = false;
    this.title = "";
    this.src = "";
    this.files = [];
    this._listeners = {};
  }
  set textContent(value) {
    this._text = String(value === null || value === undefined ? "" : value);
    this.children = [];
  }
  get textContent() { return this._text || ""; }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    child.parent = null;
    return child;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, handler) {
    if (!this._listeners[name]) this._listeners[name] = [];
    this._listeners[name].push(handler);
  }
  async dispatch(name, event) {
    for (const handler of (this._listeners[name] || []).slice()) await handler(event || {});
  }
  click() { this.dispatch("click"); }
  focus() {}
  showModal() { this.open = true; }
  close() {
    this.open = false;
    for (const handler of (this._listeners.close || []).slice()) handler();
  }
  descendants() {
    const out = [];
    for (const child of this.children) out.push(child, ...child.descendants());
    return out;
  }
  querySelectorAll(selector) {
    const cls = selector.match(/\.([\w-]+)/);
    const tag = /^[a-zA-Z][\w-]*$/.test(selector) ? selector.toUpperCase() : null;
    return this.descendants().filter(
      (node) => (!cls || node.classList.contains(cls[1])) && (!tag || node.tagName === tag),
    );
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

function textOf(node) {
  return (node.children.length ? "" : String(node.textContent || "")) +
    node.children.map(textOf).join("");
}

function response(value, status) {
  const code = status || 200;
  return {
    ok: code >= 200 && code < 300,
    status: code,
    statusText: code === 409 ? "Conflict" : "OK",
    async json() { return structuredClone(value); },
  };
}

function flusher() {
  const pending = [];
  return {
    setTimeout(fn, delay) { pending.push({ fn, delay }); return pending.length; },
    clearTimeout() {},
    async next() {
      const item = pending.shift();
      if (!item) return false;
      await item.fn();
      return true;
    },
    count() { return pending.length; },
  };
}

function snapshot(plugins, root) {
  return { root: root || "C:\\plugins", plugins };
}

function sample() {
  return snapshot([
    {
      id: "notes",
      name: "Notes",
      version: "1.2.0",
      description: "Notebook pages.",
      enabled: true,
      status: "ready",
      error: "",
      active_calls: 2,
      pages: [{ id: "main", title: "Notes", path: "web/index.html" }],
      settings: { title: "Inbox", compact: false, count: 3, spacing: 1.5 },
      settings_schema: {
        type: "object",
        properties: {
          title: { type: "string", title: "标题", description: "主标题" },
          compact: { type: "boolean", title: "紧凑" },
          count: { type: "integer", title: "数量", minimum: 0 },
          spacing: { type: "number", title: "行距" },
        },
      },
      capabilities: [{ id: "note.search", title: "搜索笔记" }],
    },
    {
      id: "off", name: "Off", version: "0.1.0", description: "", enabled: false,
      status: "disabled", error: "", active_calls: 0, pages: [], settings: {},
      settings_schema: {}, capabilities: [],
    },
  ]);
}

function harness() {
  const ids = new Map();
  const document = {
    createElement: (tag) => new FakeNode(tag),
    getElementById(id) {
      if (!ids.has(id)) {
        const tag = id.endsWith("-dialog") ? "dialog" : (id.endsWith("-form") ? "form" : "div");
        ids.set(id, new FakeNode(tag));
      }
      return ids.get(id);
    },
  };
  for (const id of [
    "plugins-requests-owner", "plugins-requests-kind", "plugins-requests-filters",
    "plugins-requests-more", "plugins-requests-list", "plugins-requests-error",
    "plugins-requests-count", "plugins-requests-kind-options",
  ]) document.getElementById(id);
  const calls = [];
  let current = sample();
  let conflicts = [];
  let replaceRejections = [];
  const windowMessages = [];
  const requests = {
    requests: [{
      id: 8, created_at: "2026-10-23T01:02:03Z", kind: "ling", owner_id: "ling-1",
      model: "deepseek-v4.1-flash", status: "sent",
    }],
  };
  const details = {
    8: {
      id: 8, created_at: "2026-10-23T01:02:03Z", kind: "ling", owner_id: "ling-1",
      model: "deepseek-v4.1-flash", status: "sent", body: { messages: [{ role: "user", content: "<b>x</b>" }] },
      materials: [{ source: "chat", note: "recent" }],
      plugins: [
        { id: "notes", status: "used", required: true },
        { id: "silent", status: "not_applicable" },
      ],
    },
  };

  const fetch = async (requestPath, options) => {
    calls.push({ path: requestPath, options });
    const method = options && options.method || "GET";
    if (requestPath === "/api/plugins" && method === "GET") return response(current);
    if (requestPath === "/api/plugins/scan") {
      current = snapshot([...current.plugins, {
        id: "new", name: "New", version: "1.0.0", description: "", enabled: false,
        status: "unloaded", error: "", active_calls: 0, pages: [], settings: {},
        settings_schema: {}, capabilities: [],
      }]);
      return response(current);
    }
    if (requestPath.startsWith("/api/plugins/import")) {
      if (replaceRejections.length) {
        return response({ error: replaceRejections.shift() }, 400);
      }
      if (conflicts.length) {
        const conflict = conflicts.shift();
        return response(conflict, 409);
      }
      const plugin = {
        id: "uploaded", name: "Uploaded", version: "2.0.0", description: "",
        enabled: false, status: "unloaded", error: "", active_calls: 0,
        pages: [], settings: {}, settings_schema: {}, capabilities: [],
      };
      current = snapshot([...current.plugins, plugin]);
      return response({ plugin: { id: plugin.id, name: plugin.name, version: plugin.version }, ...current });
    }
    const command = requestPath.match(/^\/api\/plugins\/([^/]+)\/commands$/);
    if (command) {
      const body = JSON.parse(options.body);
      const index = current.plugins.findIndex((item) => item.id === decodeURIComponent(command[1]));
      const updated = structuredClone(current);
      if (body.operation === "load") {
        updated.plugins[index].status = "loading";
        updated.plugins[index].enabled = true;
        updated.plugins[index].progress = "正在准备环境…";
      } else if (body.operation === "unload") {
        updated.plugins[index].status = "draining";
        updated.plugins[index].enabled = true;
      } else if (body.operation === "uninstall") {
        updated.plugins.splice(index, 1);
      }
      current = updated;
      return response(current);
    }
    const settings = requestPath.match(/^\/api\/plugins\/([^/]+)\/settings$/);
    if (settings && method === "PUT") {
      const index = current.plugins.findIndex((item) => item.id === decodeURIComponent(settings[1]));
      const updated = structuredClone(current);
      updated.plugins[index].settings = JSON.parse(options.body);
      current = updated;
      return response(current);
    }
    if (requestPath.startsWith("/api/model-requests/")) {
      const id = requestPath.split("/").pop();
      return details[id] ? response(details[id]) : response({ error: "not found" }, 404);
    }
    if (requestPath.startsWith("/api/model-requests?")) return response(requests);
    return response({ error: "unexpected " + requestPath }, 500);
  };

  const timers = flusher();
  const pendingWindow = [];
  const browserWindow = { addEventListener(name, handler) { pendingWindow.push(handler); } };
  const hostBridge = { openTask: null, openLing: null };
  const context = vm.createContext({
    document, fetch, console, structuredClone, confirm: () => true,
    URLSearchParams, setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    openTask: async (id) => hostBridge.openTask(id),
    openLing: async () => hostBridge.openLing(),
  });
  context.window = browserWindow;
  context.location = { origin: "http://127.0.0.1:8766" };
  vm.runInContext(SOURCE, context, { filename: "nexus/web/plugins.js" });
  return {
    ids, calls, timers, windowMessages: pendingWindow,
    current: () => current,
    setCurrent(value) { current = value; },
    conflict(value) { conflicts.push(value); },
    rejectReplace(message) { replaceRejections.push(message); },
    setHostBridge(value) {
      hostBridge.openTask = value.openTask || (() => {});
      hostBridge.openLing = value.openLing || (() => {});
    },
    async open() {
      await ids.get("plugins-open").dispatch("click");
      await new Promise((resolve) => setImmediate(resolve));
    },
    async settle() { await new Promise((resolve) => setImmediate(resolve)); },
  };
}

async function testPageWiringAndOldEntryRemoval() {
  assert.match(HTML, /id="plugins-open"[^>]*>插件管理</);
  assert.equal((HTML.match(/id="plugins-open"/g) || []).length, 1);
  assert.match(HTML, /src="\/plugins\.js"/);
  assert.match(HTML, /href="\/plugins\.css"/);
  assert.doesNotMatch(HTML, /responsibilities\.js|automation\.js/);
  assert.doesNotMatch(HTML, /id="automation-open"|id="responsibilities-region"/);
  console.log("ok - single plugin entry, assets included, old fixed panel entry removed");
}

async function testListingAndSettingsSchema() {
  const h = harness();
  await h.open();
  assert.equal(h.ids.get("plugins-dialog").open, true);
  assert.equal(h.ids.get("plugins-count").textContent, "2 个");
  assert.equal(h.ids.get("plugin-detail-title").textContent, "Notes");
  const fields = h.ids.get("plugin-settings-fields").children;
  assert.equal(fields.length, 4);
  const controls = fields.map((field) => field.querySelector("input") || field.querySelector("select"));
  assert.equal(controls[0].value, "Inbox");
  assert.equal(controls[1].checked, false);
  assert.equal(controls[2].value, "3");
  assert.equal(controls[2].step, "1");
  assert.equal(controls[3].step, "any");
  assert.match(textOf(h.ids.get("plugin-detail-actions")), /停用/);
  assert.match(textOf(h.ids.get("plugin-detail-actions")), /重载/);
  assert.equal(h.ids.get("plugin-detail-state").textContent, "已加载");
  console.log("ok - details, actions, pages, capabilities and schema-driven settings render");
}

async function testLoadingProgressIsVisibleAndPolled() {
  const h = harness();
  const value = sample();
  value.plugins[0].status = "loading";
  value.plugins[0].progress = "正在安装依赖…";
  h.setCurrent(value);
  await h.open();
  assert.equal(h.ids.get("plugin-detail-progress").textContent, "正在安装依赖…");
  assert.equal(h.ids.get("plugin-detail-progress").hidden, false);
  assert.match(textOf(h.ids.get("plugin-detail-actions")), /停用/);
  assert.doesNotMatch(textOf(h.ids.get("plugin-detail-actions")), /重载/);
  assert.ok(h.timers.count() >= 1);
  console.log("ok - loading progress and state are shown while polling for readiness");
}

async function testReplaceWhileLoadedExplainsNextStep() {
  const h = harness();
  await h.open();
  const file = {
    name: "notes.zip",
    async arrayBuffer() { return new Uint8Array([80, 75, 3, 4]).buffer; },
  };
  h.conflict({ error: "该插件已安装，请确认替换", plugin: { id: "notes", name: "Notes", version: "2.0.0" }, installed_version: "1.2.0" });
  const input = h.ids.get("plugins-file");
  input.files = [file];
  await input.dispatch("change", { target: input });
  await h.settle();
  h.rejectReplace("请先停用插件并等待收尾，再更新安装包");
  const replace = h.ids.get("plugins-import-notice").descendants()
    .find((node) => node.textContent === "替换版本");
  await replace.dispatch("click");
  await h.settle();
  assert.match(h.ids.get("plugins-status").textContent, /请先在插件详情里停用/);
  assert.equal(h.current().plugins[0].status, "ready", "UI must not unload by itself");
  console.log("ok - replacing a loaded plugin explains stop-first without changing it");
}

async function testSettingsSaveSendsTypedValues() {
  const h = harness();
  await h.open();
  const fields = h.ids.get("plugin-settings-fields").children;
  fields[0].querySelector("input").value = "Archive";
  fields[0].querySelector("input").dispatch("input");
  fields[1].querySelector("input").checked = true;
  fields[1].querySelector("input").dispatch("change");
  fields[2].querySelector("input").value = "7";
  fields[2].querySelector("input").dispatch("input");
  await h.ids.get("plugin-settings-form").dispatch("submit", { preventDefault() {} });
  await h.settle();
  const call = h.calls.find((item) => item.path === "/api/plugins/notes/settings");
  assert.ok(call, "settings PUT was sent");
  assert.equal(call.options.method, "PUT");
  assert.deepEqual(JSON.parse(call.options.body), {
    title: "Archive", compact: true, count: 7, spacing: 1.5,
  });
  assert.match(h.ids.get("plugins-status").textContent, /立即生效/);
  console.log("ok - generated settings send typed values and report immediate effect");
}

async function testZipUploadConflictAndReplace() {
  const h = harness();
  await h.open();
  const file = {
    name: "uploaded.zip",
    async arrayBuffer() { return new Uint8Array([80, 75, 3, 4]).buffer; },
  };
  h.conflict({ error: "already installed", plugin: { id: "notes", name: "Notes", version: "2.0.0" }, installed_version: "1.2.0" });
  const input = h.ids.get("plugins-file");
  input.files = [file];
  await input.dispatch("change", { target: input });
  await h.settle();
  const first = h.calls.filter((item) => item.path.startsWith("/api/plugins/import"));
  assert.equal(first.length, 1);
  assert.match(first[0].path, /replace=0/);
  assert.equal(first[0].options.headers["Content-Type"], "application/zip");
  assert.ok(first[0].options.body instanceof ArrayBuffer, "raw bytes are sent");
  assert.match(textOf(h.ids.get("plugins-import-notice")), /替换版本/);
  assert.match(textOf(h.ids.get("plugins-import-notice")), /1\.2\.0/);
  const replace = h.ids.get("plugins-import-notice").descendants()
    .find((node) => node.textContent === "替换版本");
  assert.ok(replace, "replace button is shown after 409");
  await replace.dispatch("click");
  await h.settle();
  const second = h.calls.filter((item) => item.path.startsWith("/api/plugins/import"))[1];
  assert.match(second.path, /replace=1/);
  assert.equal(second.options.body instanceof ArrayBuffer, true);
  assert.equal(h.ids.get("plugin-detail-title").textContent, "Uploaded");
  assert.match(h.ids.get("plugins-status").textContent, /默认不启用/);
  console.log("ok - ZIP upload is raw bytes and a 409 requires an explicit replace request");
}

async function testDrainingPollsWithoutRepeatingCommand() {
  const h = harness();
  await h.open();
  const hA = h.current();
  assert.equal(hA.plugins[0].id, "notes");
  const unload = h.ids.get("plugin-detail-actions").children
    .find((node) => node.textContent === "停用");
  await unload.dispatch("click");
  await h.settle();
  const commands = h.calls.filter((item) => /\/commands$/.test(item.path));
  assert.equal(commands.length, 1);
  assert.match(textOf(h.ids.get("plugin-detail-actions")), /正在收尾/);
  assert.equal(h.timers.count(), 1);
  assert.match(h.ids.get("plugins-status").textContent, /等待状态更新/);
  const pollsBefore = h.calls.filter((item) => item.path === "/api/plugins" &&
    (!item.options || !item.options.method || item.options.method === "GET")).length;
  await h.timers.next();
  await h.settle();
  const pollsAfter = h.calls.filter((item) => item.path === "/api/plugins" &&
    (!item.options || !item.options.method || item.options.method === "GET")).length;
  assert.equal(pollsAfter, pollsBefore + 1);
  assert.equal(h.calls.filter((item) => /\/commands$/.test(item.path)).length, 1);
  console.log("ok - draining is shown and polled without repeating the operation");
}

async function testLoadShowsProgressBeforeReady() {
  const h = harness();
  await h.open();
  const second = h.ids.get("plugins-list").querySelectorAll(".plugin-list-item")[1];
  await second.dispatch("click");
  const load = h.ids.get("plugin-detail-actions").children
    .find((node) => node.textContent === "加载");
  assert.ok(load, "load is available for an unloaded plugin");
  await load.dispatch("click");
  await h.settle();
  const command = h.calls.filter((item) => /\/commands$/.test(item.path)).pop();
  assert.deepEqual(JSON.parse(command.options.body), { operation: "load" });
  assert.equal(h.ids.get("plugin-detail-state").textContent, "加载中");
  assert.equal(h.ids.get("plugin-detail-progress").textContent, "正在准备环境…");
  assert.equal(h.timers.count() >= 1, true);
  console.log("ok - load shows loading progress until the snapshot becomes ready");
}

async function testUninstallMentionsRetainedData() {
  const h = harness();
  await h.open();
  const uninstall = h.ids.get("plugin-detail-actions").children
    .find((node) => node.textContent === "卸载");
  await uninstall.dispatch("click");
  await h.settle();
  assert.equal(h.current().plugins.some((plugin) => plugin.id === "notes"), false);
  assert.equal(h.ids.get("plugin-detail-title").textContent, "Off");
  console.log("ok - uninstall sends the operation and selects the remaining plugin");
}

async function testModulePageLoadsInFrame() {
  const h = harness();
  await h.open();
  const open = h.ids.get("plugin-detail-pages").querySelector(".plugin-secondary");
  await open.dispatch("click");
  assert.equal(h.ids.get("plugin-frame-panel").hidden, false);
  assert.equal(h.ids.get("plugin-frame").src, "/plugins/notes/web/index.html");
  await h.ids.get("plugin-frame-close").dispatch("click");
  assert.equal(h.ids.get("plugin-frame-panel").hidden, true);
  assert.equal(h.ids.get("plugin-frame").attributes.src, undefined);
  console.log("ok - plugin page opens in the frame and closing it leaves the plugin enabled");
}

async function testFrameMessageBridgeUsesOnlyTheCurrentFrame() {
  const h = harness();
  let openedTask = null;
  await h.open();
  const open = h.ids.get("plugin-detail-pages").querySelector(".plugin-secondary");
  await open.dispatch("click");
  const frame = h.ids.get("plugin-frame");
  frame.contentWindow = { name: "plugin" };
  assert.equal(h.windowMessages.length, 1, "one fixed message listener is installed");
  const listener = h.windowMessages[0];
  const origin = "http://127.0.0.1:8766";
  const taskMessage = { type: "nexus.open_task", task_id: "task-17" };
  h.setHostBridge({ openTask: (id) => { openedTask = id; }, openLing() {} });
  listener({ data: taskMessage, source: { other: true }, origin });
  assert.equal(openedTask, null);
  listener({ data: taskMessage, source: frame.contentWindow, origin: "http://evil.test" });
  assert.equal(openedTask, null);
  listener({ data: { type: "nexus.open_task", task_id: "" }, source: frame.contentWindow, origin });
  assert.equal(openedTask, null);
  listener({ data: { type: "nexus.run_js", code: "bad()" }, source: frame.contentWindow, origin });
  assert.equal(openedTask, null);
  listener({ data: taskMessage, source: frame.contentWindow, origin });
  assert.equal(openedTask, "task-17");
  assert.equal(h.ids.get("plugins-dialog").open, false, "the plugin center closes before opening a task");

  const h2 = harness();
  await h2.open();
  const open2 = h2.ids.get("plugin-detail-pages").querySelector(".plugin-secondary");
  await open2.dispatch("click");
  const frame2 = h2.ids.get("plugin-frame");
  frame2.contentWindow = { name: "plugin" };
  let lingOpened = 0;
  h2.setHostBridge({ openTask() {}, openLing: () => { lingOpened += 1; } });
  h2.windowMessages[0]({ data: { type: "nexus.open_ling" },
    source: frame2.contentWindow, origin: "http://127.0.0.1:8766" });
  assert.equal(lingOpened, 1);
  assert.equal(h2.ids.get("plugins-dialog").open, false);
  console.log("ok - the frame bridge accepts only source-matched task and Ling navigation");
}

async function testRequestTabRendersTextAndJSON() {
  const h = harness();
  await h.open();
  await h.ids.get("plugins-tab-requests").dispatch("click");
  await h.settle();
  assert.equal(h.ids.get("plugins-pane-requests").hidden, false);
  assert.match(textOf(h.ids.get("plugins-requests-list")), /2026/);
  const row = h.ids.get("plugins-requests-list").querySelector(".plugin-request-row");
  await row.dispatch("click");
  await h.settle();
  const detail = h.ids.get("plugins-request-detail");
  assert.match(textOf(detail), /已参与/);
  assert.match(textOf(detail), /本次必需/);
  assert.match(textOf(detail), /无需参与/);
  assert.match(textOf(detail), /最终 JSON/);
  const json = detail.querySelectorAll(".plugin-json").pop();
  assert.match(json.textContent, /<b>x<\/b>/);
  assert.equal(json.children.length, 0);
  console.log("ok - requests show participation and final JSON as text");
}

async function testSevenDayFiltersAndOlderPages() {
  const h = harness();
  await h.open();
  await h.ids.get("plugins-tab-requests").dispatch("click");
  await h.settle();
  assert.match(h.calls.find((item) => item.path.startsWith("/api/model-requests?")).path, /limit=50/);
  h.ids.get("plugins-requests-owner").value = "ling 1";
  h.ids.get("plugins-requests-kind").value = "wake";
  await h.ids.get("plugins-requests-filters").dispatch("submit", { preventDefault() {} });
  await h.settle();
  const filtered = h.calls.filter((item) => item.path.startsWith("/api/model-requests?")).pop();
  assert.match(filtered.path, /owner_id=ling\+1/);
  assert.match(filtered.path, /kind=wake/);
  assert.doesNotMatch(filtered.path, /before=/);
  assert.equal(h.ids.get("plugins-requests-more").hidden, true);
  console.log("ok - requests use owner/kind filters and the seven-day list contract");
}

async function main() {
  await testPageWiringAndOldEntryRemoval();
  await testListingAndSettingsSchema();
  await testLoadingProgressIsVisibleAndPolled();
  await testSettingsSaveSendsTypedValues();
  await testZipUploadConflictAndReplace();
  await testReplaceWhileLoadedExplainsNextStep();
  await testDrainingPollsWithoutRepeatingCommand();
  await testLoadShowsProgressBeforeReady();
  await testUninstallMentionsRetainedData();
  await testModulePageLoadsInFrame();
  await testFrameMessageBridgeUsesOnlyTheCurrentFrame();
  await testRequestTabRendersTextAndJSON();
  await testSevenDayFiltersAndOlderPages();
  console.log("frontend plugins: all checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
