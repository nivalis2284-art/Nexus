"use strict";

// Focused frontend checks for the continuous Ling entry point. The browser
// scripts run in a VM with the same small DOM stand-in used by
// frontend_behavior.cjs; server routes are replaced with request recordings.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const LING = fs.readFileSync(path.join(ROOT, "nexus", "web", "ling.js"), "utf8");
const APP = fs.readFileSync(path.join(ROOT, "nexus", "web", "app.js"), "utf8");
const TASKS = fs.readFileSync(path.join(ROOT, "nexus", "web", "tasks.js"), "utf8");

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
    return this.descendants().filter((node) =>
      (!className || node.classList.contains(className)) &&
      (!tag || node.tagName === tag.toUpperCase()));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  contains(node) { return node === this || this.descendants().includes(node); }
}

function response(value) {
  return { ok: true, status: 200, statusText: "OK", async json() { return structuredClone(value); } };
}

async function harness() {
  const ids = new Map();
  const document = {
    hidden: false,
    readyState: "complete",
    createElement(tag) { return new Node(tag); },
    getElementById(id) { if (!ids.has(id)) ids.set(id, new Node("div")); return ids.get(id); },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  const calls = { paths: [], bodies: [] };
  const lingState = {
    session_id: "ling-stable",
    items: [],
    running: false,
    queue_count: 0,
    inputs: [],
    tasks: [],
    notifications: [],
    unread_count: 0,
  };
  const notifications = [
    { id: 3, task_id: "t1", title: "完成", text: "工作已完成", read_at: null, created_at: "2026-10-20T01:00:00Z" },
  ];
  let taskDetail = {
    id: "t1", control_version: 8, pending_interactions: [],
  };
  const fetch = async (requestPath, options) => {
    calls.paths.push(requestPath);
    if (options?.body) calls.bodies.push({ path: requestPath, body: JSON.parse(options.body) });
    if (requestPath === "/api/state") {
      return response({ providers: [], current: {}, effort: { levels: [], labels: {} }, sessions: [],
        permission: "ask", workspace: "C:\\tmp", profile: { context_window_tokens: 65536 } });
    }
    if (requestPath === "/api/ling") return response(structuredClone(lingState));
    if (requestPath.startsWith("/api/notifications?")) {
      return response({ notifications: requestPath.includes("after=0") ? [] : notifications,
        next: notifications.length ? 3 : 0, unread_count: 1 });
    }
    if (requestPath === "/api/ling/input") return response({ queued: true, input_id: "in1", session_id: "ling-stable" });
    if (requestPath === "/api/ling/stop") return response({ stopped: true });
    if (requestPath === "/api/tasks") return response({ tasks: [] });
    if (requestPath === "/api/tasks/t1") return response(structuredClone(taskDetail));
    if (requestPath.startsWith("/api/sessions/")) return response({ id: "legacy", title: "旧会话", items: [] });
    return response({});
  };
  const context = vm.createContext({
    document, window: { innerWidth: 1200 }, fetch, TextDecoder, TextEncoder, console,
    setTimeout() { return 1; }, clearTimeout() {}, confirm: () => true,
  });
  const load = (source, name) => vm.runInContext(source, context, { filename: name });
  load(APP, "nexus/web/app.js");
  load(LING, "nexus/web/ling.js");
  load(TASKS, "nexus/web/tasks.js");
  await new Promise((resolve) => setImmediate(resolve));
  return {
    context, ids, calls, document,
    evaluate(code) { return vm.runInContext(code, context); },
    snapshot() { return lingState; },
    setTaskDetail(value) { taskDetail = value; },
  };
}

async function testDefaultEntryUsesStableLingSession() {
  const h = await harness();
  const first = h.snapshot();
  first.items = [{ role: "assistant", text: "在。", at: "2026-10-20T01:00:00Z" }];
  await h.evaluate("openLing()");
  assert.equal(h.evaluate("lingView.active"), true, "the default page enters Ling");
  assert.equal(h.evaluate("lingView.sessionId"), "ling-stable");
  assert.equal(h.evaluate("state.items[0].text"), "在。");
  assert.match(h.ids.get("title").textContent, /灵/);
  console.log("ok - default Ling entry uses the server session");
}

async function testSendQueuesWithoutStopping() {
  const h = await harness();
  const data = h.snapshot();
  data.running = true;
  await h.evaluate("openLing()");
  h.ids.get("input").value = "另外问一个问题";
  const before = h.calls.bodies.length;
  await h.ids.get("composer").handlers.submit({ preventDefault() {} });
  const bodies = h.calls.bodies.slice(before);
  assert.equal(bodies.filter((call) => call.path === "/api/ling/stop").length, 0,
    "submitting while running must not call stop");
  const input = bodies.find((call) => call.path === "/api/ling/input");
  assert.equal(input.body.text, "另外问一个问题");
  assert.equal(input.body.request_id.startsWith("input-"), true);
  assert.equal(h.evaluate("state.running"), false, "Ling never opens a second chat stream");
  assert.equal(h.ids.get("send").classList.contains("stop"), false);
  assert.equal(h.ids.get("send").attributes["aria-label"], "发送");
  assert.equal(h.ids.get("ling-stop").hidden, false, "stop stays available as a separate button");
  assert.match(h.ids.get("input").value, /^$/, "queued input clears only after acceptance");
  console.log("ok - Ling enqueue is separate from an explicit stop");
}

async function testLegacyRunningNeverCapturesLingSend() {
  const h = await harness();
  const data = h.snapshot();
  data.running = true;
  await h.evaluate("openLing()");
  h.evaluate("state.running = true; state.sessionId = 'legacy-running';");
  h.ids.get("input").value = "旧会话仍在运行时的新问题";
  const before = h.calls.bodies.length;
  await h.ids.get("composer").handlers.submit({preventDefault() {}});
  const calls = h.calls.bodies.slice(before);
  assert.equal(calls.some((call) => call.path === "/api/chat/abort"), false,
    "Ling must not inherit the legacy send-means-stop rule");
  assert.ok(calls.some((call) => call.path === "/api/ling/input"), "new text enters the Ling queue");
  console.log("ok - legacy running state cannot turn a Ling send into an abort");
}

async function testTextIsVisibleBeforeLowLatencyPollReturns() {
  const h = await harness();
  let release;
  h.evaluate(`__lingInputDone = new Promise((resolve) => { __release = resolve; });`);
  h.evaluate(`__originalFetch = fetch; fetch = async (path, options) => {
    if (path === "/api/ling/input") {
      await __lingInputDone;
      return {ok: true, status: 200, statusText: "OK", json: async () => ({queued: true, input_id: "in2", session_id: "ling-stable"})};
    }
    return __originalFetch(path, options);
  };`);
  h.evaluate("openLing()");
  await new Promise((resolve) => setImmediate(resolve));
  h.evaluate(`lingView.items = []; lingView.itemsSignature = "[]"; state.items = [];`);
  h.ids.get("input").value = "低延迟模型请求";
  const pending = h.evaluate("submitLingMessage('低延迟模型请求')");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.evaluate("state.items.some((item) => item.text === '低延迟模型请求')"), true,
    "the user message appears immediately");
  h.evaluate("__release()");
  await pending;
  console.log("ok - Ling renders a queued message before its input response");
}

