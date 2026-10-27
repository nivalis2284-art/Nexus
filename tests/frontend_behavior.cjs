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

  get childNodes() { return this.children; }

  get textContent() { return (this._text || "") + this.children.map(node => node.textContent).join(""); }
  set textContent(value) { this._text = String(value || ""); if (this.children) this.children = []; }

  insertBefore(child, before) {
    if (child.parent) child.remove();
    child.parent = this;
    const index = before ? this.children.indexOf(before) : this.children.length;
    this.children.splice(index, 0, child); return child;
  }

  replaceChildren(...children) { this.children = []; this._text = ""; for (const child of children) this.appendChild(child); }

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
    (this.handlers ||= {})[name] = handler;
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
    window: { innerWidth: 1200, addEventListener() {} },
    fetch,
    TextDecoder,
    TextEncoder,
    console,
    renderContent(text) { const n = new FakeNode("div"); n.textContent = text; return n; },
    toolNode(item) {
      const n=new FakeNode("details"); n.className="step";
      const summary=new FakeNode("summary"); summary.textContent=(item.name || "tool") + (item.live ? " 运行中" : " 完成"); n.appendChild(summary);
      const body=new FakeNode("div"); body.textContent=item.text || ""; n.appendChild(body); return n;
    },
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
    vm.runInContext(fs.readFileSync(path.join(ROOT, "nexus", "web", "ling.js"), "utf8"), context);
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

async function testPickedFileBecomesAnImageAttachment() {
  const harness = await loadHarness();
  evaluate(harness.context, `
    pendingImages = [];
    readingImages = 0;
    addImages([{ type: "image/png", size: 10, name: "a.png", data: ${JSON.stringify(PNG_A)} }]);
  `);
  assert.equal(evaluate(harness.context, "pendingImages.length"), 1);
  assert.equal(evaluate(harness.context, "readingImages"), 0);
  const avatar = harness.ids.get("attachments").querySelectorAll("img");
  assert.equal(avatar.length, 1);
  assert.equal(avatar[0].src, "data:image/png;base64," + PNG_A);
  console.log("ok - a successfully read file is visible as an attachment");
}

async function testToolRecordsStayVisibleInTheTranscript() {
  const harness = await loadHarness();
  evaluate(harness.context, `
    state.items = [
      {role: "user", text: "运行命令", at: "2026-10-20T01:00:00Z"},
      {role: "tool", name: "bash", text: "ran", ok: true, arguments: {command: "echo hi"}},
      {role: "assistant", text: "完成", at: "2026-10-20T01:01:00Z"},
    ];
    renderTranscript();
  `);
  const text = harness.ids.get("transcript").descendants().map((node) => node.textContent).join(" ");
  assert.match(text, /bash/);
  assert.match(text, /完成/);
  console.log("ok - tool records remain available in the transcript");
}

async function testReasoningAndProseBothRemainInspectable() {
  const harness = await loadHarness();
  evaluate(harness.context, `
    state.items = [
      {role: "user", text: "解释一下", at: "2026-10-20T01:00:00Z"},
      {role: "assistant", thinking: ["先检查已有代码。", "再决定改动范围。"].join(String.fromCharCode(10)),
        text: "这里是最终回答。", elapsed: 3.5},
    ];
    renderTranscript();
  `);
  const transcript = harness.ids.get("transcript");
  assert.equal(transcript.querySelectorAll(".phase").length, 1,
    "reasoning is folded under one phase");
  assert.equal(transcript.querySelectorAll(".step").length, 1,
    "reasoning is inspectable as a step");
  assert.match(transcript.querySelector(".step").descendants().map((node) => node.textContent).join(" "),
    /先检查已有代码/,
    "a settled thinking step shows its first line");
  assert.equal(transcript.querySelectorAll(".answer")[0].textContent, "这里是最终回答。",
    "reasoning plus prose still renders the answer");
  assert.match(transcript.descendants().map((node) => node.textContent).join(" "), /本轮用时 3.5 秒/);
  console.log("ok - reasoning, prose, and elapsed remain available in one fold");
}

async function testLiveThoughtIsVisibleBeforeProse() {
  const harness = await loadHarness();
  evaluate(harness.context, `
    state.items = [
      {role: "user", text: "继续", at: "2026-10-20T01:00:00Z"},
      {role: "assistant", thinking: "正在核对实际运行结果。", text: "", live: true},
    ];
    renderTranscript();
  `);
  const transcript = harness.ids.get("transcript");
  const phase = transcript.querySelector(".phase");
  assert.equal(phase.classList.contains("live"), true, "live thinking opens its phase");
  assert.match(phase.descendants().map((node) => node.textContent).join(" "), /正在核对实际运行结果/);
  console.log("ok - thinking-only live items show the current thought before prose arrives");
}
async function testTaskSavedThinkingAppears() {
  const h = await loadHarness({tasks:true});
  evaluate(h.context, `lingView.active=true;
    taskView.current={id:"t1",workspace:"C:/work"};
    renderTaskMessages([{seq:1,kind:"assistant",text:"",thinking:"SAVED_BACKGROUND_THOUGHT"}],null);`);
  assert.match(h.ids.get("task-messages").textContent,/SAVED_BACKGROUND_THOUGHT/);
  console.log("ok - saved background thinking remains inspectable");
}

