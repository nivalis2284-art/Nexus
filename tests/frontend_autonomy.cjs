"use strict";

// Frontend checks for the owner-facing autonomy panel. Scripts run in a small
// DOM stand-in with a mocked HTTP boundary; this never starts a model or a
// background autonomy worker.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const APP = fs.readFileSync(path.join(ROOT, "nexus", "web", "app.js"), "utf8");
const LING = fs.readFileSync(path.join(ROOT, "nexus", "web", "ling.js"), "utf8");
const TASKS = fs.readFileSync(path.join(ROOT, "nexus", "web", "tasks.js"), "utf8");
const AUTONOMY = fs.readFileSync(path.join(ROOT, "nexus", "web", "autonomy.js"), "utf8");

class ClassList {
  constructor(node) { this.node = node; }
  values() { return String(this.node.className || "").split(/\s+/).filter(Boolean); }
  contains(value) { return this.values().includes(value); }
  add(...values) {
    const current = this.values();
    for (const value of values) if (!current.includes(value)) current.push(value);
    this.node.className = current.join(" ");
  }
  remove(...values) { this.node.className = this.values().filter((value) => !values.includes(value)).join(" "); }
  toggle(value, force) {
    const wanted = force === undefined ? !this.contains(value) : Boolean(force);
    if (wanted) this.add(value); else this.remove(value);
    return wanted;
  }
}

