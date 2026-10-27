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
const LIVE = fs.readFileSync(path.join(ROOT, "nexus", "web", "live.js"), "utf8");

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
    this._text = "";
    this.selectionStart = 0;
    this.selectionEnd = 0;
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.files = [];
  }
  set innerHTML(value) { this.children = []; this._innerHTML = String(value || ""); }
  get innerHTML() { return this._innerHTML || ""; }
  get textContent() {
    if (this.children.length) return this.children.map((child) => child.textContent).join("");
    return this._text;
  }
  set textContent(value) { this.children = []; this._text = String(value ?? ""); }
  get childNodes() { return this.children; }
  appendChild(child) { child.parent = this; this.children.push(child); return child; }
  insertBefore(child, before) {
    if (child.parent) child.parent.children = child.parent.children.filter((item) => item !== child);
    const index = before ? this.children.indexOf(before) : -1;
    child.parent = this;
    if (index < 0) this.children.push(child); else this.children.splice(index, 0, child);
    return child;
  }
  replaceChildren(...values) {
    for (const child of this.children) child.parent = null;
    this.children = [];
    for (const child of values) this.appendChild(child);
  }
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
  focus(options) { this.focusOptions = options; }
  select() { this.selected = true; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
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
    usage: { input: 0, cached: 0, created: 0, output: 0, total: 0 },
    context: null,
    live: null,
    live_revision: 0,
  };
  const notifications = [
    { id: 3, task_id: "t1", title: "完成", text: "工作已完成", read_at: null, created_at: "2026-10-20T01:00:00Z" },
  ];
  let taskDetail = {
    id: "t1", control_version: 8, pending_interactions: [],
  };
  let fetchHandler = null;
  const fetch = async (requestPath, options) => {
    if (fetchHandler) {
      const result = await fetchHandler(requestPath, options);
      if (result) return result;
    }
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
    if (requestPath.startsWith("/api/tasks/")) return response(structuredClone(taskDetail));
    if (requestPath.startsWith("/api/sessions/")) return response({ id: "legacy", title: "旧会话", items: [] });
    return response({});
  };
  const context = vm.createContext({
    document, window: { innerWidth: 1200, addEventListener() {} }, fetch, TextDecoder, TextEncoder, console,
    renderContent(text) { const node = document.createElement("div"); node.textContent = text; return node; },
    toolNode(item) { const node = document.createElement("details"); node.className="step"; node.textContent=(item.name||"tool")+(item.text||""); return node; },
    EventSource: class { constructor(url) { this.url = url; this.closed = false; } close() { this.closed = true; } },
    setTimeout() { return 1; }, clearTimeout() {}, confirm: () => true,
  });
  const load = (source, name) => vm.runInContext(source, context, { filename: name });
  load(`function renderContent(text) { const node = el("div", "markdown"); node.textContent = text; return node; }\nfunction toolNode(item) { const node = el("div", "step tool"); node.textContent = item.name || ""; return node; }`, "presentation-stub.js");
  load(APP, "nexus/web/app.js");
  load(LIVE, "nexus/web/live.js");
  load(LING, "nexus/web/ling.js");
  load(TASKS, "nexus/web/tasks.js");
  await new Promise((resolve) => setImmediate(resolve));
  return {
    context, ids, calls, document,
    evaluate(code) { return vm.runInContext(code, context); },
    snapshot() { return lingState; },
    setTaskDetail(value) { taskDetail = value; },
    setFetch(handler) { fetchHandler = handler; },
  };
}

async function testDefaultEntryUsesStableLingSession() {
  const h = await harness();
  const first = h.snapshot();
  first.items = [{ role: "assistant", text: "在。", at: "2026-10-20T01:00:00Z" }];
  await h.evaluate("openLing()");
  await h.evaluate("pollLing()");
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
  assert.equal(h.evaluate("lingView.running"), true, "Ling reflects the server's active turn");
  assert.equal(h.calls.paths.includes("/api/chat"), false, "Ling never opens a second chat stream");
  assert.equal(h.ids.get("send").classList.contains("stop"), false);
  assert.equal(h.ids.get("send").attributes["aria-label"], "发送");
  assert.equal(h.ids.get("ling-stop").hidden, false, "stop stays available as a separate button");
  assert.match(h.ids.get("input").value, /^$/, "queued input clears only after acceptance");
  console.log("ok - Ling enqueue is separate from an explicit stop");
}

