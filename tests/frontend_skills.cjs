"use strict";

// Behaviour tests for the skill editor against a local DOM stand-in.
// Run with: node tests/frontend_skills.cjs

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const SKILLS = fs.readFileSync(path.join(ROOT, "nexus", "web", "skills.js"), "utf8");

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
    const handlers = (this._listeners.close || []).slice();
    for (const handler of handlers) handler();
  }
  descendants() {
    const out = [];
    for (const child of this.children) out.push(child, ...child.descendants());
    return out;
  }
  querySelectorAll(selector) {
    const cls = selector.match(/\.([\w-]+)/);
    return this.descendants().filter((node) => !cls || node.classList.contains(cls[1]));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

const IDS = [
  "skills-open", "skills-close", "skills-dialog", "skills-refresh", "skills-new",
  "skills-list", "skills-count", "skills-status", "skill-dialog", "skill-form",
  "skill-dialog-title", "skill-dialog-close", "skill-name", "skill-description",
  "skill-body", "skill-source", "skill-error", "skill-cancel", "skill-save",
];

function listing() {
  return {
    skills: [
      { source: "project", path: ".nexus/skills/review/SKILL.md", name: "review", description: "Review code.", body: "PROJECT REVIEW", effective: true, read_only: false },
      { source: "personal", path: "skills/review/SKILL.md", name: "review", description: "Older review.", body: "PERSONAL REVIEW", effective: false, read_only: false },
      { source: "builtin", path: "nexus/builtin_skills/ling-automation/SKILL.md", name: "ling-automation", description: "Run Ling work.", body: "BUILTIN BODY", effective: true, read_only: true },
    ],
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
  return (node.children.length ? "" : String(node.textContent || "")) +
    node.children.map(textOf).join("");
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
  for (const id of ["skills-dialog", "skill-dialog"]) {
    const dialog = ids.get(id);
    dialog.open = false;
    dialog.showModal = function () { this.open = true; };
    dialog.close = function () {
      this.open = false;
      const handlers = (this._listeners.close || []).slice();
      for (const handler of handlers) handler();
    };
  }
  const calls = { paths: [], bodies: [] };
  const queue = new Map();
  let current = listing();
  const fetch = async (requestPath, options) => {
    calls.paths.push(requestPath);
    const body = options && options.body ? JSON.parse(options.body) : null;
    calls.bodies.push(body);
    if (queue.has(requestPath)) {
      const pending = queue.get(requestPath).shift();
      if (pending) return pending;
    }
    if (requestPath === "/api/skills" && options && options.method === "POST") {
      const saved = {
        source: body.source,
        path: body.path || (body.source === "project"
          ? ".nexus/skills/" + body.name + "/SKILL.md"
          : "skills/" + body.name + "/SKILL.md"),
        name: body.name,
        description: body.description,
        body: body.body,
        effective: true,
        read_only: false,
      };
      current = {
        skills: current.skills.some((item) => item.path === saved.path)
          ? current.skills.map((item) => item.path === saved.path ? saved : item)
          : [...current.skills, saved],
      };
      return response(current);
    }
    if (requestPath === "/api/skills/delete") {
      current = { skills: current.skills.filter((item) => item.path !== body.path) };
      return response(current);
    }
    if (requestPath === "/api/skills") return response(current);
    return response({});
  };
  const context = vm.createContext({ document, fetch, console, confirm: () => true, structuredClone });
  vm.runInContext(SKILLS, context, { filename: "nexus/web/skills.js" });
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
      await ids.get("skills-open").dispatch("click");
      await new Promise((resolve) => setImmediate(resolve));
    },
  };
}

function cards(harness) {
  return harness.ids.get("skills-list").querySelectorAll(".persona-entry");
}

async function testListingShowsSourcesAndOverrides() {
  const harness = await loadHarness();
  await harness.open();
  assert.equal(harness.ids.get("skills-dialog").open, true);
  assert.equal(harness.ids.get("skills-count").textContent, "3 个技能");
  const list = cards(harness);
  assert.equal(list.length, 3);
  assert.match(textOf(list[0]), /项目/);
  assert.match(textOf(list[0]), /当前生效/);
  assert.match(textOf(list[1]), /被更高优先级同名技能覆盖/);
  assert.match(textOf(list[2]), /内置/);
  assert.equal(list[2].querySelector(".persona-action").textContent, "查看");
  assert.equal(list[2].querySelector(".persona-delete"), null);
  console.log("ok - lists builtin, personal and effective project skills");
}

async function testBuiltinViewShowsBodyReadOnly() {
  const harness = await loadHarness();
  await harness.open();
  const builtin = cards(harness)[2];
  await builtin.querySelector(".persona-action").dispatch("click");
  assert.equal(harness.ids.get("skill-dialog").open, true);
  assert.equal(harness.ids.get("skill-dialog-title").textContent, "查看技能");
  assert.equal(harness.ids.get("skill-name").value, "ling-automation");
  assert.equal(harness.ids.get("skill-body").value, "BUILTIN BODY");
  assert.equal(harness.ids.get("skill-name").readOnly, true);
  assert.equal(harness.ids.get("skill-description").readOnly, true);
  assert.equal(harness.ids.get("skill-body").readOnly, true);
  assert.equal(harness.ids.get("skill-source").disabled, true);
  assert.equal(harness.ids.get("skill-save").hidden, true);
  const writes = harness.calls.bodies.length;
  await harness.ids.get("skill-form").dispatch("submit", { preventDefault() {} });
  assert.equal(harness.calls.bodies.length, writes, "read-only view never saves");
  console.log("ok - builtin body is visible in the same dialog with no save button");
}

