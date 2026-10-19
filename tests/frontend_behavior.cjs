"use strict";

// Behaviour tests for the real browser script. The app is loaded in a Node VM
// with a small DOM stand-in; send() and nodeFor() are the original functions.
// Run with: node tests/frontend_behavior.cjs

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const ROOT = path.resolve(__dirname, "..");
const APP = fs.readFileSync(path.join(ROOT, "nexus", "web", "app.js"), "utf8");
const PNG_A = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
const PNG_B = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";

class ClassList {
  constructor(node) {
    this.node = node;
  }

  values() {
    return String(this.node.className || "").split(/\s+/).filter(Boolean);
  }

  contains(name) {
    return this.values().includes(name);
  }

  add(...names) {
    const values = this.values();
    for (const name of names) if (!values.includes(name)) values.push(name);
    this.node.className = values.join(" ");
  }

  remove(...names) {
    this.node.className = this.values().filter((name) => !names.includes(name)).join(" ");
  }

  toggle(name, force) {
    const present = this.contains(name);
    const wanted = force === undefined ? !present : Boolean(force);
    if (wanted) this.add(name);
    else this.remove(name);
    return wanted;
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
    this.style = {};
    this.attributes = {};
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.open = false;
    this.disabled = false;
    this.files = [];
    this.onclick = null;
  }

  set innerHTML(value) {
    this.children = [];
    this._innerHTML = String(value || "");
  }

  get innerHTML() {
    return this._innerHTML || "";
  }

  appendChild(child) {
    child.parent = this;
    this.children.push(child);
    return child;
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

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener(name, handler) {
    if (name === "submit") this.__submitHandler = handler;
  }
  focus() {}
  select() {}
  showModal() {}
  close() {}

  getBoundingClientRect() {
    return { left: 10, top: 10, right: 110, bottom: 40, width: 100, height: 30 };
  }

  descendants() {
    const out = [];
    for (const child of this.children) {
      out.push(child, ...child.descendants());
    }
    return out;
  }

  querySelectorAll(selector) {
    const classMatch = selector.match(/\.([\w-]+)/);
    const tag = selector.match(/^[a-z][\w-]*$/i);
    const live = selector.includes('[data-live="1"]');
    return this.descendants().filter(
      (node) =>
        (!tag || node.tagName === tag[0].toUpperCase()) &&
        (!classMatch || node.classList.contains(classMatch[1])) &&
        (!live || node.dataset.live === "1")
    );
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  contains(node) {
    return node === this || this.descendants().includes(node);
  }
}

function initialState() {
  return {
    providers: [],
    current: { provider: "", model: "", effort: null },
    effort: { levels: [], labels: {}, current: null, mapping: null },
    sessions: [],
    permission: "ask",
    workspace: "C:\\tmp",
    profile: { context_window_tokens: 65536 },
  };
}

function streamResponse(events) {
  const encoded = new TextEncoder().encode(
    events.map((event) => JSON.stringify(event) + "\n").join("")
  );
  let sent = false;
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    body: {
      getReader() {
        return {
          async read() {
            if (sent) return { value: undefined, done: true };
            sent = true;
            return { value: encoded, done: false };
          },
        };
      },
    },
  };
}

function jsonResponse(value) {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    async json() {
      return structuredClone(value);
    },
  };
}