async function testTaskDetailHasNoSecondComposer() {
  const harness = await loadHarness({tasks: true, setTimeout: () => 1, clearTimeout: () => {}});
  evaluate(harness.context, `
    lingView.active = false;
    openLing = async () => { lingView.active = false; };
    __task = {id: "t1", title: "一项工作", state: "waiting_user",
      control_version: 2, messages: [{kind: "user", text: "原始目标"}],
      pending_interactions: [{id: "i1", kind: "question", payload: {questions: [{question: "Which?"}]}}]};
    api = async path => path === "/api/tasks" ? {tasks: []} : {...__task};
  `);
  await evaluate(harness.context, "openTask('t1')");
  const records = harness.ids.get("task-messages");
  assert.ok(records.children.length, "the read-only work record is rendered");
  assert.equal(harness.ids.get("task-interactions").querySelector("input"), null,
    "work details have no second composer");
  assert.equal(harness.ids.get("task-start"), undefined,
    "work details have no manual lifecycle control");
  console.log("ok - work detail is read-only and keeps its record stable");
}
async function testGenericFileUploadsPreserveOriginalData() {
  const harness = await loadHarness();
  evaluate(harness.context, `
    __uploads = 0;
    __uploadedBody = null;
    __baseFetch = fetch;
    fetch = async (path, options) => {
      if (path.includes("/attachments?")) {
        __uploads += 1;
        __uploadedPath = path;
        __uploadedBody = options.body;
        return {ok: true, json: async () => ({id: "a_test", name: "report.pdf", size: 10,
          status: "ready", note: "可读取文本", url: "/download"})};
      }
      return __baseFetch(path, options);
    };
  `);
  const result = await evaluate(harness.context,
    `uploadFiles("sessions", "s1", [{name: "report.pdf", type: "application/pdf", size: 10, file: {}}])`);
  assert.equal(evaluate(harness.context, "__uploads"), 1);
  assert.match(evaluate(harness.context, "__uploadedPath"), /name=report.pdf/);
  assert.equal(result[0].id, "a_test");
  console.log("ok - generic files upload their original bytes and return an attachment reference");
}

async function testFailedUploadReturnsAnError() {
  const harness = await loadHarness();
  evaluate(harness.context, `fetch = async () => ({ok: false, json: async () => ({error: "upload failed"})});`);
  await assert.rejects(() => evaluate(harness.context,
    `uploadFiles("sessions", "s1", [{name: "notes.md", type: "text/plain", size: 10, file: {}}])`),
    /upload failed/);
  console.log("ok - a failed upload stays an explicit error");
}
async function testOrderedProcessKeepsExpandedNodes() {
  const h = await loadHarness();
  evaluate(h.context, `state.items = [
    {role:"user",text:"work"}, {role:"assistant",message_id:"m1",thinking:"FIRST"},
    {role:"tool",parent_message_id:"m1",tool_call_id:"c0",name:"read_file",text:"read"},
    {role:"assistant",message_id:"m2",thinking:"SECOND",live:true},
    {role:"tool",parent_message_id:"m2",tool_call_id:"c0",name:"bash",text:"done"}]; renderTranscript();`);
  const phase=h.ids.get("transcript").querySelector(".phase");
  const steps=phase.querySelectorAll(".step");
  assert.match(steps[0].textContent,/FIRST/); assert.match(steps[1].textContent,/read_file/);
  assert.match(steps[2].textContent,/SECOND/); assert.match(steps[3].textContent,/bash/);
  phase.open=true; steps[0].open=true;
  evaluate(h.context, `state.items[3].thinking += " DELTA"; renderTranscript({preserveScroll:true});`);
  assert.equal(h.ids.get("transcript").querySelector(".phase"),phase);
  assert.equal(phase.querySelectorAll(".step").length,4,"reused provider tool IDs do not duplicate rows");
  assert.equal(phase.querySelectorAll(".step")[0],steps[0]);
  assert.equal(steps[0].open,true); assert.equal(phase.open,true);
  console.log("ok - ordered process updates keep expanded nodes");
}

async function testComposerDoesNotSendImeComposition() {
  const h=await loadHarness(); let sent=0;
  h.ids.get("composer").requestSubmit=()=>sent++;
  const keydown=h.ids.get("input").handlers.keydown;
  keydown({key:"Enter",isComposing:true,preventDefault(){}});
  assert.equal(sent,0);
  keydown({key:"Enter",isComposing:false,preventDefault(){}});
  assert.equal(sent,1);
  console.log("ok - IME Enter commits text without sending");
}

async function testCurrentContextIsSeparateFromUsage() {
  const h=await loadHarness();
  evaluate(h.context, `state.contextWindow=1000; applyUsageSnapshot({
    usage:{input:20,cached:80,created:0,output:5000,total_input:100,total:5100},
    context:{system:100,tools:50,messages:100,window:1000}});`);
  assert.equal(h.ids.get("stat-context").textContent,"上下文 ~25%");
  assert.equal(h.ids.get("stat-tokens").textContent,"5,100 tok");
  assert.match(h.ids.get("usage-panel").innerHTML,/80%/);
  console.log("ok - current context and cached usage use different denominators");
}

async function main() {
  await testOrderedProcessKeepsExpandedNodes();
  await testComposerDoesNotSendImeComposition();
  await testCurrentContextIsSeparateFromUsage();
  await testHistoricalUserImagesRender();
  await testNodeForHandlesAnImageOnlyMessage();
  await testPickedFileBecomesAnImageAttachment();
  await testToolRecordsStayVisibleInTheTranscript();
  await testReasoningAndProseBothRemainInspectable();
  await testLiveThoughtIsVisibleBeforeProse();
  await testTaskSavedThinkingAppears();
  await testTaskDetailHasNoSecondComposer();
  await testGenericFileUploadsPreserveOriginalData();
  await testFailedUploadReturnsAnError();
  console.log("frontend behaviour: all checks passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