class Node {
  constructor(tag) {
    this.tagName = String(tag || "div").toUpperCase();
    this.children = [];
    this.parent = null;
    this.className = "";
    this.classList = new ClassList(this);
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.files = [];
    this.rows = 1;
  }
  set innerHTML(value) { this.children = []; this._innerHTML = String(value || ""); }
  get innerHTML() { return this._innerHTML || ""; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  replaceWith(other) {
    if (!this.parent) return;
    const index = this.parent.children.indexOf(this);
    if (index < 0) return;
    other.parent = this.parent;
    this.parent.children[index] = other;
    this.parent = null;
  }
  remove() {
    if (!this.parent) return;
    const index = this.parent.children.indexOf(this);
    if (index >= 0) this.parent.children.splice(index, 1);
    this.parent = null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, handler) { (this.handlers ||= {})[name] = handler; }
  focus() {}
  select() {}
  showModal() {}
  close() {}
  requestSubmit() { if (this.handlers?.submit) return this.handlers.submit({ preventDefault() {}, target: this }); }
  getBoundingClientRect() { return { left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 }; }
  descendants() {
    const out = [];
    for (const child of this.children) out.push(child, ...child.descendants());
    return out;
  }
  querySelectorAll(selector) {
    const className = selector.match(/\.([\w-]+)/)?.[1];
    const tag = selector.match(/^[a-z][\w-]*$/i)?.[0];
    const dataMatch = selector.match(/^\[data-([\w-]+)\]$/);
    return this.descendants().filter((node) =>
      (!className || node.classList.contains(className)) &&
      (!tag || node.tagName === tag.toUpperCase()) &&
      (!dataMatch || node.dataset[dataMatch[1]] !== undefined));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  contains(node) { return node === this || this.descendants().includes(node); }
}

function response(value, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : String(status),
    async json() { return structuredClone(value); },
  };
}

function baseSnapshot() {
  return {
    control: { state: "disabled", version: 1 },
    settings: {
      read_roots: [],
      output_root: "",
      token_limit: 0,
      max_output_tokens: 24000,
      fallback_seconds: 7200,
      contact_after_seconds: 86400,
      observer_interval: 60,
      allowed_mcp_tools: [],
    },
    budget: { limit: 120000, spent: 100, reserved: 25, remaining: 119875, unknown: 0 },
    running: false,
    error: "",
    observer: { connected: false, error: "" },
    goals: [],
    plans: [],
    watches: [],
    decisions: [],
    messages: [],
    requests: [],
  };
}

async function harness(options = {}) {
  const ids = new Map();
  const document = {
    hidden: false,
    readyState: "complete",
    createElement(tag) { return new Node(tag); },
    getElementById(id) { if (!ids.has(id)) ids.set(id, new Node("div")); return ids.get(id); },
    querySelectorAll(selector) {
      const roots = [...ids.values()];
      return roots.flatMap((node) => node.querySelectorAll(selector));
    },
    addEventListener() {},
  };
  const calls = { paths: [], bodies: [] };
  let snapshot = options.snapshot || baseSnapshot();
  let failNext = options.failNext || null;
  const fetch = async (requestPath, requestOptions) => {
    calls.paths.push(requestPath);
    let body = null;
    if (requestOptions?.body) {
      body = JSON.parse(requestOptions.body);
      calls.bodies.push({ path: requestPath, body });
    }
    if (failNext && failNext.path === requestPath) {
      const failure = failNext;
      failNext = null;
      return response({ error: failure.error }, failure.status || 500);
    }
    if (requestPath === "/api/state") {
      return response({ providers: [], current: {}, effort: { levels: [], labels: {} },
        sessions: [], permission: "ask", workspace: "C:\\tmp",
        profile: { context_window_tokens: 65536 } });
    }
    if (requestPath === "/api/ling") {
      return response({ session_id: "ling", items: [], running: false, queue_count: 0,
        inputs: [], tasks: [], notifications: [], unread_count: 0 });
    }
    if (requestPath === "/api/autonomy") return response(structuredClone(snapshot));
    if (requestPath.startsWith("/api/autonomy/sources/")) {
      const url = new URL(requestPath, "http://localhost");
      const id = decodeURIComponent(url.pathname.split("/").pop());
      const offset = Number(url.searchParams.get("offset") || 0);
      if (id === "event:404") return response({ error: "来源不存在" }, 404);
      if (id === "event:1" || id === "event:3") {
        const content = "{\"text\":\"实际来源原文\"}";
        const chunk = content.slice(offset, offset + 10);
        return response({ id, kind: "owner_message", at: "2026-10-21T01:00:00Z",
          content: chunk, total_chars: content.length,
          next_offset: offset + chunk.length < content.length ? offset + chunk.length : null });
      }
      if (id === "event:2") {
        const content = "ABCDEFGHIJ第二段内容";
        const chunk = content.slice(offset, offset + 10);
        return response({ id, kind: "owner_message", at: "2026-10-21T01:00:00Z",
          content: chunk, total_chars: content.length,
          next_offset: offset + chunk.length < content.length ? offset + chunk.length : null });
      }
      return response({ error: "来源不存在" }, 404);
    }
    if (requestPath === "/api/autonomy/settings") {
      snapshot.settings = structuredClone(body);
      snapshot.control.version += 1;
      return response(structuredClone(snapshot));
    }
    if (requestPath === "/api/autonomy/control") {
      if (body.expected_version !== snapshot.control.version) {
        return response({ error: "version conflict" }, 409);
      }
      snapshot.control.state = {
        enable: "enabled", pause: "paused", resume: "enabled", terminate: "terminated",
      }[body.operation];
      snapshot.control.version += 1;
      return response(structuredClone(snapshot));
    }
    if (/\/api\/autonomy\/goals\/.+\/control$/.test(requestPath)) {
      const id = decodeURIComponent(requestPath.split("/")[4]);
      const goal = snapshot.goals.find((item) => item.id === id);
      if (!goal || body.expected_version !== goal.version) return response({ error: "version conflict" }, 409);
      goal.status = "withdrawn";
      goal.version += 1;
      return response(structuredClone(snapshot));
    }
    if (/\/api\/autonomy\/messages\/.+\/read$/.test(requestPath)) {
      const id = decodeURIComponent(requestPath.split("/")[4]);
      const message = snapshot.messages.find((item) => item.id === id);
      if (message) message.read_at = "2026-10-21T03:00:00Z";
      return response(structuredClone(snapshot));
    }
    if (/\/api\/autonomy\/requests\/.+\/answer$/.test(requestPath)) {
      const id = decodeURIComponent(requestPath.split("/")[4]);
      const request = snapshot.requests.find((item) => item.id === id);
      if (!request || body.expected_version !== request.version) return response({ error: "version conflict" }, 409);
      request.status = "answered";
      request.answer = body.answer;
      return response(structuredClone(snapshot));
    }
    if (requestPath === "/api/autonomy/review") return response({ requested: true });
    return response({});
  };
  const context = vm.createContext({
    document, window: { innerWidth: 1200 }, fetch, TextDecoder, TextEncoder, console,
    setTimeout(callback) { return callback; },
    clearTimeout() {}, confirm: () => true,
  });
  const load = (source, name) => vm.runInContext(source, context, { filename: name });
  load(APP, "nexus/web/app.js");
  load(LING, "nexus/web/ling.js");
  load(AUTONOMY, "nexus/web/autonomy.js");
  load(TASKS, "nexus/web/tasks.js");
  await new Promise((resolve) => setImmediate(resolve));
  return {
    context, ids, calls,
    evaluate(code) { return vm.runInContext(code, context); },
    snapshot() { return snapshot; },
    setFailNext(value) { failNext = value; },
  };
}

async function testInitialDisabledAndNoReview() {
  const h = await harness();
  assert.equal(h.evaluate("autonomyState()"), "disabled");
  assert.equal(h.ids.get("autonomy-enable").disabled, false);
  assert.equal(h.ids.get("autonomy-pause").disabled, true);
  assert.equal(h.calls.paths.includes("/api/autonomy/review"), false,
    "loading the UI must not request an autonomous review");
  assert.match(h.ids.get("autonomy-state").textContent, /未启用/);
  assert.equal(h.ids.get("autonomy-settings-section").open, true,
    "an unconfigured autonomy setup starts expanded");
  assert.equal(h.evaluate("autonomyView.settingsInitialized"), true,
    "the first real settings snapshot initializes the open/closed choice");
  console.log("ok - autonomy starts disabled and never auto-requests review");
}

async function testCandidateAndOutputLimitContract() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 2 };
  snapshot.settings.read_roots = ["C:\\Research"];
  snapshot.settings.output_root = "C:\\Research\\output";
  snapshot.settings.token_limit = 120000;
  snapshot.settings.max_output_tokens = 2048;
  snapshot.goals = [{ id: "g1", title: "候选目标", status: "candidate", version: 1 }];
  const h = await harness({ snapshot });
  const goalText = h.ids.get("autonomy-goals").descendants().map((node) => node.textContent).join(" ");
  assert.match(goalText, /候选事项/);
  assert.doesNotMatch(goalText, /\bcandidate\b/);
  const html = fs.readFileSync(path.join(ROOT, "nexus", "web", "index.html"), "utf8");
  assert.match(html, /id="autonomy-max-output-tokens"[^>]*min="128"/);
  assert.match(html, /id="autonomy-max-output-tokens"[^>]*max="65536"/);
  h.ids.get("autonomy-max-output-tokens").value = "127";
  assert.equal(h.evaluate("autonomyReadSettings().max_output_tokens"), 127,
    "validation sees the owner's entered value instead of silently clamping it");
  assert.match(h.evaluate("autonomyValidateSettings(autonomyReadSettings())"), /128/);
  console.log("ok - candidate status and output-token bounds match the backend contract");
}

