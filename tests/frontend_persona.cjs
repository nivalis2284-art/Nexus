"use strict";

// Behaviour tests for the real persona panel script against a small DOM stand-in.
// Run with: node tests/frontend_persona.cjs

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const PERSONA = fs.readFileSync(path.join(ROOT, "nexus", "web", "persona.js"), "utf8");

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
    this.value = "";
    this.checked = false;
    this.hidden = false;
    this.open = false;
    this.disabled = false;
    this.readOnly = false;
    this.type = "";
    this._listeners = {};
  }
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
  addEventListener(name, handler) {
    if (!this._listeners[name]) this._listeners[name] = [];
    this._listeners[name].push(handler);
  }
  async dispatch(name, event) {
    for (const handler of this._listeners[name] || []) await handler(event || {});
  }
  focus() {}
  showModal() { this.open = true; }
  close() {
    this.open = false;
    return this.dispatch("close");
  }
  descendants() {
    const out = [];
    for (const child of this.children) out.push(child, ...child.descendants());
    return out;
  }
  querySelectorAll(selector) {
    const tag = selector.match(/^[a-z][\w-]*$/i);
    const cls = selector.match(/\.([\w-]+)/);
    return this.descendants().filter((node) =>
      (!tag || node.tagName === tag[0].toUpperCase()) &&
      (!cls || node.classList.contains(cls[1])));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

const IDS = [
  "persona-dialog", "persona-open", "persona-close", "persona-load-error",
  "persona-snapshot-status", "persona-sections", "persona-section-status",
  "persona-add-section", "persona-section-dialog", "persona-section-dialog-title",
  "persona-section-dialog-close", "persona-section-title", "persona-section-content",
  "persona-section-error", "persona-section-cancel", "persona-section-save",
  "persona-section-form", "persona-entry-count", "persona-add", "persona-list-status",
  "persona-entries", "persona-entry-dialog", "persona-entry-form", "persona-form-title",
  "persona-entry-dialog-close", "persona-name", "persona-description", "persona-body",
  "persona-enabled", "persona-form-error", "persona-entry-cancel", "persona-entry-save",
  "persona-summary-generate", "persona-summary-status", "persona-suggestion",
  "persona-suggestion-text", "persona-adopt", "persona-discard", "persona-preview",
  "persona-refresh", "persona-last",
];

function snapshot() {
  return {
    persona: {
      sections: [
        { id: "s1", title: "默认核心设定", content: "保持简洁，先核实再回答。" },
        { id: "s2", title: "回复原则", content: "给结论，再补充必要细节。" },
      ],
      entries: [
        { id: "e1", name: "writing-style", description: "偏好短段落", body: "避免空泛套话。", enabled: true },
        { id: "e2", name: "old-habit", description: "暂时停用", body: "这条不应进入预览。", enabled: false },
      ],
    },
    preview: "保持简洁，先核实再回答。[[writing-style]]",
    last_request: {
      content: "最近的注入内容",
      time: "2026-10-22T08:30:00Z",
      source: "chat",
      model: "test-model",
    },
  };
}

function response(value, ok) {
  return {
    ok: ok !== false,
    status: ok === false ? 500 : 200,
    statusText: ok === false ? "Internal Server Error" : "OK",
    async json() { return structuredClone(value); },
  };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function textOf(node) {
  return String(node.textContent || "") + node.children.map(textOf).join("");
}

async function loadHarness() {
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
  for (const id of IDS) document.getElementById(id);
  for (const id of ["persona-dialog", "persona-section-dialog", "persona-entry-dialog"]) {
    const dialog = ids.get(id);
    dialog.open = false;
    dialog.showModal = function () { this.open = true; };
    dialog.close = function () {
      this.open = false;
      const handler = this._listeners.close ? this._listeners.close.slice() : [];
      for (const fn of handler) fn();
    };
  }
  const calls = { paths: [], bodies: [] };
  const queue = new Map();
  let current = snapshot();
  const fetch = async (requestPath, options) => {
    calls.paths.push(requestPath);
    const body = options && options.body ? JSON.parse(options.body) : null;
    calls.bodies.push(body);
    if (queue.has(requestPath)) {
      const pending = queue.get(requestPath).shift();
      if (pending) return pending;
    }
    if (requestPath === "/api/persona" && options && options.method === "PUT") {
      const assigned = body;
      assigned.sections = assigned.sections.map((item, index) => ({
        ...item,
        id: item.id || "s" + (index + 1),
      }));
      assigned.entries = assigned.entries.map((item, index) => ({
        ...item,
        id: item.id || "e" + (index + 1),
      }));
      current = {
        persona: assigned,
        preview: assigned.sections.map((item) => item.content).join("\n"),
        last_request: current.last_request,
      };
      return response(current);
    }
    if (requestPath === "/api/persona/summary") return response({ summary: "建议摘要：适合短段落。" });
    if (requestPath === "/api/persona") return response(current);
    return response({});
  };
  const context = vm.createContext({ document, fetch, console, confirm: () => true, structuredClone });
  vm.runInContext(PERSONA, context, { filename: "nexus/web/persona.js" });
  await new Promise((resolve) => setImmediate(resolve));
  return {
    ids,
    calls,
    current: () => current,
    setCurrent(value) { current = value; },
    queueOnce(requestPath, pending) {
      if (!queue.has(requestPath)) queue.set(requestPath, []);
      queue.get(requestPath).push(pending);
    },
    async open() {
      await ids.get("persona-open").dispatch("click");
      await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

async function testLoadSectionsEntriesAndLastSend() {
  const harness = await loadHarness();
  await harness.open();
  assert.equal(harness.ids.get("persona-dialog").open, true);
  const sections = harness.ids.get("persona-sections").querySelectorAll(".persona-section-card");
  assert.equal(sections.length, 2);
  assert.match(textOf(sections[0]), /默认核心设定/);
  const entries = harness.ids.get("persona-entries").querySelectorAll(".persona-entry");
  assert.equal(entries.length, 2);
  assert.equal(entries[0].querySelector(".persona-entry-name").textContent, "writing-style");
  assert.equal(entries[1].classList.contains("is-disabled"), true);
  assert.match(textOf(harness.ids.get("persona-last")), /聊天/);
  assert.match(textOf(harness.ids.get("persona-last")), /test-model/);
  console.log("ok - loads ordered sections, entries, preview and last request");
}

async function testSectionRenameKeepsId() {
  const harness = await loadHarness();
  await harness.open();
  const first = harness.ids.get("persona-sections").querySelector(".persona-section-card");
  await first.querySelectorAll("button")[0].dispatch("click");
  assert.equal(harness.ids.get("persona-section-title").value, "默认核心设定");
  harness.ids.get("persona-section-title").value = "Identity";
  await harness.ids.get("persona-section-form").dispatch("submit", { preventDefault() {} });
  const put = harness.calls.bodies.filter((body) => body && body.sections).pop();
  assert.equal(put.sections[0].id, "s1");
  assert.equal(put.sections[0].title, "Identity");
  assert.equal(put.sections[0].content, "保持简洁，先核实再回答。");
  assert.equal(harness.ids.get("persona-section-dialog").hidden, true);
  console.log("ok - renaming a section preserves its id and content");
}

async function testSectionEditAddMoveDelete() {
  const harness = await loadHarness();
  await harness.open();
  let first = harness.ids.get("persona-sections").querySelector(".persona-section-card");
  await first.querySelectorAll("button")[1].dispatch("click");
  harness.ids.get("persona-section-content").value = "新正文";
  await harness.ids.get("persona-section-form").dispatch("submit", { preventDefault() {} });
  let put = harness.calls.bodies.filter((body) => body && body.sections).pop();
  assert.equal(put.sections[0].content, "新正文");

  await harness.ids.get("persona-add-section").dispatch("click");
  harness.ids.get("persona-section-title").value = "新设定";
  harness.ids.get("persona-section-content").value = "新设定正文";
  await harness.ids.get("persona-section-form").dispatch("submit", { preventDefault() {} });
  put = harness.calls.bodies.filter((body) => body && body.sections).pop();
  assert.equal(put.sections[2].title, "新设定");
  assert.equal(typeof put.sections[2].id, "string");

  first = harness.ids.get("persona-sections").querySelectorAll(".persona-section-card")[2];
  await first.querySelectorAll("button")[2].dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  put = harness.calls.bodies.filter((body) => body && body.sections).pop();
  assert.equal(put.sections[1].title, "新设定");

  first = harness.ids.get("persona-sections").querySelectorAll(".persona-section-card")[1];
  await first.querySelector(".persona-delete").dispatch("click");
  put = harness.calls.bodies.filter((body) => body && body.sections).pop();
  assert.deepEqual(put.sections.map((item) => item.id), ["s1", "s2"]);
  console.log("ok - sections can be edited, added, reordered and deleted");
}

async function testEntrySaveUsesDescriptionOnly() {
  const harness = await loadHarness();
  await harness.open();
  await harness.ids.get("persona-add").dispatch("click");
  assert.equal(harness.ids.get("persona-entry-dialog").open, true);
  harness.ids.get("persona-name").value = "new-entry";
  harness.ids.get("persona-description").value = "新摘要";
  harness.ids.get("persona-body").value = "";
  harness.ids.get("persona-enabled").checked = false;
  await harness.ids.get("persona-entry-form").dispatch("submit", { preventDefault() {} });
  const put = harness.calls.bodies.filter((body) => body && body.entries).pop();
  assert.equal(put.entries.length, 3);
  assert.equal(put.entries[2].name, "new-entry");
  assert.equal(put.entries[2].description, "新摘要");
  assert.equal(put.entries[2].body, "");
  assert.equal(Object.hasOwn(put.entries[2], "summary"), false);
  assert.equal(harness.ids.get("persona-entry-dialog").hidden, true);
  assert.equal(harness.ids.get("persona-entry-count").textContent, "3 个条目 · 1 个已启用");
  assert.equal(harness.ids.get("persona-entry-count").textContent.includes("3 个条目"), true);
  console.log("ok - new entries require description, send no summary, and appear after save");
}

async function testEntryValidationAndDraftPreservation() {
  const harness = await loadHarness();
  await harness.open();
  await harness.ids.get("persona-add").dispatch("click");
  harness.ids.get("persona-name").value = "Bad Name";
  harness.ids.get("persona-description").value = "摘要";
  let writes = harness.calls.bodies.filter((body) => body && body.entries).length;
  await harness.ids.get("persona-entry-form").dispatch("submit", { preventDefault() {} });
  assert.match(harness.ids.get("persona-form-error").textContent, /slug/);
  assert.equal(harness.calls.bodies.filter((body) => body && body.entries).length, writes);

  harness.ids.get("persona-name").value = "valid-name";
  harness.ids.get("persona-description").value = "   ";
  await harness.ids.get("persona-entry-form").dispatch("submit", { preventDefault() {} });
  assert.match(harness.ids.get("persona-form-error").textContent, /摘要/);
  assert.equal(harness.ids.get("persona-entry-dialog").hidden, false);

  harness.ids.get("persona-description").value = "保留摘要";
  harness.queueOnce("/api/persona", response({ error: "disk full" }, false));
  await harness.ids.get("persona-entry-form").dispatch("submit", { preventDefault() {} });
  assert.equal(harness.ids.get("persona-name").value, "valid-name");
  assert.equal(harness.ids.get("persona-description").value, "保留摘要");
  assert.equal(harness.ids.get("persona-entry-dialog").open, true);
  assert.match(harness.ids.get("persona-form-error").textContent, /disk full/);
  assert.equal(harness.ids.get("persona-entry-save").disabled, false);
  console.log("ok - invalid names/descriptions block saving and failures keep the draft");
}

async function testSuggestionRequiresExplicitAdoption() {
  const harness = await loadHarness();
  await harness.open();
  await harness.ids.get("persona-add").dispatch("click");
  harness.ids.get("persona-name").value = "suggestion-test";
  harness.ids.get("persona-body").value = "一段正文";
  const writes = harness.calls.bodies.filter((body) => body && body.entries).length;
  await harness.ids.get("persona-summary-generate").dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.ids.get("persona-suggestion").hidden, false);
  assert.match(harness.ids.get("persona-suggestion-text").textContent, /建议摘要/);
  assert.equal(harness.ids.get("persona-description").value, "");
  assert.equal(harness.calls.bodies.filter((body) => body && body.entries).length, writes);
  await harness.ids.get("persona-adopt").dispatch("click");
  assert.equal(harness.ids.get("persona-description").value, "建议摘要：适合短段落。");
  assert.equal(harness.ids.get("persona-suggestion").hidden, true);
  assert.equal(harness.calls.bodies.filter((body) => body && body.entries).length, writes);
  console.log("ok - summary maps into description and only saves when the entry is saved");
}

async function testLateSummaryCannotOverwriteAnotherEntry() {
  const harness = await loadHarness();
  await harness.open();
  const pending = deferred();
  harness.queueOnce("/api/persona/summary", pending.promise);
  await harness.ids.get("persona-add").dispatch("click");
  harness.ids.get("persona-name").value = "first";
  harness.ids.get("persona-body").value = "first body";
  const generating = harness.ids.get("persona-summary-generate").dispatch("click");
  const first = harness.ids.get("persona-entries").querySelector(".persona-entry");
  await first.querySelectorAll("button")[0].dispatch("click");
  pending.resolve(response({ summary: "迟到建议" }));
  await generating;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.ids.get("persona-suggestion").hidden, true);
  assert.equal(harness.ids.get("persona-description").value, "偏好短段落");
  console.log("ok - a late summary cannot overwrite another selected entry");
}

async function testToggleAndDeleteUseWholeDocument() {
  const harness = await loadHarness();
  await harness.open();
  const entries = harness.ids.get("persona-entries").querySelectorAll(".persona-entry");
  const toggle = entries[0].querySelector("input");
  toggle.checked = false;
  await toggle.dispatch("change");
  let put = harness.calls.bodies.filter((body) => body && body.entries).pop();
  assert.equal(put.entries[0].enabled, false);
  assert.equal(put.sections.length, 2);
  const refreshed = harness.ids.get("persona-entries").querySelectorAll(".persona-entry");
  await refreshed[1].querySelector(".persona-delete").dispatch("click");
  put = harness.calls.bodies.filter((body) => body && body.entries).pop();
  assert.equal(put.entries.length, 1);
  assert.equal(put.entries[0].id, "e1");
  console.log("ok - toggle and delete submit the whole persona document");
}

async function testUserContentAndSkillFieldsRenderAsText() {
  const harness = await loadHarness();
  const value = snapshot();
  value.persona.sections[0].title = "<img src=x onerror=alert(1)>";
  value.persona.entries[0].name = "<b>粗体</b>";
  value.persona.entries[0].description = "<script>bad()</script>";
  value.preview = "<div>预览</div>";
  value.last_request.content = "<svg onload=bad()>";
  harness.setCurrent(value);
  await harness.open();
  const sections = harness.ids.get("persona-sections");
  assert.equal(sections.querySelector(".persona-entry-name").textContent, "<img src=x onerror=alert(1)>");
  assert.equal(sections.querySelector(".persona-entry-name").children.length, 0);
  assert.equal(harness.ids.get("persona-entries").querySelector(".persona-entry-summary").textContent, "<script>bad()</script>");
  assert.equal(harness.ids.get("persona-preview").textContent, "<div>预览</div>");
  assert.match(textOf(harness.ids.get("persona-last")), /<svg onload=bad\(\)>/);
  console.log("ok - all persona source data renders with textContent");
}

async function testEncodingErrorIsExplicitAndKeepsDraft() {
  const harness = await loadHarness();
  await harness.open();
  const first = harness.ids.get("persona-entries").querySelector(".persona-entry");
  await first.querySelectorAll("button")[0].dispatch("click");
  harness.ids.get("persona-body").value = "尚未保存的中文草稿";
  const message = "无法读取技能/人设文件 SKILL.md：内容不是有效的 UTF-8 或 GBK 编码。"
    + "请将文件重新保存为 UTF-8 或 GBK 后再编辑；原文件未被修改。";
  harness.queueOnce("/api/persona", response({ error: message }, false));
  await harness.ids.get("persona-entry-form").dispatch("submit", { preventDefault() {} });
  assert.equal(harness.ids.get("persona-body").value, "尚未保存的中文草稿");
  assert.equal(harness.ids.get("persona-entry-dialog").open, true);
  assert.match(harness.ids.get("persona-form-error").textContent, /UTF-8/);
  assert.match(harness.ids.get("persona-form-error").textContent, /GBK/);
  assert.match(harness.ids.get("persona-form-error").textContent, /未被修改/);
  console.log("ok - an encoding error is shown as text and keeps the entry draft");
}

async function main() {
  await testLoadSectionsEntriesAndLastSend();
  await testSectionRenameKeepsId();
  await testSectionEditAddMoveDelete();
  await testEntrySaveUsesDescriptionOnly();
  await testEntryValidationAndDraftPreservation();
  await testSuggestionRequiresExplicitAdoption();
  await testLateSummaryCannotOverwriteAnotherEntry();
  await testToggleAndDeleteUseWholeDocument();
  await testUserContentAndSkillFieldsRenderAsText();
  await testEncodingErrorIsExplicitAndKeepsDraft();
  console.log("frontend persona: all checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