async function testInputStatusesMatchQueueContract() {
  const h = await harness();
  await h.evaluate("openLing()");
  h.evaluate(`lingView.inputs = [
    {id: "a", status: "pending", text: "排队的请求", error: ""},
    {id: "b", status: "failed", text: "失败的请求", error: "模型尚未配置"},
    {id: "c", status: "interrupted", text: "中断的请求", error: ""}
  ]; renderLingInputs(); renderLingStatus();`);
  const text = h.ids.get("ling-inputs").descendants().map((node) => node.textContent).join(" ");
  assert.match(text, /已接收/);
  assert.match(text, /模型尚未配置/);
  assert.match(text, /检查已生效操作后发送新消息继续/);
  assert.equal(h.ids.get("ling-resume").hidden, true,
    "interrupted inputs have no automatic resume button");
  h.evaluate(`lingView.inputs.push({id: "d", status: "held", text: "held", error: ""}); renderLingStatus();`);
  assert.equal(h.ids.get("ling-resume").hidden, false,
    "only held inputs offer explicit resume");
  console.log("ok - Ling input statuses expose queue, failure, and interruption accurately");
}

async function testEmptyLingChromeStaysHidden() {
  const h = await harness();
  await h.evaluate("openLing()");
  assert.equal(h.ids.get("ling-interaction").hidden, true,
    "an empty active interaction must not leave a visible box");
  h.evaluate(`lingView.inputs = [
    {id: "d", status: "done", text: "finished", error: ""},
    {id: "c", status: "cancelled", text: "cancelled", error: ""}
  ]; renderLingInputs();`);
  assert.equal(h.ids.get("ling-inputs").hidden, true,
    "finished inputs do not unhide an empty queue strip");
  console.log("ok - empty Ling status strips stay hidden");
}