async function testConfiguredSettingsCollapseOnlyOnce() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 2 };
  snapshot.settings.read_roots = ["C:\\Research"];
  snapshot.settings.output_root = "C:\\Research\\output";
  snapshot.settings.token_limit = 120000;
  const h = await harness({ snapshot });
  const section = h.ids.get("autonomy-settings-section");
  assert.equal(section.open, false, "configured settings start collapsed");
  section.open = true;
  section.handlers.toggle();
  await h.evaluate("autonomyPoll()");
  assert.equal(section.open, true, "polling respects an owner's manual expansion");
  console.log("ok - configured settings collapse once without overriding owner expansion");
}

async function testTimesPreserveConfiguredPrecision() {
  const snapshot = baseSnapshot();
  snapshot.settings.read_roots = ["C:\\Research"];
  snapshot.settings.output_root = "C:\\Research\\output";
  snapshot.settings.token_limit = 120000;
  snapshot.settings.fallback_seconds = 1;
  snapshot.settings.contact_after_seconds = 0;
  snapshot.settings.observer_interval = 0.1;
  const h = await harness({ snapshot });
  assert.equal(h.ids.get("autonomy-fallback-minutes").value, "0.016667");
  assert.equal(h.ids.get("autonomy-contact-minutes").value, "0");
  assert.equal(h.ids.get("autonomy-observer-interval").value, "0.1");
  const settings = h.evaluate("autonomyReadSettings()");
  assert.equal(settings.fallback_seconds, 1, "a one-second fallback is not silently changed");
  assert.equal(settings.contact_after_seconds, 0, "zero contact wait remains an explicit choice");
  assert.equal(settings.observer_interval, 0.1, "sub-second observer interval is preserved");
  assert.equal(h.evaluate("autonomyValidateSettings(autonomyReadSettings())"), "");
  h.ids.get("autonomy-observer-interval").value = "0.123456";
  assert.equal(h.evaluate("autonomyReadSettings().observer_interval"), 0.123456,
    "supported observer precision is not silently rounded away");
  console.log("ok - minute and observer fields preserve exact supported values");
}