async function testSlowSendKeepsTextTypedMeanwhile() {
  const h = await harness();
  await h.evaluate("openLing()");
  h.evaluate(`__sendGate = new Promise((resolve) => { __releaseSend = resolve; });`);
  h.setFetch(async (path) => {
    if (path === "/api/ling/input") {
      await __sendGate;
      return response({queued: true, input_id: "slow", session_id: "ling-stable"});
    }
    return null;
  });
  h.ids.get("input").value = "first message";
  const sending = h.ids.get("composer").handlers.submit({preventDefault() {}});
  await new Promise((resolve) => setImmediate(resolve));
  h.ids.get("input").value = "typed while sending";
  h.evaluate("__releaseSend()");
  await sending;
  assert.equal(h.ids.get("input").value, "typed while sending",
    "a slow send does not erase text typed after submitting");
  console.log("ok - a slow Ling send preserves newer composer text");
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
  assert.equal(h.ids.get("ling-requests").hidden, true,
    "an empty request area must not leave a visible box");
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

async function testUnchangedSnapshotKeepsFocusAndCaret() {
  const h = await harness();
  await h.evaluate("openLing()");
  const input = h.ids.get("input");
  input.value = "编辑中的草稿";
  input.selectionStart = 3;
  input.selectionEnd = 3;
  await h.evaluate("pollLing()");
  assert.equal(input.value, "编辑中的草稿");
  assert.equal(input.selectionStart, 3);
  assert.equal(input.selectionEnd, 3);
  console.log("ok - an unchanged snapshot keeps the draft caret");
}

async function testScrollRestoresBottomAcrossHiddenPanel() {
  const h = await harness();
  await h.evaluate("openLing()");
  const scroller = h.ids.get("scroller");
  scroller.hidden = true;
  scroller.scrollTop = 260;
  scroller.scrollHeight = 300;
  scroller.clientHeight = 40;
  scroller.scrollTop = 0;
  h.snapshot().items = [{role: "assistant", text: "新内容", at: "2026-10-20T01:03:00Z"}];
  scroller.scrollHeight = 900;
  scroller.hidden = false;
  await h.evaluate("openLing()");
  assert.equal(scroller.scrollTop, 900,
    "a reader at the bottom returns to the bottom after hidden panel and new messages");
  console.log("ok - Ling scroll restores bottom after a hidden panel and new messages");
}

async function testMainRequestPanelDedupesByToken() {
  const h = await harness();
  const data = h.snapshot();
  data.main_requests = [
    {id: "m1", kind: "approval", status: "pending", payload: {tool: "write_file", arguments: {path: "x"}}},
    {id: "m1", kind: "approval", status: "pending", payload: {tool: "write_file", arguments: {path: "x"}}},
  ];
  await h.evaluate("openLing()");
  assert.equal(h.ids.get("ling-requests").querySelectorAll(".ling-request").length, 1,
    "main requests dedupe by their durable token");
  const panel = h.ids.get("ling-requests").querySelector(".ling-request");
  h.ids.get("input").value = "保留中的草稿";
  await h.evaluate("pollLing()");
  assert.equal(h.ids.get("input").value, "保留中的草稿");
  panel.querySelector(".ling-approval-allow").handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  const answer = h.calls.bodies.find((call) => call.path === "/api/approval");
  assert.equal(answer.body.token, "m1");
  assert.equal(answer.body.allowed, true);
  console.log("ok - main approvals keep one card and answer their own token");
}

async function testMainQuestionDraftsArePerRequest() {
  const h = await harness();
  const data = h.snapshot();
  data.main_requests = [
    {id: "qa", kind: "question", status: "pending", payload: {questions: [{question: "怎么继续？"}]}},
    {id: "qb", kind: "question", status: "pending", payload: {questions: [{question: "另一个问题"}]}},
  ];
  await h.evaluate("openLing()");
  const panels = h.ids.get("ling-requests").querySelectorAll(".ling-request");
  const first = panels[0].querySelector(".ling-question-free");
  first.value = "保留回答";
  first.handlers.input();
  await h.evaluate("pollLing()");
  const again = h.ids.get("ling-requests").querySelectorAll(".ling-request")[0]
    .querySelector(".ling-question-free");
  assert.equal(again.value, "保留回答");
  panels[1].querySelector(".ling-question-free").value = "另一个回答";
  panels[1].querySelector(".ling-question-free").handlers.input();
  panels[1].querySelector(".ling-request-submit").handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  const answer = h.calls.bodies.find((call) => call.path === "/api/answer");
  assert.equal(answer.body.token, "qb");
  assert.deepEqual(answer.body.answers, ["另一个回答"]);
  console.log("ok - main question drafts and tokens stay with their own request");
}

async function testUnknownMainApprovalIsReadOnly() {
  const h = await harness();
  const data = h.snapshot();
  data.main_requests = [{id: "u1", kind: "approval", status: "unknown",
    payload: {tool: "write_file", arguments: {path: "maybe.txt"}}}];
  await h.evaluate("openLing()");
  const panel = h.ids.get("ling-requests").querySelector(".ling-request");
  assert.equal(panel.querySelector(".ling-approval-allow"), null);
  assert.equal(panel.querySelector(".ling-approval-deny"), null);
  assert.match(panel.descendants().map((node) => node.textContent).join(" "), /不会自动重放/);
  panel.querySelector(".ling-request-reviewed").handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  const body = h.calls.bodies.find((call) => call.path === "/api/approval");
  assert.equal(body.body.token, "u1");
  assert.equal(body.body.reviewed_unknown, true);
  assert.equal(Object.hasOwn(body.body, "allowed"), false);
  console.log("ok - unknown approvals dismiss without granting or replaying");
}

async function testWorkRequestUsesTaskEndpoint() {
  const h = await harness();
  const data = h.snapshot();
  data.main_requests = [];
  data.tasks = [{id: "t1", title: "写入报告", state: "waiting_user", control_version: 9,
    pending_interactions: [{id: "w1", kind: "question", control_version: 9,
      payload: {questions: [{question: "用哪种格式？"}]}}]}];
  await h.evaluate("openLing()");
  const section = h.ids.get("ling-requests").querySelector(".ling-request.work");
  assert.ok(section);
  const input = section.querySelector(".ling-question-free");
  input.value = "Markdown";
  input.handlers.input();
  await h.evaluate("pollLing()");
  const again = h.ids.get("ling-requests").querySelector(".ling-request.work");
  assert.equal(again.querySelector(".ling-question-free").value, "Markdown");
  again.querySelector(".ling-request-submit").handlers.click();
  await new Promise((resolve) => setImmediate(resolve));
  const answer = h.calls.bodies.find((call) => call.path.endsWith("/tasks/t1/interactions/w1/answer"));
  assert.equal(answer.body.answer, "Markdown");
  assert.equal(answer.body.expected_version, 9);
  console.log("ok - work requests answer their own task interaction");
}

async function testComposerTargetAndPauseAll() {
  const h = await harness();
  const data = h.snapshot();
  data.tasks = [
    {id: "t1", title: "第一项", state: "running", pending_interactions: []},
    {id: "t2", title: "第二项", state: "completed", pending_interactions: []},
  ];
  await h.evaluate("openLing()");
  assert.equal(h.ids.get("composer-task").children.length, 3);
  h.ids.get("composer-task").value = "t1";
  h.ids.get("input").value = "保留原格式";
  await h.ids.get("composer").handlers.submit({preventDefault() {}});
  const input = h.calls.bodies.find((call) => call.path === "/api/ling/input");
  assert.equal(input.body.task_id, "t1");
  assert.equal(h.ids.get("composer-task").value, "");
  assert.equal(h.ids.get("ling-pause-all").hidden, false);
  await h.evaluate("pauseAllWorks()");
  assert.equal(h.calls.bodies.filter((call) => call.path === "/api/ling/pause-all").length, 1);
  assert.equal(h.calls.bodies.some((call) => call.path === "/api/ling/stop"), false);
  console.log("ok - composed work targeting and pause-all use separate routes");
}
async function testResumeOnlyAppliesToHeldInput() {
  const h = await harness();
  await h.evaluate("openLing()");
  await h.evaluate("resumeLing()");
  assert.equal(h.calls.bodies.filter((call) => call.path === "/api/ling/resume").length, 1);
  assert.equal(h.calls.bodies.filter((call) => call.path === "/api/ling/stop").length, 0);
  console.log("ok - Ling resume is explicit and separate from stop");
}

async function testTaskRefreshPreservesOpenPanelAndControls() {
  const h = await harness();
  h.setTaskDetail({id: "t1", state: "paused", steps: [{content: "test", verification: "unverified"}], pending_interactions: []});
  await h.evaluate('openTask("t1")');
  const controls = h.ids.get("task-steps").children[0];
  const version = h.evaluate("state.viewVersion");
  await h.evaluate('openTask("t1", {refresh: true})');
  assert.equal(h.ids.get("task-panel").hidden, false);
  assert.equal(h.evaluate("state.viewVersion"), version);
  assert.equal(h.ids.get("task-steps").children[0], controls);
  assert.equal(h.evaluate('typeof renderTaskActions + " " + typeof taskCommand'),
    "undefined undefined", "work details expose facts without manual controls");
  console.log("ok - task refresh preserves read-only panels");
}

async function testLingSendCannotOverwriteAnotherView() {
  for (const fail of [false, true]) {
    const h = await harness();
    await h.evaluate("openLing()");
    h.evaluate('uploadFiles = () => new Promise((resolve, reject) => { globalThis.releaseUpload = resolve; globalThis.rejectUpload = reject; })');
    h.ids.get("input").value = "send";
    const sending = h.ids.get("composer").handlers.submit({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
    h.evaluate('clearLingView(); state.viewVersion++; state.sessionId = "other"; state.items = [{role: "user", text: "other"}]; renderTranscript(); $("input").value = "other draft"');
    if (fail) h.evaluate('rejectUpload(new Error("upload failed"))');
    else h.evaluate('releaseUpload([])');
    await sending;
    assert.equal(h.evaluate("state.items[0].text"), "other");
    assert.equal(h.evaluate("state.sessionId"), "other");
    const transcript = h.ids.get("transcript").descendants().map((node) => node.textContent).join(" ");
    assert.match(transcript, /other/);
    assert.doesNotMatch(transcript, /send/);
    assert.equal(h.ids.get("input").value, "other draft");
  }
  console.log("ok - Ling send success and failure respect view changes");
}

async function testLingSendFailureAfterReturningRollsBackCurrentView() {
  for (const fail of [false, true]) {
    const h = await harness();
    await h.evaluate("openLing()");
    const version = h.evaluate("state.viewVersion");
    h.evaluate('uploadFiles = () => new Promise((resolve, reject) => { globalThis.releaseUpload = resolve; globalThis.rejectUpload = reject; })');
    h.ids.get("input").value = "send after return";
    const sending = h.ids.get("composer").handlers.submit({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
    h.evaluate('clearLingView(); state.viewVersion++');
    const returning = h.evaluate("openLing()");
    await new Promise(resolve => setImmediate(resolve));
    if (fail) h.evaluate('rejectUpload(new Error("upload failed"))');
    else h.evaluate('releaseUpload([])');
    await sending;
    await returning;
    if (!fail) continue;
    assert.notEqual(h.evaluate("state.viewVersion"), version);
    assert.equal(h.evaluate("state.items.some((item) => item.text === 'send after return')"), false,
      "a failed optimistic message is removed from the visible transcript");
    assert.equal(h.evaluate("lingView.items.length"), 0);
    assert.equal(h.ids.get("input").value, "send after return",
      "a failed send restores the draft");
    assert.match(h.ids.get("status").textContent, /upload failed/);
  }
  console.log("ok - Ling send failure after returning rolls back the current view");
}

async function testLingSendFailureAfterReturningKeepsNewerDraft() {
  const h = await harness();
  await h.evaluate("openLing()");
  h.evaluate('uploadFiles = () => new Promise((resolve, reject) => { globalThis.releaseUpload = resolve; globalThis.rejectUpload = reject; })');
  h.ids.get("input").value = "failed message";
  const sending = h.ids.get("composer").handlers.submit({preventDefault() {}});
  await new Promise(resolve => setImmediate(resolve));
  h.evaluate('clearLingView(); state.viewVersion++');
  const returning = h.evaluate("openLing()");
  await new Promise(resolve => setImmediate(resolve));
  h.ids.get("input").value = "newer draft";
  h.evaluate('rejectUpload(new Error("upload failed"))');
  await sending;
  await returning;
  assert.equal(h.ids.get("input").value, "newer draft",
    "a failure does not replace a draft typed after returning");
  assert.equal(h.evaluate("state.items.length"), 0);
  console.log("ok - Ling send failure after returning preserves a newer draft");
}

async function testTaskControlErrorPreservesComposerDraft() {
  const h = await harness();
  await h.evaluate("openLing()");
  h.evaluate('api = async () => { throw new Error("HTTP 500"); }');
  h.ids.get("input").value = "keep my message";
  await h.ids.get("composer").handlers.submit({preventDefault() {}});
  assert.equal(h.ids.get("input").value, "keep my message");
  assert.equal(h.calls.bodies.some(call => call.path === "/api/chat"), false);
  console.log("ok - Ling send errors preserve the draft");
}

async function testNewWorkAppearsInSidebarAndCompletedCanBeTargeted() {
  const h = await harness();
  await h.evaluate("openLing()");
  h.snapshot().tasks = [{id:"finished",title:"saved result",state:"completed",pending_interactions:[]}];
  await h.evaluate("pollLing()");
  assert.equal(h.ids.get("tasks").children.length, 1, "main snapshots update the work sidebar");
  assert.ok(h.ids.get("composer-task").children.some(item => item.value === "finished"), "completed work remains targetable for a new iteration");
  h.snapshot().tasks = [];
  await h.evaluate("openLing()");
  assert.match(h.ids.get("transcript").descendants().map((node) => node.textContent).join(" "), /灵/,
    "an empty main entry still shows the Ling welcome");
  console.log("ok - new work reaches the sidebar and completed results stay targetable");
}

async function testDeferredSnapshotCannotEraseNewerLiveEvent() {
  const h = await harness();
  await h.evaluate("openLing()");
  let release;
  h.evaluate(`__snapshotGate = new Promise((resolve) => { __releaseSnapshot = resolve; });`);
  h.setFetch(async (path) => {
    if (path === "/api/ling") await __snapshotGate;
    return null;
  });
  const stale = h.snapshot();
  stale.live = null;
  stale.live_revision = 1;
  const polling = h.evaluate("pollLing()");
  await new Promise((resolve) => setImmediate(resolve));
  h.evaluate(`receiveRuntimeEvent({scope: "ling", owner_id: "ling-stable", type: "delta", revision: 2,
    live: {revision: 2, message_id: "m1", text: "new", thinking: ""}})`);
  h.evaluate("__releaseSnapshot()");
  await polling;
  assert.equal(h.evaluate("state.items.some((item) => item.text === 'new')"), true,
    "a snapshot that started before a live event cannot erase its text");
  assert.equal(h.evaluate("lingView.liveRevision"), 2);
  console.log("ok - a deferred Ling snapshot cannot erase newer live text");
}

async function testSavedTaskThinkingIsRendered() {
  const h = await harness();
  h.setTaskDetail({
    id: "t1", state: "paused", pending_interactions: [], live_revision: 0,
    messages: [{seq: 1, kind: "assistant", text: "", thinking: "saved reasoning", created_at: "2026-10-20T01:00:00Z"}],
  });
  await h.evaluate('openTask("t1")');
  const text = h.ids.get("task-messages").descendants().map((node) => node.textContent).join(" ");
  assert.match(text, /saved reasoning/, "saved thinking-only task records remain visible");
  console.log("ok - task records render saved thinking");
}

async function testTaskLiveUsesSameOrderedIdentityAsSavedRecords() {
  const h = await harness();
  h.setTaskDetail({id:"t1", state:"running", live_revision:1,
    messages:[{seq:1,kind:"assistant",message_id:"m1",text:"",thinking:"saved thought"}],
    live:{revision:1,message_id:"m1",thinking:"live thought",
      tool:{id:"c0",parent_message_id:"m1",name:"bash",arguments:{command:"true"},content:"ok",ok:true}}});
  await h.evaluate("openTask('t1')");
  const text=h.ids.get("task-messages").descendants().map(n=>n.textContent).join(" ");
  assert.match(text,/live thought/); assert.match(text,/bash/);
  console.log("ok - task live records keep stable identity and ordered process");
}

async function testTaskSwitchDoesNotCarryPreviousLiveState() {
  const h = await harness();
  h.setTaskDetail({id: "t1", state: "running", live_revision: 5,
    live: {revision: 5, text: "first task", message_id: "m1"},
    pending_interactions: [], messages: []});
  await h.evaluate('openTask("t1")');
  h.setTaskDetail({id: "t2", state: "running", live_revision: 1,
    live: {revision: 1, text: "second task", message_id: "m2"},
    pending_interactions: [], messages: []});
  await h.evaluate('openTask("t2")');
  assert.equal(h.evaluate("taskView.current.live.text"), "second task");
  assert.equal(h.evaluate("taskView.liveRevision"), 1);
  const text = h.ids.get("task-messages").descendants().map((node) => node.textContent).join(" ");
  assert.match(text, /second task/);
  assert.doesNotMatch(text, /first task/);
  console.log("ok - task switches do not carry the previous live state");
}

async function main() {
  await testNewWorkAppearsInSidebarAndCompletedCanBeTargeted();
  await testTaskRefreshPreservesOpenPanelAndControls();
  await testLingSendCannotOverwriteAnotherView();
  await testLingSendFailureAfterReturningRollsBackCurrentView();
  await testLingSendFailureAfterReturningKeepsNewerDraft();
  await testTaskControlErrorPreservesComposerDraft();
  await testDefaultEntryUsesStableLingSession();
  await testSendQueuesWithoutStopping();
  await testSlowSendKeepsTextTypedMeanwhile();
  await testLegacyRunningNeverCapturesLingSend();
  await testTextIsVisibleBeforeLowLatencyPollReturns();
  await testInputStatusesMatchQueueContract();
  await testEmptyLingChromeStaysHidden();
  await testTaskPendingCountHandlesArray();
  await testPollingDoesNotRedrawStableMessages();
  await testPollingKeepsDraftAndAttachment();
  await testUnchangedSnapshotKeepsFocusAndCaret();
  await testScrollRestoresBottomAcrossHiddenPanel();
  await testMainRequestPanelDedupesByToken();
  await testMainQuestionDraftsArePerRequest();
  await testUnknownMainApprovalIsReadOnly();
  await testWorkRequestUsesTaskEndpoint();
  await testComposerTargetAndPauseAll();
  await testResumeOnlyAppliesToHeldInput();
  await testDeferredSnapshotCannotEraseNewerLiveEvent();
  await testSavedTaskThinkingIsRendered();
  await testTaskLiveUsesSameOrderedIdentityAsSavedRecords();
  await testTaskSwitchDoesNotCarryPreviousLiveState();
  console.log("frontend continuity: all checks passed");
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