async function testTaskPendingCountHandlesArray() {
  const h = await harness();
  await h.evaluate("openLing()");
  h.evaluate(`lingView.tasks = [
    {id: "a", title: "empty", state: "running", pending_interactions: []},
    {id: "b", title: "open", state: "waiting_user", pending_interactions: [{}]}
  ]; lingView.renderedTaskState = ""; renderLingTasks();`);
  const pills = h.ids.get("ling-tasks").querySelectorAll(".ling-task");
  const labels = pills.map((pill) => pill.descendants().map((node) => node.textContent).join(""));
  assert.doesNotMatch(labels[0], /待处理/);
  assert.match(labels[1], /待处理/);
  console.log("ok - Ling task pills count pending interactions correctly");
}

async function testPollingDoesNotRedrawStableMessages() {
  const h = await harness();
  const data = h.snapshot();
  data.items = [{ role: "assistant", text: "稳定回复", at: "2026-10-20T01:00:00Z" }];
  await h.evaluate("openLing()");
  let rendered = 0;
  h.evaluate(`__renderTranscript = renderTranscript; renderTranscript = () => { __rendered += 1; }; __rendered = 0;`);
  await h.evaluate("pollLing()");
  assert.equal(h.evaluate("__rendered"), 0, "identical snapshots do not rebuild the transcript");
  data.items.push({ role: "assistant", text: "新回复", at: "2026-10-20T01:01:00Z" });
  await h.evaluate("pollLing()");
  assert.equal(h.evaluate("__rendered"), 1);
  console.log("ok - Ling polling redraws only when items change");
}

async function testPollingKeepsDraftAndAttachment() {
  const h = await harness();
  await h.evaluate("openLing()");
  h.ids.get("input").value = "尚未发送的草稿";
  h.evaluate(`pendingFiles = [{ name: "keep.txt", size: 4, file: {type: "text/plain"} }];`);
  const data = h.snapshot();
  data.items = [{ role: "user", text: "服务端新输入", at: "2026-10-20T01:02:00Z" }];
  await h.evaluate("pollLing()");
  assert.equal(h.ids.get("input").value, "尚未发送的草稿");
  assert.equal(h.evaluate("pendingFiles[0].name"), "keep.txt");
  console.log("ok - Ling polling preserves drafts and attachments");
}

async function testInteractionDraftSurvivesPolling() {
  const h = await harness();
  const data = h.snapshot();
  data.interaction = { type: "approval", token: "tok", tool: "bash", arguments: { command: "ls" } };
  await h.evaluate("openLing()");
  const answer = h.ids.get("ling-interaction").querySelector(".ling-approval-answer");
  answer.value = "说明尚未提交";
  answer.handlers.input();
  await h.evaluate("pollLing()");
  assert.equal(h.ids.get("ling-interaction").querySelector(".ling-approval-answer").value,
    "说明尚未提交", "polling keeps the live interaction input");
  console.log("ok - Ling interaction draft survives a matching poll");
}