async function testSettingsDraftSurvivesPollingAndSaveUsesContract() {
  const snapshot = baseSnapshot();
  snapshot.settings.read_roots = ["C:\\Research"];
  snapshot.settings.output_root = "C:\\Research\\output";
  snapshot.settings.token_limit = 120000;
  const h = await harness({ snapshot });
  h.ids.get("autonomy-read-roots").value = "C:\\Draft\\one\nC:\\Draft\\two";
  h.ids.get("autonomy-output-root").value = "C:\\Draft\\out";
  h.ids.get("autonomy-token-limit").value = "42000";
  h.ids.get("autonomy-max-output-tokens").value = "8000";
  h.ids.get("autonomy-fallback-minutes").value = "90";
  h.ids.get("autonomy-contact-minutes").value = "1440";
  h.ids.get("autonomy-observer-interval").value = "30";
  h.ids.get("autonomy-allowed-mcp-tools").value = "mcp__research__search\nmcp__notes__read";
  h.evaluate("autonomyMarkSettingsDirty()");
  await h.evaluate("autonomyPoll()");
  assert.equal(h.ids.get("autonomy-read-roots").value, "C:\\Draft\\one\nC:\\Draft\\two",
    "a poll must not overwrite an owner draft");
  await h.evaluate("autonomySaveSettings()");
  const save = h.calls.bodies.find((call) => call.path === "/api/autonomy/settings");
  assert.deepEqual(save.body.read_roots, ["C:\\Draft\\one", "C:\\Draft\\two"]);
  assert.equal(save.body.token_limit, 42000);
  assert.equal(save.body.max_output_tokens, 8000);
  assert.equal(save.body.fallback_seconds, 5400);
  assert.equal(save.body.contact_after_seconds, 86400);
  assert.equal(save.body.observer_interval, 30);
  assert.deepEqual(save.body.allowed_mcp_tools, ["mcp__research__search", "mcp__notes__read"]);
  console.log("ok - autonomy settings keep drafts and submit the declared contract");
}