async function loadHarness(options = {}) {
  const ids = new Map();
  const document = {
    createElement(tag) {
      return new FakeNode(tag);
    },
    getElementById(id) {
      if (!ids.has(id)) ids.set(id, new FakeNode("div"));
      return ids.get(id);
    },
    querySelectorAll() {
      return [];
    },
    addEventListener() {},
  };

  const calls = { chatBodies: [], paths: [] };
  const stateValue = initialState();
  let chatEvents = [];
  const fetch = async (requestPath, options) => {
    calls.paths.push(requestPath);
    if (requestPath === "/api/state") return jsonResponse(stateValue);
    if (requestPath === "/api/chat") {
      calls.chatBodies.push(JSON.parse(options.body));
      return streamResponse(chatEvents);
    }
    if (requestPath === "/api/task-control") {
      return {...jsonResponse({error: "The instruction does not identify exactly one task."}), ok: false};
    }
    if (requestPath.startsWith("/api/sessions/")) {
      return jsonResponse({ id: "s1", title: "测试", items: [] });
    }
    return jsonResponse({});
  };

  const context = vm.createContext({
    document,
    window: { innerWidth: 1200 },
    fetch,
    TextDecoder,
    TextEncoder,
    console,
    setTimeout: options.setTimeout || setTimeout,
    clearTimeout: options.clearTimeout || clearTimeout,
    confirm: () => true,
    FileReader: class {
      readAsDataURL(file) {
        this.result = "data:" + file.type + ";base64," + file.data;
        this.onload();
      }
    },
  });
  vm.runInContext(APP, context, { filename: "nexus/web/app.js" });
  await new Promise((resolve) => setImmediate(resolve));
  if (options.tasks) {
    vm.runInContext(fs.readFileSync(path.join(ROOT, "nexus", "web", "tasks.js"), "utf8"), context,
      { filename: "nexus/web/tasks.js" });
    await new Promise((resolve) => setImmediate(resolve));
  }
  return {
    context,
    ids,
    calls,
    setChatEvents(events) {
      chatEvents = events;
    },
  };
}

function evaluate(context, code) {
  return vm.runInContext(code, context);
}

async function testHistoricalUserImagesRender() {
  const harness = await loadHarness();
  const item = {
    role: "user",
    text: "这张图是什么？",
    images: [
      { mime: "image/png", data: PNG_A },
      { mime: "image/jpeg", data: PNG_B },
    ],
    at: "2026-10-19T06:00:00Z",
  };
  evaluate(
    harness.context,
    `state.items = ${JSON.stringify([item])}; renderTranscript();`
  );
  const transcript = harness.ids.get("transcript");
  const images = transcript.querySelectorAll("img");
  assert.equal(images.length, 2, "both historical images must be rendered");
  assert.equal(images[0].src, "data:image/png;base64," + PNG_A);
  assert.equal(images[1].src, "data:image/jpeg;base64," + PNG_B);
  assert.equal(transcript.querySelectorAll(".bubble").length, 1);
  console.log("ok - historical user images render");
}

async function testNodeForHandlesAnImageOnlyMessage() {
  const harness = await loadHarness();
  const node = evaluate(
    harness.context,
    `nodeFor(${JSON.stringify({
      role: "user",
      text: "",
      images: [{ mime: "image/webp", data: PNG_A }],
      at: "",
    })})`
  );
  assert.equal(node.querySelectorAll(".bubble").length, 0);
  const images = node.querySelectorAll("img");
  assert.equal(images.length, 1);
  assert.equal(images[0].src, "data:image/webp;base64," + PNG_A);
  console.log("ok - nodeFor keeps an image-only message visible");
}

async function testSendFreezesTheAttachmentSnapshot() {
  const harness = await loadHarness();
  harness.setChatEvents([]);
  evaluate(
    harness.context,
    `
      pendingImages = [{ mime: "image/png", data: ${JSON.stringify(PNG_A)} }];
      state.sessionId = null;
      refresh = async () => ({});
      openSession = async () => {};
      newSession = async () => {
        state.sessionId = "s1";
        pendingImages.push({ mime: "image/png", data: ${JSON.stringify(PNG_B)} });
      };
    `
  );
  await evaluate(harness.context, "send('查看图片')");
  assert.equal(harness.calls.chatBodies.length, 1);
  const body = harness.calls.chatBodies[0];
  assert.equal(body.images.length, 1, "only attachments present at send time are sent");
  assert.equal(body.images[0].mime, "image/png");
  assert.equal(body.images[0].data, PNG_A);
  const user = evaluate(harness.context, "state.items.find((item) => item.role === 'user')");
  assert.equal(user.images[0].data, PNG_A, "the optimistic transcript needs display data");
  assert.equal(evaluate(harness.context, "pendingImages.length"), 1);
  assert.equal(evaluate(harness.context, "pendingImages[0].data"), PNG_B);
  console.log("ok - send snapshots image data before awaiting a new session");
}

