"use strict";

// Focused editor checks: per-model windows preserve other model metadata.
// Run with: node tests/frontend_model_context.cjs

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const SOURCE = fs.readFileSync(path.join(ROOT, "nexus", "web", "app.js"), "utf8");
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
  toggle(name, force) { if (force) this.add(name); else this.remove(name); }
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
    this.style = {};
    this.textContent = "";
    this.innerHTML = "";
    this.value = "";
    this.checked = false;
    this.hidden = false;
    this.disabled = false;
    this.open = false;
    this._listeners = {};
  }
  set textContent(value) {
    this._text = String(value === null || value === undefined ? "" : value);
    this.children = [];
  }
  get textContent() { return this._text || ""; }
  set innerHTML(value) { this._html = String(value || ""); this.children = []; }
  get innerHTML() { return this._html || ""; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  removeChild(child) { const i = this.children.indexOf(child); if (i >= 0) this.children.splice(i, 1); return child; }
  addEventListener(name, handler) {
    if (!this._listeners[name]) this._listeners[name] = [];
    this._listeners[name].push(handler);
  }
  async dispatch(name, event) { for (const handler of (this._listeners[name] || []).slice()) await handler(event || {}); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  descendants() { const out = []; for (const child of this.children) out.push(child, ...child.descendants()); return out; }
  querySelectorAll(selector) {
    const cls = selector.match(/\.([\w-]+)/);
    return this.descendants().filter((node) => !cls || node.classList.contains(cls[1]));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  focus() {}
  select() {}
}

function response(value, status) {
  const code = status || 200;
  return {
    ok: code >= 200 && code < 300,
    status: code,
    statusText: "OK",
    async json() { return structuredClone(value); },
  };
}

function stateValue() {
  return {
    providers: [{
      id: "prov-1", name: "Local", family: "openai_chat_completions",
      base_url: "https://local.test/v1", has_key: true,
      models: [
        { id: "small", context_window_tokens: 32000, max_output_tokens: 2048,
          capabilities: { tools: true } },
        { id: "large", context_window_tokens: 200000, max_output_tokens: 8192 },
        { id: "third", context_window_tokens: 96000 },
      ],
    }],
    current: { provider: "prov-1", model: "small", effort: null },
    effort: { levels: [], labels: {}, current: null, mapping: null },
    sessions: [], permission: "ask", workspace: "/tmp",
    profile: { context_window_tokens: 32000 },
    context_window: 32000,
    context_limit: null,
  };
}

function harness() {
  const ids = new Map();
  const document = {
    createElement: (tag) => new FakeNode(tag),
    getElementById(id) {
      if (!ids.has(id)) ids.set(id, new FakeNode(id.endsWith("-dialog") ? "dialog" : "div"));
      return ids.get(id);
    },
    querySelectorAll: () => [],
    addEventListener() {}, readyState: "complete",
  };
  for (const id of ["p-models-list", "context-window-info"]) document.getElementById(id);
  const calls = [];
  let current = stateValue();
  const fetch = async (requestPath, options) => {
    calls.push({ path: requestPath, options });
    if (requestPath === "/api/state") return response(current);
    if (requestPath === "/api/providers/prov-1" && options.method === "PUT") {
      const payload = JSON.parse(options.body);
      const provider = current.providers[0];
      provider.name = payload.name;
      provider.family = payload.family;
      provider.base_url = payload.base_url;
      provider.models = payload.models;
      return response(current);
    }
    if (requestPath === "/api/select" && options.method === "POST") {
      const payload = JSON.parse(options.body);
      current.current = { provider: payload.provider, model: payload.model, effort: payload.effort };
      const selected = current.providers[0].models.find((model) => model.id === payload.model);
      current.context_window = (selected && selected.context_window_tokens) || 64000;
      return response(current);
    }
    return response({ error: "unexpected " + requestPath }, 500);
  };
  const context = vm.createContext({
    document, fetch, console, structuredClone, confirm: () => true, addEventListener() {},
    setTimeout, clearTimeout, location: { href: "http://127.0.0.1/" },
  });
  context.window = context;
  vm.runInContext(SOURCE, context, { filename: "nexus/web/app.js" });
  return { ids, calls, context, get current() { return current; },
    setCurrent(value) { current = value; } };
}

function models(h) { return h.ids.get("p-models-list").children; }
function nameInput(row) { return row.querySelector(".provider-model-name"); }
function windowInput(row) { return row.querySelector(".provider-model-window-input"); }

async function testHasModelRowsAndEffectiveWindow() {
  assert.match(HTML, /id="p-models-list"/);
  assert.match(HTML, /id="context-window-info"/);
  const h = harness();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.ids.get("context-window-info").textContent, /32,000/);
  assert.doesNotMatch(h.ids.get("context-window-info").textContent, /全局/);
  h.context.openProviderDialog(h.current.providers[0]);
  assert.equal(models(h).length, 3);
  assert.equal(nameInput(models(h)[0]).value, "small");
  assert.equal(windowInput(models(h)[0]).value, "32000");
  assert.equal(windowInput(models(h)[1]).value, "200000");
  console.log("ok - each model has its own window field and the effective budget is shown");
}

async function testDifferentWindowsAndMetadataSurviveRename() {
  const h = harness();
  await h.context.refresh();
  h.context.openProviderDialog(h.current.providers[0]);
  await nameInput(models(h)[0]).dispatch("input");
  nameInput(models(h)[0]).value = "smaller";
  await nameInput(models(h)[0]).dispatch("input");
  await h.context.saveProvider();
  const call = h.calls.find((item) => item.path === "/api/providers/prov-1");
  const payload = JSON.parse(call.options.body);
  assert.deepEqual(payload.models.map((model) => [model.id, model.context_window_tokens]), [
    ["smaller", 32000], ["large", 200000], ["third", 96000],
  ]);
  assert.equal(payload.models[0].max_output_tokens, 2048);
  assert.deepEqual(payload.models[0].capabilities, { tools: true });
  assert.equal(payload.models[1].max_output_tokens, 8192);
  assert.deepEqual(Object.keys(payload.models[0]).sort(), [
    "capabilities", "context_window_tokens", "id", "max_output_tokens",
  ]);
  console.log("ok - two separate windows and other model fields survive a rename");
}

async function testEmptyRemovesOverrideAndInvalidIsRejected() {
  const h = harness();
  h.context.openProviderDialog(h.current.providers[0]);
  const input = windowInput(models(h)[0]);
  input.value = "";
  await input.dispatch("input");
  await h.context.saveProvider();
  let payload = JSON.parse(h.calls.filter((item) => item.path === "/api/providers/prov-1").pop().options.body);
  assert.equal("context_window_tokens" in payload.models[0], false);
  assert.equal(payload.models[1].context_window_tokens, 200000);
  assert.equal(payload.models[2].context_window_tokens, 96000);

  h.context.openProviderDialog(h.current.providers[0]);
  const bad = windowInput(models(h)[0]);
  bad.value = "0";
  await bad.dispatch("input");
  const before = h.calls.length;
  await h.context.saveProvider();
  assert.equal(h.calls.length, before, "invalid input never reaches the server");
  assert.match(h.ids.get("provider-error").textContent, /正整数/);
  console.log("ok - clearing a field removes only that override and invalid values are blocked");
}

async function testGlobalOverrideIsExplainable() {
  const h = harness();
  const value = stateValue();
  value.context_window = 120000;
  value.context_limit = 120000;
  h.setCurrent(value);
  await h.context.refresh();
  assert.match(h.ids.get("context-window-info").textContent, /120,000/);
  assert.match(h.ids.get("context-window-info").textContent, /全局/);
  console.log("ok - a global override is described instead of pretending the model value applies");
}

async function testSelectedModelKeepsSelectionWhenRenamed() {
  const h = harness();
  await h.context.refresh();
  h.context.openProviderDialog(h.current.providers[0]);
  const input = nameInput(models(h)[0]);
  input.value = "smaller";
  await input.dispatch("input");
  await h.context.saveProvider();
  const putIndex = h.calls.findIndex((call) => call.path === "/api/providers/prov-1");
  const payload = JSON.parse(h.calls[putIndex].options.body);
  assert.deepEqual(payload.models.map((model) => model.id), ["smaller", "large", "third"]);
  const select = h.calls.find((call) => call.path === "/api/select");
  assert.ok(select, "the selected model window is re-read after the rename");
  assert.deepEqual(JSON.parse(select.options.body), {
    provider: "prov-1", model: "smaller", effort: null,
  });
  assert.ok(h.calls.indexOf(select) > putIndex, "selection is restored after the provider write");
  console.log("ok - renaming the selected model keeps it selected after the provider save");
}

async function testRemovingEarlierRowDoesNotReselectAnotherModel() {
  const h = harness();
  const value = stateValue();
  value.current = { provider: "prov-1", model: "large", effort: null };
  h.setCurrent(value);
  await h.context.refresh();
  h.context.openProviderDialog(h.current.providers[0]);
  const remove = models(h)[0].querySelector(".provider-model-remove");
  await remove.dispatch("click");
  await h.context.saveProvider();
  const put = h.calls.find((call) => call.path === "/api/providers/prov-1");
  const payload = JSON.parse(put.options.body);
  assert.deepEqual(payload.models.map((model) => model.id), ["large", "third"]);
  assert.equal(h.calls.some((call) => call.path === "/api/select"), false);
  assert.equal(h.current.current.model, "large");
  console.log("ok - deleting a row before the current model changes no selection");
}

async function testRenamingNonFirstRowAfterDeletionSelectsThatRow() {
  const h = harness();
  const value = stateValue();
  value.current = { provider: "prov-1", model: "large", effort: null };
  h.setCurrent(value);
  await h.context.refresh();
  h.context.openProviderDialog(h.current.providers[0]);
  const remove = models(h)[0].querySelector(".provider-model-remove");
  await remove.dispatch("click");
  const input = nameInput(models(h)[0]);
  input.value = "renamed-large";
  await input.dispatch("input");
  await h.context.saveProvider();
  const select = h.calls.find((call) => call.path === "/api/select");
  assert.ok(select, "the renamed current model is selected");
  assert.deepEqual(JSON.parse(select.options.body), {
    provider: "prov-1", model: "renamed-large", effort: null,
  });
  assert.equal(h.current.current.model, "renamed-large");
  console.log("ok - a renamed current row is found after earlier rows are deleted");
}

async function testBlankKeyDoesNotEraseStoredCredential() {
  const h = harness();
  await h.context.refresh();
  h.context.openProviderDialog(h.current.providers[0]);
  await h.context.saveProvider();
  const saved = h.calls.find(call => call.path === "/api/providers/prov-1" && call.options.method === "PUT");
  assert.equal(Object.hasOwn(JSON.parse(saved.options.body), "api_key"), false);
  console.log("ok - blank key is omitted when editing a provider");
}

async function main() {
  await testBlankKeyDoesNotEraseStoredCredential();
  await testHasModelRowsAndEffectiveWindow();
  await testDifferentWindowsAndMetadataSurviveRename();
  await testEmptyRemovesOverrideAndInvalidIsRejected();
  await testGlobalOverrideIsExplainable();
  await testSelectedModelKeepsSelectionWhenRenamed();
  await testRemovingEarlierRowDoesNotReselectAnotherModel();
  await testRenamingNonFirstRowAfterDeletionSelectsThatRow();
  console.log("frontend model context: all checks passed");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