async function testEnableSavesDirtySettingsBeforeControl() {
  const snapshot = baseSnapshot();
  snapshot.settings.read_roots = ["C:\\Research"];
  snapshot.settings.output_root = "C:\\Research\\output";
  snapshot.settings.token_limit = 120000;
  const h = await harness({ snapshot });
  h.ids.get("autonomy-token-limit").value = "50000";
  h.evaluate("autonomyMarkSettingsDirty()");
  await h.evaluate("autonomyControl('enable')");
  const settingIndex = h.calls.paths.indexOf("/api/autonomy/settings");
  const controlIndex = h.calls.paths.indexOf("/api/autonomy/control");
  assert.ok(settingIndex >= 0 && controlIndex > settingIndex,
    "enable saves edited settings first, then applies the control");
  const control = h.calls.bodies.find((call) => call.path === "/api/autonomy/control");
  assert.equal(control.body.operation, "enable");
  assert.equal(control.body.expected_version, 2);
  assert.equal(h.evaluate("autonomyState()"), "enabled");
  console.log("ok - enabling persists settings and uses the fresh control version");
}

async function testPauseResumeTerminateAndConflict() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 7 };
  const h = await harness({ snapshot });
  await h.evaluate("autonomyControl('pause')");
  assert.equal(h.evaluate("autonomyState()"), "paused");
  await h.evaluate("autonomyControl('resume')");
  assert.equal(h.evaluate("autonomyState()"), "enabled");
  h.snapshot().control.version = 20;
  await h.evaluate("autonomyControl('terminate')");
  assert.equal(h.evaluate("autonomyView.conflict"), true,
    "a stale control version is surfaced instead of silently retried");
  assert.equal(h.ids.get("autonomy-conflict").hidden, false);
  await h.evaluate("autonomyPoll()");
  assert.equal(h.evaluate("autonomyView.snapshot.control.version"), 20,
    "a manual refresh retrieves the current version");
  console.log("ok - pause, resume, and terminate preserve version conflicts");
}

async function testGoalsWatchesPlansAndNoHiddenReasoning() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 3 };
  snapshot.goals = [{
    id: "g1", title: "核对资料中的矛盾", question: "哪份记录更可靠？", status: "investigating",
    reason: "两份材料说法不同", understanding: "目前只能确认第一版内容",
    sources: [{ title: "材料 A", path: "C:\\Research\\a.md" }], version: 4,
    thinking: "PRIVATE CHAIN OF THOUGHT", reasoning: "HIDDEN REASONING",
  }];
  snapshot.plans = [{ id: "p1", goal_id: "g1", due_at: "2026-10-22T01:00:00Z",
    reason: "等新材料到达", status: "scheduled" }];
  snapshot.watches = [{ id: "w1", title: "材料 A", path: "C:\\Research\\a.md",
    status: "active", last_checked_at: "2026-10-21T01:00:00Z" }];
  snapshot.decisions = [{ id: "d1", at: "2026-10-21T01:00:00Z", action: "先调查", reason: "证据不足" }];
  const h = await harness({ snapshot });
  await h.evaluate("autonomyOpen(true)");
  const textLeaves = (root) => {
    if (!root.children.length) return [root.textContent, root.innerHTML];
    return root.children.flatMap(textLeaves);
  };
  const roots = ["autonomy-goals", "autonomy-plans", "autonomy-watches", "autonomy-decisions"];
  const rendered = roots.flatMap((id) => textLeaves(h.ids.get(id))).join("\n");
  assert.match(rendered, /核对资料中的矛盾/);
  assert.match(rendered, /材料 A/);
  assert.match(rendered, /等新材料到达/);
  assert.doesNotMatch(rendered, /PRIVATE CHAIN OF THOUGHT|HIDDEN REASONING/,
    "only recorded understanding and sources are rendered, not hidden reasoning");
  console.log("ok - goals, sources, plans, watches, and decisions render without hidden reasoning");
}