async function testComposerWaitsUntilImageReadsFinish() {
  const harness = await loadHarness();
  harness.setChatEvents([]);
  evaluate(
    harness.context,
    `
      pendingImages = [];
      readingImages = 1;
      state.sessionId = "s1";
      $("input").value = "看图";
      __submitted = 0;
      __originalSend = send;
      send = async () => { __submitted += 1; };
      $("composer").requestSubmit = () => {};
    `
  );
  const handler = evaluate(harness.context, "$('composer').__submitHandler");
  assert.equal(typeof handler, "function", "the submit handler must be reachable");
  handler({ preventDefault() {}, target: harness.ids.get("composer") });
  assert.equal(evaluate(harness.context, "__submitted"), 0);
  assert.match(harness.ids.get("status").textContent, /读取/);
  console.log("ok - composer refuses to send while a picked image is still reading");
}

async function testPickedFileBecomesAnImageInTheRequest() {
  const harness = await loadHarness();
  harness.setChatEvents([]);
  evaluate(
    harness.context,
    `
      pendingImages = [];
      readingImages = 0;
      addImages([{ type: "image/png", size: 10, name: "a.png", data: ${JSON.stringify(PNG_A)} }]);
      __sent = 0;
      __originalSend = send;
      send = async () => { __sent += 1; };
    `
  );
  assert.equal(evaluate(harness.context, "pendingImages.length"), 1);
  assert.equal(evaluate(harness.context, "readingImages"), 0);
  const avatar = harness.ids.get("attachments").querySelectorAll("img");
  assert.equal(avatar.length, 1);
  assert.equal(avatar[0].src, "data:image/png;base64," + PNG_A);
  harness.ids.get("input").value = "发送选中的图片";
  const handler = harness.ids.get("composer").__submitHandler;
  handler({ preventDefault() {} });
  assert.equal(evaluate(harness.context, "__sent"), 1);
  console.log("ok - a successfully read file is visible and sendable");
}

async function runToolEventCase(resultArguments) {
  const harness = await loadHarness();
  const resultTool = { name: "bash", content: "ran", ok: true };
  if (resultArguments !== undefined) resultTool.arguments = resultArguments;
  harness.setChatEvents([
    { type: "tool_start", tool: { name: "bash", arguments: { command: "echo start" } } },
    { type: "tool_result", tool: resultTool },
    { type: "done", data: {} },
  ]);
  evaluate(
    harness.context,
    `
      state.sessionId = "s1";
      pendingImages = [];
      refresh = async () => ({});
      openSession = async () => {};
      __calls = [];
      __originalStepTool = stepTool;
      stepTool = (item) => { __calls.push(item); return __originalStepTool(item); };
    `
  );
  await evaluate(harness.context, "send('运行命令')");
  const calls = evaluate(harness.context, "__calls");
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].arguments)),
    resultArguments === undefined ? { command: "echo start" } : resultArguments);
}

async function testToolResultArgumentsReachTheStep() {
  await runToolEventCase(undefined);
  console.log("ok - tool_result without arguments keeps tool_start arguments");
  await runToolEventCase({ command: "echo result" });
  console.log("ok - tool_result arguments replace the earlier preview");
}

async function testChatComposerWithTasksLoaded() {
  const harness = await loadHarness({tasks: true});
  evaluate(harness.context, `
    __sent = [];
    send = async text => __sent.push(text);
    $("input").value = "普通聊天";
  `);
  await harness.ids.get("composer").__submitHandler({preventDefault() {}});
  assert.equal(evaluate(harness.context, "__sent[0]"), "普通聊天");
  evaluate(harness.context, `
    pendingImages = [{mime: "image/png", data: ${JSON.stringify(PNG_A)}}];
    $("input").value = "";
  `);
  await harness.ids.get("composer").__submitHandler({preventDefault() {}});
  assert.equal(evaluate(harness.context, "__sent.length"), 2, "image-only chat remains sendable");
  assert.equal(evaluate(harness.context, "__sent[1]"), "");
  console.log("ok - loading task controls preserves ordinary and image-only chat");
}