async function testInteractionClearsOnceWithoutEchoingHandledText() {
  const h = await harness();
  const data = h.snapshot();
  data.interaction = { type: "question", token: "q", questions: [{ question: "继续吗？" }] };
  await h.evaluate("openLing()");
  const input = h.ids.get("ling-interaction").querySelector(".ling-question-free");
  input.value = "继续";
  input.handlers.input();
  data.interaction = null;
  await h.evaluate("pollLing()");
  assert.equal(h.ids.get("ling-interaction").querySelector(".ling-question-free"), null);
  const handled = h.ids.get("ling-interaction").querySelector(".ling-interaction-expired");
  h.evaluate(`lingView.interactionHandled = true; lingView.interactionDraft = {key: "", value: {answers: {0: "继续"}, approval: ""}};`);
  await h.evaluate("pollLing()");
  assert.equal(h.ids.get("ling-interaction").querySelector(".ling-question-free"), null,
    "a handled interaction cannot come back from a stale local draft");
  console.log("ok - completed interaction clears once and does not echo answered input");
}

async function testNotificationsDedupeAndAnswerThroughTaskApi() {
  const h = await harness();
  await h.evaluate("openLing()");
  await h.evaluate("pollNotifications()");
  const counts = h.calls.paths.filter((value) => value.includes("unread=0")).length;
  assert.ok(counts >= 1, "startup requests unread notifications for backfill");
  assert.equal(h.evaluate("lingView.notifications.length"), 1);
  assert.equal(typeof h.ids.get("notifications-toggle").handlers.click, "function",
    "broken script loading must not leave the notification button inert");
  const data = h.snapshot();
  data.notifications = [{
    id: 6, task_id: "t1", kind: "interaction", title: "需要处理", text: "",
    interaction_id: "i6", read_at: null, created_at: "2026-10-20T01:00:00Z",
  }];
  await h.evaluate("pollLing()");
  assert.equal(h.evaluate("lingView.notifications.some((item) => item.id === 6)"), true,
    "/api/ling notifications enter the rendered feed");
  assert.ok(h.ids.get("notification-list").querySelector(".notification-handle"),
    "Ling polling alone can surface a notification action");
  await h.evaluate("pollNotifications({startup: true})");
  assert.equal(h.evaluate("lingView.notifications.length"), 2, "startup backfill is deduplicated by id");
  const interaction = { kind: "approval", task_id: "t1", interaction_id: "i1", control_version: 7 };
  h.evaluate(`lingView.interaction = ${JSON.stringify(interaction)}`);
  await h.evaluate("answerLingInteraction('允许')");
  const answer = h.calls.bodies.find((call) => call.path.endsWith("/interactions/i1/answer"));
  assert.equal(answer.body.answer, true);
  assert.equal(answer.body.expected_version, 7);
  assert.equal(answer.body.resume, true);
  console.log("ok - notification backfill dedupes and task approval reuses task detail API");
}

async function testNotificationHandlesRequestInline() {
  const h = await harness();
  const data = h.snapshot();
  data.notifications = [{
    id: 4, task_id: "t1", kind: "interaction", title: "需要授权", text: "批准写入",
    interaction_id: "i-live", current_state: "waiting_user", control_version: 4,
    read_at: null, created_at: "2026-10-20T01:00:00Z",
  }];
  h.setTaskDetail({
    id: "t1", control_version: 9, pending_interactions: [{
      id: "i-live", kind: "approval", control_version: 9,
      payload: { tool: "write_file", arguments: { path: "x" } },
    }],
  });
  await h.evaluate("openLing()");
  assert.equal(h.evaluate("lingView.sessionId"), "ling-stable");
  const notification = h.ids.get("notification-list").querySelector(".notification-handle");
  assert.ok(notification, "interaction notifications offer an inline handler");
  h.evaluate("$('notification-region').hidden = false");
  h.ids.get("input").value = "不能丢的草稿";
  notification.handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.evaluate("lingView.active"), true, "handling does not navigate away from Ling");
  assert.equal(h.ids.get("input").value, "不能丢的草稿");
  assert.match(h.ids.get("notification-list").querySelector(".notification-action-tool").textContent,
    /write_file/);
  await h.evaluate("submitNotificationAction(true)");
  const answer = h.calls.bodies.find((call) => call.path.endsWith("/interactions/i-live/answer"));
  assert.equal(answer.body.answer, true);
  assert.equal(answer.body.expected_version, 9, "approval uses fresh task detail version");
  assert.equal(h.evaluate("lingView.active"), true);
  console.log("ok - notification approval answers inline against fresh task detail");
}