async function testGoalWithdrawUsesGoalVersion() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 9 };
  snapshot.goals = [{ id: "goal-a", title: "撤回测试", status: "waiting", version: 12 }];
  const h = await harness({ snapshot });
  await h.evaluate("autonomyOpen(true)");
  const goal = h.snapshot().goals[0];
  await h.evaluate("autonomyWithdrawGoal(autonomyView.snapshot.goals[0])");
  const call = h.calls.bodies.find((item) => item.path.endsWith("/goals/goal-a/control"));
  assert.equal(call.body.operation, "withdraw");
  assert.equal(call.body.expected_version, 12);
  assert.equal(goal.status, "withdrawn");
  console.log("ok - withdrawing a goal uses that goal's version");
}

async function testMessagesAreIndependentAndToastOnlyForFreshUnread() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 2 };
  snapshot.messages = [{ id: "m-old", text: "之前的补看消息", at: "2026-10-20T01:00:00Z", read_at: null,
    sources: [{ title: "旧资料" }] }];
  const h = await harness({ snapshot });
  assert.equal(h.evaluate("$('autonomy-toast').hidden"), true,
    "existing unread messages are backfilled without an immediate toast");
  assert.equal(h.ids.get("autonomy-unread-count").textContent, "1");
  assert.equal(h.ids.get("notification-count").textContent, "0",
    "autonomy unread state never changes task notification count");
  h.snapshot().messages.push({ id: "m-new", text: "新发现值得告诉你", at: "2026-10-21T02:00:00Z",
    read_at: null, sources: [{ title: "新资料" }] });
  await h.evaluate("autonomyPoll()");
  assert.equal(h.evaluate("$('autonomy-toast').hidden"), false,
    "a newly arriving unread message raises the passive toast");
  assert.match(h.ids.get("autonomy-toast-text").textContent, /新发现值得告诉你/);
  assert.equal(h.ids.get("autonomy-unread-count").textContent, "2");
  assert.equal(h.ids.get("notification-count").textContent, "0");
  await h.evaluate("autonomyMarkRead(autonomyView.snapshot.messages[1])");
  const readCall = h.calls.bodies.find((item) => item.path.endsWith("/messages/m-new/read"));
  assert.deepEqual(readCall.body, {});
  assert.equal(h.ids.get("autonomy-unread-count").textContent, "1");
  console.log("ok - autonomy unread and toast are independent of task notifications");
}

async function testRequestDraftSurvivesPollAndFailure() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 5 };
  snapshot.requests = [{ id: "r1", title: "是否继续读取这组资料？", status: "pending", version: 8 }];
  const h = await harness({ snapshot });
  await h.evaluate("autonomyOpen(true)");
  const requestBox = h.ids.get("autonomy-requests");
  assert.equal(requestBox.querySelector(".autonomy-request-input"), null,
    "no independent answer endpoint means no misleading input or submit button");
  assert.equal(requestBox.querySelector(".autonomy-inline-button"), null,
    "the panel never renders a button that would post to a missing endpoint");
  assert.equal(h.calls.bodies.some((item) => item.path.includes("/requests/")), false);
  console.log("ok - requests are informational until a real answer endpoint exists");
}

async function testObserverAndAuthorizationSemanticsAreVisible() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 4 };
  snapshot.observer = { connected: false, error: "观察进程尚未就绪" };
  snapshot.watches = [{ id: "w1", path: "C:\\Research\\paper.md", status: "active",
    reason: "确认修订版", recursive: false, applied_version: 3, applied_control: 4 }];
  const h = await harness({ snapshot });
  await h.evaluate("autonomyOpen(true)");
  const observer = h.ids.get("autonomy-watches").querySelector(".autonomy-observer");
  const observerText = (node) => node.children.length
    ? node.children.map(observerText).join(" ") : node.textContent;
  assert.match(observerText(observer), /观察服务未连接/);
  assert.match(observerText(observer), /观察进程尚未就绪/);
  const html = fs.readFileSync(path.join(ROOT, "nexus", "web", "index.html"), "utf8");
  assert.match(html, /不授予外部写入权限/);
  assert.match(html, /现有持久任务审批/);
  assert.match(html, /调整 Token 总额度不会清零已用额度/);
  console.log("ok - observer connection and MCP authorization limits are explicit");
}