async function testTaskPollingSurvivesHiddenPageAndErrors() {
  const timers = new Map();
  let sequence = 0;
  const harness = await loadHarness({tasks: true,
    setTimeout: callback => { timers.set(++sequence, callback); return sequence; },
    clearTimeout: id => timers.delete(id),
  });
  evaluate(harness.context, `
    taskView.current = {id: "t1"};
    document.hidden = true;
    armTaskPolling();
  `);
  async function tick() {
    const [id, callback] = timers.entries().next().value;
    timers.delete(id);
    await callback();
  }
  await tick();
  assert.equal(timers.size, 1, "hidden page must keep a future refresh scheduled");
  evaluate(harness.context, `
    document.hidden = false;
    openTask = async () => { throw new Error("temporary failure"); };
  `);
  await tick();
  assert.equal(timers.size, 1, "temporary request failure must not stop polling");
  evaluate(harness.context, "taskView.current = null");
  await tick();
  assert.equal(timers.size, 0, "leaving a task must end polling");
  console.log("ok - task polling survives hidden tabs and transient request failures");
}

async function testStaleTaskResponseCannotReplaceSession() {
  const harness = await loadHarness({tasks: true});
  evaluate(harness.context, `
    __originalApi = api;
    api = path => path === "/api/tasks/t1"
      ? new Promise(resolve => { __resolveTask = resolve; }) : __originalApi(path);
    __openingTask = openTask("t1");
  `);
  await evaluate(harness.context, "openSession('s1')");
  evaluate(harness.context, "__resolveTask({id: 't1', state: 'pending'})");
  await evaluate(harness.context, "__openingTask");
  assert.equal(evaluate(harness.context, "state.sessionId"), "s1");
  assert.equal(evaluate(harness.context, "taskView.current"), null);
  console.log("ok - a stale task response cannot overwrite a selected chat");
}

async function testTaskRefreshPreservesUnsubmittedInputs() {
  const harness = await loadHarness({tasks: true, setTimeout: () => 1, clearTimeout: () => {}});
  evaluate(harness.context, `
    __task = {id: "t1", state: "waiting_user", messages: [], schedule: null,
      pending_interactions: [{id: "i1", kind: "question", payload: {questions: [{question: "Which?"}]}}]};
    api = async path => path === "/api/tasks" ? {tasks: []} : {...__task};
  `);
  await evaluate(harness.context, "openTask('t1')");
  const answer = harness.ids.get("task-interactions").querySelector("input");
  answer.value = "正在输入";
  harness.ids.get("task-due").value = "2030-01-01T12:30";
  harness.ids.get("task-timezone").value = "Asia/Tokyo";
  await evaluate(harness.context, "openTask('t1')");
  assert.equal(harness.ids.get("task-interactions").querySelector("input"), answer);
  assert.equal(answer.value, "正在输入");
  assert.equal(harness.ids.get("task-due").value, "2030-01-01T12:30");
  assert.equal(harness.ids.get("task-timezone").value, "Asia/Tokyo");
  console.log("ok - polling preserves unsubmitted task answers and schedule edits");
}

async function testTruncatedAnswerKeepsItsVisibleStatus() {
  const harness = await loadHarness();
  harness.setChatEvents([{type: "delta", text: "partial"}, {type: "done", data: {stopped: "length"}}]);
  evaluate(harness.context, `
    state.sessionId = "s1";
    refresh = async () => ({});
    openSession = async () => {};
  `);
  await evaluate(harness.context, "send('answer fully')");
  assert.match(harness.ids.get("status").textContent, /未完整结束.*length/);
  console.log("ok - truncated model output keeps a visible incomplete status");
}

async function main() {
  await testHistoricalUserImagesRender();
  await testNodeForHandlesAnImageOnlyMessage();
  await testSendFreezesTheAttachmentSnapshot();
  await testComposerWaitsUntilImageReadsFinish();
  await testPickedFileBecomesAnImageInTheRequest();
  await testToolResultArgumentsReachTheStep();
  await testChatComposerWithTasksLoaded();
  await testTaskPollingSurvivesHiddenPageAndErrors();
  await testStaleTaskResponseCannotReplaceSession();
  await testTaskRefreshPreservesUnsubmittedInputs();
  await testTruncatedAnswerKeepsItsVisibleStatus();
  console.log("frontend behaviour: all checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