async function testStaleNotificationShowsHandledState() {
  const h = await harness();
  const data = h.snapshot();
  data.notifications = [{
    id: 5, task_id: "t1", kind: "interaction", title: "已经处理的请求", text: "",
    interaction_id: "i-old", current_state: "waiting_user", control_version: 3, read_at: null,
    created_at: "2026-10-20T01:00:00Z",
  }];
  h.setTaskDetail({ id: "t1", control_version: 10, pending_interactions: [] });
  await h.evaluate("openLing()");
  const handle = h.ids.get("notification-list").querySelector(".notification-handle");
  handle.handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  assert.match(h.ids.get("notification-list").querySelector(".notification-action-message").textContent,
    /已处理或已过期/);
  assert.equal(h.calls.bodies.filter((call) => call.path.includes("/interactions/")).length, 0,
    "a stale notification cannot send an approval");
  console.log("ok - stale notification answers are rejected from fresh task detail");
}

async function testNotificationFeedKeepsOpenDraftAcrossPoll() {
  const h = await harness();
  const data = h.snapshot();
  data.notifications = [{
    id: 7, task_id: "t1", kind: "interaction", title: "待处理", text: "",
    interaction_id: "i7", read_at: null, created_at: "2026-10-20T01:00:00Z",
  }];
  h.setTaskDetail({
    id: "t1", control_version: 12, pending_interactions: [{
      id: "i7", kind: "question", control_version: 12,
      payload: { questions: [{ question: "具体怎样继续？" }] },
    }],
  });
  await h.evaluate("openLing()");
  h.evaluate("$('notification-region').hidden = false");
  h.ids.get("notification-list").querySelector(".notification-handle").handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  const input = h.ids.get("notification-list").querySelector(".notification-action-input");
  assert.ok(input, "questions use the pending interaction payload");
  input.value = "尚未提交的回答";
  input.handlers.input();
  await h.evaluate("renderNotifications()");
  const again = h.ids.get("notification-list").querySelector(".notification-action-input");
  assert.equal(again.value, "尚未提交的回答", "an open notification question survives feed rerender");
  assert.equal(h.evaluate("lingView.active"), true);
  console.log("ok - notification interaction drafts survive feed updates");
}

async function testNotificationSubmitStaysBoundDuringPolling() {
  const h = await harness();
  const data = h.snapshot();
  data.notifications = [{
    id: 8, task_id: "t1", kind: "interaction", title: "审批", text: "",
    interaction_id: "i8", read_at: null, created_at: "2026-10-20T01:00:00Z",
  }];
  h.setTaskDetail({
    id: "t1", control_version: 3, pending_interactions: [{
      id: "i8", kind: "approval", control_version: 3,
      payload: { tool: "write_file", arguments: { path: "x" } },
    }],
  });
  await h.evaluate("openLing()");
  h.evaluate("$('notification-region').hidden = false");
  h.ids.get("notification-list").querySelector(".notification-handle").handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  h.evaluate(`
    __notificationGate = new Promise((resolve) => { __releaseNotification = resolve; });
    __originalSubmitNotification = submitNotificationAction;
    submitNotificationAction = async (approval) => {
      await __notificationGate;
      return __originalSubmitNotification(approval);
    };
    __sendGate = new Promise((resolve) => { __releaseSend = resolve; });
    __originalAnswerWithGate = answerLingInteraction;
    answerLingInteraction = async (value) => {
      await __sendGate;
      return __originalAnswerWithGate(value);
    };
  `);
  const submitting = h.evaluate("submitNotificationAction(true)");
  await new Promise((resolve) => setImmediate(resolve));
  await h.evaluate("pollLing()");
  h.evaluate("__releaseNotification()");
  h.evaluate("__releaseSend()");
  await submitting;
  const answer = h.calls.bodies.find((call) => call.path.endsWith("/interactions/i8/answer"));
  assert.equal(answer.body.answer, true, "polling cannot sever the in-flight approval state");
  console.log("ok - notification approval submission keeps a stable interaction state");
}