async function testSettingsChangeDoesNotResetBudgetOrDraft() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 11 };
  snapshot.budget = { limit: 120000, spent: 9000, reserved: 500, remaining: 110500, unknown: 1 };
  const h = await harness({ snapshot });
  h.ids.get("autonomy-token-limit").value = "150000";
  h.evaluate("autonomyMarkSettingsDirty()");
  await h.evaluate("autonomySaveSettings()");
  const budget = h.evaluate("autonomyView.snapshot.budget");
  assert.equal(budget.spent, 9000, "changing the limit must not zero spent usage");
  assert.equal(budget.reserved, 500);
  assert.equal(budget.unknown, 1);
  assert.equal(h.evaluate("autonomyView.snapshot.control.state"), "enabled",
    "a settings save does not pretend to enable or disable autonomy");
  console.log("ok - changing the token limit preserves spent, reserved, and unknown usage");
}

async function testPanelIsAvailableOutsideLing() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "paused", version: 4 };
  const h = await harness({ snapshot });
  await h.evaluate("openSession('legacy')");
  const toast = h.evaluate("$('autonomy-toast')");
  toast.hidden = false;
  toast.style.display = "";
  h.ids.get("autonomy-toggle").handlers.click();
  assert.equal(h.evaluate("lingView.active"), false,
    "opening autonomy does not navigate away from an ordinary session");
  assert.equal(h.evaluate("autonomyView.open"), true);
  assert.equal(h.ids.get("autonomy-region").hidden, false);
  h.ids.get("autonomy-close").handlers.click();
  h.ids.get("autonomy-toast-view").handlers.click();
  assert.equal(h.evaluate("autonomyView.open"), true);
  assert.equal(h.evaluate("$('autonomy-toast').hidden"), true,
    "viewing detail closes the passive toast");
  console.log("ok - autonomy detail is available from ordinary pages");
}

async function testEventSourceCanLoadAndKeepOpen() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 4 };
  snapshot.messages = [{ id: "m1", text: "来源可追溯的消息", at: "2026-10-21T02:00:00Z",
    read_at: null, sources: ["event:1", "note:owner"] }];
  const h = await harness({ snapshot });
  await h.evaluate("autonomyOpen(true)");
  const sources = h.ids.get("autonomy-messages").querySelector(".autonomy-sources");
  const event = sources.querySelector(".autonomy-source-event");
  assert.ok(event, "event sources get an expandable detail");
  assert.match(event.querySelector(".autonomy-source-view-label").textContent, /查看依据/);
  assert.ok(sources.querySelector(".autonomy-source"), "non-event references remain plain text");
  event.open = true;
  event.handlers.toggle();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(event.querySelector(".autonomy-source-content").textContent, /"实/);
  const first = h.calls.paths.find((value) => value.includes("/api/autonomy/sources/event%3A1"));
  assert.ok(first && first.includes("offset=0") && first.includes("limit=8000"));
  const more = event.querySelector(".autonomy-inline-button");
  assert.ok(more, "a partial source exposes the next-page action");
  more.handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(event.querySelector(".autonomy-source-content").textContent, /来源原文/);
  await h.evaluate("autonomyPoll()");
  const rebuilt = h.ids.get("autonomy-messages").querySelector(".autonomy-source-event");
  assert.equal(rebuilt.open, true, "a poll does not collapse an expanded source");
  assert.match(rebuilt.querySelector(".autonomy-source-content").textContent, /实际来源原文/);
  console.log("ok - event sources load real text, page forward, and stay open across polls");
}