async function testNewSkillDefaultsToPersonal() {
  const harness = await loadHarness();
  await harness.open();
  await harness.ids.get("skills-new").dispatch("click");
  assert.equal(harness.ids.get("skill-source").value, "personal");
  assert.equal(harness.ids.get("skill-source").disabled, false);
  assert.equal(harness.ids.get("skill-save").hidden, false);
  harness.ids.get("skill-name").value = "new-skill";
  harness.ids.get("skill-description").value = "New summary.";
  harness.ids.get("skill-body").value = "New body.";
  await harness.ids.get("skill-form").dispatch("submit", { preventDefault() {} });
  const post = harness.calls.bodies.filter((body) => body && body.source).pop();
  assert.deepEqual(post, {
    source: "personal",
    name: "new-skill",
    description: "New summary.",
    body: "New body.",
  });
  assert.equal(harness.ids.get("skill-dialog").hidden, true);
  assert.equal(cards(harness).length, 4);
  assert.equal(harness.ids.get("skills-new").disabled, false);
  assert.equal(harness.ids.get("skills-refresh").disabled, false);
  console.log("ok - a new skill defaults to personal and saves description plus body");
}

async function testEditKeepsSourceAndUsesPath() {
  const harness = await loadHarness();
  await harness.open();
  const personal = cards(harness)[1];
  await personal.querySelector(".persona-action").dispatch("click");
  assert.equal(harness.ids.get("skill-source").value, "personal");
  assert.equal(harness.ids.get("skill-source").disabled, true);
  harness.ids.get("skill-source").value = "project";
  harness.ids.get("skill-description").value = "Updated summary.";
  await harness.ids.get("skill-form").dispatch("submit", { preventDefault() {} });
  const post = harness.calls.bodies.filter((body) => body && body.path).pop();
  assert.equal(post.source, "personal");
  assert.equal(post.path, "skills/review/SKILL.md");
  assert.equal(post.description, "Updated summary.");
  assert.equal(cards(harness).length, 3);
  console.log("ok - editing an existing skill locks its original source");
}

async function testValidationAndDelete() {
  const harness = await loadHarness();
  await harness.open();
  await harness.ids.get("skills-new").dispatch("click");
  harness.ids.get("skill-name").value = "Bad Name";
  harness.ids.get("skill-description").value = "Summary.";
  let writes = harness.calls.bodies.length;
  await harness.ids.get("skill-form").dispatch("submit", { preventDefault() {} });
  assert.match(harness.ids.get("skill-error").textContent, /slug/);
  assert.equal(harness.calls.bodies.length, writes);
  harness.ids.get("skill-name").value = "valid-name";
  harness.ids.get("skill-description").value = "  ";
  await harness.ids.get("skill-form").dispatch("submit", { preventDefault() {} });
  assert.match(harness.ids.get("skill-error").textContent, /摘要/);
  assert.equal(harness.calls.bodies.length, writes);
  harness.ids.get("skill-description").value = "Valid summary.";
  await harness.ids.get("skill-cancel").dispatch("click");
  assert.equal(harness.ids.get("skill-dialog").open, false);

  const personal = cards(harness)[1];
  await personal.querySelector(".persona-delete").dispatch("click");
  const post = harness.calls.bodies.filter((body) => body && body.path && !body.name).pop();
  assert.equal(post.source, "personal");
  assert.equal(post.path, "skills/review/SKILL.md");
  assert.equal(cards(harness).length, 2);
  console.log("ok - name/description are required and deletion sends source plus path");
}

async function testUserContentRendersAsText() {
  const harness = await loadHarness();
  const value = listing();
  value.skills[0].name = "<img src=x onerror=bad()>";
  value.skills[0].description = "<script>bad()</script>";
  harness.setCurrent(value);
  await harness.open();
  const first = cards(harness)[0];
  assert.equal(first.querySelector(".persona-entry-name").textContent, "<img src=x onerror=bad()>");
  assert.equal(first.querySelector(".persona-entry-name").children.length, 0);
  assert.equal(first.querySelector(".persona-entry-summary").textContent, "<script>bad()</script>");
  console.log("ok - skill names and descriptions render with textContent");
}

async function testLateSaveCannotReplaceNewerEditor() {
  const harness = await loadHarness();
  await harness.open();
  const personal = cards(harness)[1];
  await personal.querySelector(".persona-action").dispatch("click");
  const pending = deferred();
  harness.queueOnce("/api/skills", pending.promise);
  harness.ids.get("skill-description").value = "Updated summary.";
  const saving = harness.ids.get("skill-form").dispatch("submit", { preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.ids.get("skills-new").disabled, true);
  await harness.ids.get("skills-close").dispatch("click");
  await harness.ids.get("skills-open").dispatch("click");
  await new Promise((resolve) => setImmediate(resolve));
  await harness.ids.get("skills-new").dispatch("click");
  harness.ids.get("skill-name").value = "fresh-draft";
  const dialog = harness.ids.get("skill-dialog");
  assert.equal(dialog.open, true);
  assert.equal(dialog.hidden, false);
  pending.resolve(response(listing()));
  await saving;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(harness.ids.get("skill-dialog").open, true);
  assert.equal(harness.ids.get("skill-name").value, "fresh-draft");
  assert.equal(harness.ids.get("skill-name").readOnly, false);
  console.log("ok - a late save response cannot overwrite a newer skill editor");
}

async function main() {
  await testListingShowsSourcesAndOverrides();
  await testBuiltinViewShowsBodyReadOnly();
  await testNewSkillDefaultsToPersonal();
  await testEditKeepsSourceAndUsesPath();
  await testValidationAndDelete();
  await testUserContentRendersAsText();
  await testLateSaveCannotReplaceNewerEditor();
  console.log("frontend skills: all checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