async function testNotificationAutoOpensOnlyForFreshUnread() {
  const h = await harness();
  const data = h.snapshot();
  data.notifications = [];
  await h.evaluate("openLing()");
  h.evaluate("setNotificationsOpen(false)");
  data.unread_count = 1;
  data.notifications = [{
    id: 9, task_id: "t1", kind: "completed", title: "工作完成", text: "结果已保存",
    read_at: null, created_at: "2026-10-20T01:00:00Z",
  }];
  h.ids.get("input").value = "继续写的草稿";
  await h.evaluate("pollLing()");
  assert.equal(h.evaluate("lingView.notificationsOpen"), true,
    "a fresh unread notification opens the passive in-page feed");
  assert.equal(h.ids.get("input").value, "继续写的草稿");
  h.evaluate("setNotificationsOpen(false)");
  await h.evaluate("pollLing()");
  assert.equal(h.evaluate("lingView.notificationsOpen"), false,
    "polling a notification the user dismissed does not reopen it");
  assert.equal(h.evaluate("lingView.active"), true);
  console.log("ok - fresh unread notifications auto-open once without stealing the draft");
}

async function testLegacyEntryRemains() {
  const h = await harness();
  await h.evaluate("openLing()");
  await h.evaluate("openSession('legacy')");
  assert.equal(h.evaluate("lingView.active"), false);
  assert.equal(h.ids.get("ling-bar").hidden, true);
  assert.equal(h.evaluate("state.sessionId"), "legacy");
  console.log("ok - legacy chat remains reachable and hides Ling chrome");
}

async function testLegacyCompletionCannotStealLingView() {
  const h = await harness();
  await h.evaluate("openLing()");
  let refreshed = 0;
  h.evaluate(`
    state.sessionId = "legacy-finished";
    refresh = async () => ({});
    openSession = async () => { __legacyRefreshes += 1; };
    __legacyRefreshes = 0;
    __originalFetch = fetch;
    fetch = async (path, options) => {
      if (path === "/api/chat") return {ok: true, status: 200, statusText: "OK",
        body: {getReader: () => {let sent = false; return {read: async () => {
          if (sent) return {done: true};
          sent = true;
          return {done: false, value: new TextEncoder().encode(
            JSON.stringify({type: "done", data: {}}) + "\\n")};
        }};}}};
      return __originalFetch(path, options);
    };
  `);
  await h.evaluate("send('旧会话请求')");
  assert.equal(h.evaluate("lingView.active"), true, "Ling remains the current view");
  assert.equal(h.evaluate("__legacyRefreshes"), 0, "finishing the old stream does not call openSession");
  console.log("ok - legacy stream completion respects the current Ling view");
}

async function testResumeOnlyAppliesToHeldInput() {
  const h = await harness();
  await h.evaluate("openLing()");
  await h.evaluate("resumeLing()");
  assert.equal(h.calls.bodies.filter((call) => call.path === "/api/ling/resume").length, 1);
  assert.equal(h.calls.bodies.filter((call) => call.path === "/api/ling/stop").length, 0);
  console.log("ok - Ling resume is explicit and separate from stop");
}

async function main() {
  await testDefaultEntryUsesStableLingSession();
  await testSendQueuesWithoutStopping();
  await testLegacyRunningNeverCapturesLingSend();
  await testTextIsVisibleBeforeLowLatencyPollReturns();
  await testInputStatusesMatchQueueContract();
  await testEmptyLingChromeStaysHidden();
  await testTaskPendingCountHandlesArray();
  await testPollingDoesNotRedrawStableMessages();
  await testPollingKeepsDraftAndAttachment();
  await testInteractionDraftSurvivesPolling();
  await testInteractionClearsOnceWithoutEchoingHandledText();
  await testNotificationsDedupeAndAnswerThroughTaskApi();
  await testNotificationHandlesRequestInline();
  await testStaleNotificationShowsHandledState();
  await testNotificationFeedKeepsOpenDraftAcrossPoll();
  await testNotificationSubmitStaysBoundDuringPolling();
  await testNotificationAutoOpensOnlyForFreshUnread();
  await testLegacyEntryRemains();
  await testLegacyCompletionCannotStealLingView();
  await testResumeOnlyAppliesToHeldInput();
  console.log("frontend continuity: all checks passed");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