async function testSourceCanLoadNextPage() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 4 };
  snapshot.messages = [{ id: "m1", text: "分页来源", at: "2026-10-21T02:00:00Z",
    read_at: null, sources: ["event:2"] }];
  const h = await harness({ snapshot });
  await h.evaluate("autonomyOpen(true)");
  const event = h.ids.get("autonomy-messages").querySelector(".autonomy-source-event");
  event.open = true;
  event.handlers.toggle();
  await new Promise((resolve) => setImmediate(resolve));
  const more = event.querySelector(".autonomy-inline-button");
  assert.ok(more, "a truncated source exposes a next-page button");
  more.handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(event.querySelector(".autonomy-source-content").textContent, /第二段内容/);
  assert.ok(h.calls.paths.some((value) => value.includes("offset=10")),
    "the next page uses next_offset from the source response");
  console.log("ok - source details load subsequent pages without replacing earlier content");
}

async function testMissingSourceShowsErrorWithoutInventingText() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 4 };
  snapshot.messages = [{ id: "m1", text: "缺失来源", at: "2026-10-21T02:00:00Z",
    read_at: null, sources: ["event:404"] }];
  const h = await harness({ snapshot });
  await h.evaluate("autonomyOpen(true)");
  const event = h.ids.get("autonomy-messages").querySelector(".autonomy-source-event");
  event.open = true;
  event.handlers.toggle();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(event.querySelector(".autonomy-source-error").textContent, /来源不存在/);
  assert.equal(event.querySelector(".autonomy-source-content"), null,
    "a failed lookup never fabricates a source excerpt");
  console.log("ok - missing event sources show an error without fabricated content");
}

async function testSourceFailureRetryKeepsOpenState() {
  const snapshot = baseSnapshot();
  snapshot.control = { state: "enabled", version: 4 };
  snapshot.messages = [{ id: "m1", text: "来源重试", at: "2026-10-21T02:00:00Z",
    read_at: null, sources: ["event:3"] }];
  const h = await harness({ snapshot });
  h.setFailNext({ path: "/api/autonomy/sources/event%3A3?offset=0&limit=8000",
    status: 500, error: "暂时无法读取" });
  await h.evaluate("autonomyOpen(true)");
  const event = h.ids.get("autonomy-messages").querySelector(".autonomy-source-event");
  event.open = true;
  event.handlers.toggle();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(event.querySelector(".autonomy-source-error").textContent, /暂时无法读取/);
  assert.equal(event.querySelector(".autonomy-source-content"), null);
  const retry = event.querySelector(".autonomy-inline-button");
  assert.ok(retry, "a failed source offers an explicit retry");
  retry.handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(event.querySelector(".autonomy-source-content").textContent, /"实/);
  assert.equal(event.open, true);
  console.log("ok - source load failures stay explicit and retry in place");
}

async function main() {
  await testInitialDisabledAndNoReview();
  await testCandidateAndOutputLimitContract();
  await testConfiguredSettingsCollapseOnlyOnce();
  await testTimesPreserveConfiguredPrecision();
  await testSettingsDraftSurvivesPollingAndSaveUsesContract();
  await testEnableSavesDirtySettingsBeforeControl();
  await testPauseResumeTerminateAndConflict();
  await testGoalsWatchesPlansAndNoHiddenReasoning();
  await testGoalWithdrawUsesGoalVersion();
  await testMessagesAreIndependentAndToastOnlyForFreshUnread();
  await testRequestDraftSurvivesPollAndFailure();
  await testObserverAndAuthorizationSemanticsAreVisible();
  await testSettingsChangeDoesNotResetBudgetOrDraft();
  await testPanelIsAvailableOutsideLing();
  await testEventSourceCanLoadAndKeepOpen();
  await testSourceCanLoadNextPage();
  await testMissingSourceShowsErrorWithoutInventingText();
  await testSourceFailureRetryKeepsOpenState();
  console.log("frontend autonomy: all checks passed");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
