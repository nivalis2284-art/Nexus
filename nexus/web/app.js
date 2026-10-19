"use strict";

// One page, no framework. State is one object; the transcript rebuilds from it.

const $ = (id) => document.getElementById(id);

const state = {
  providers: [],
  current: { provider: "", model: "", effort: null },
  effort: { levels: [], labels: {}, current: null, mapping: null },
  sessions: [],
  sessionId: null,
  items: [],
  running: false,
  turns: 0,
  tokens: 0,
  contextLimit: null,
  editingProvider: null,
  // Context composition from the last turn, and cumulative token usage.
  context: null,
  usage: { input: 0, output: 0, cached: 0, created: 0 },
};

async function api(path, options) {
  const response = await fetch(path, options || {});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

const post = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

const put = (path, body) =>
  api(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setStatus(text, isError) {
  const node = $("status");
  node.textContent = text || "";
  node.className = "small" + (isError ? " error" : "");
  node.style.color = isError ? "var(--danger)" : "";
}

function clock(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return String(date.getHours()).padStart(2, "0") + ":" + String(date.getMinutes()).padStart(2, "0");
}

// ---------------------------------------------------------------- state

async function refresh() {
  const value = await api("/api/state");
  Object.assign(state, {
    providers: value.providers || [],
    current: value.current || state.current,
    effort: value.effort || state.effort,
    sessions: value.sessions || [],
    permission: value.permission,
    workspacePath: value.workspace,
  });
  state.contextLimit = (value.profile && value.profile.context_window_tokens) || null;
  $("workspace").textContent = shortPath(value.workspace || "");
  $("workspace").title = value.workspace || "";
  renderSessions();
  renderModelLabel();
  renderEffortLabel();
  return value;
}

function shortPath(path) {
  const parts = String(path).split(/[\\/]/).filter(Boolean);
  return parts.length > 2 ? "…/" + parts.slice(-2).join("/") : path;
}

function currentProvider() {
  return state.providers.find((p) => p.id === state.current.provider) || state.providers[0];
}

function currentModel() {
  const provider = currentProvider();
  if (!provider) return null;
  return (provider.models || []).find((m) => m.id === state.current.model) || provider.models[0];
}

function renderModelLabel() {
  const provider = currentProvider();
  const model = currentModel();
  $("model-label").textContent = model ? model.id : "选择模型";
  $("model-label").title = provider ? provider.name + " · " + provider.family : "";
}

function renderEffortLabel() {
  const labels = state.effort.labels || {};
  const level = state.effort.current;
  $("effort-label").textContent = level ? "思考 · " + (labels[level] || level) : "思考 · 默认";
  const mapping = state.effort.mapping;
  $("effort-label").title = mapping
    ? mapping.field + " = " + mapping.value
    : "不发送思考字段，使用提供商默认值";
}

function renderSessions() {
  const list = $("sessions");
  list.innerHTML = "";
  for (const session of state.sessions) {
    const item = el("li");
    if (session.id === state.sessionId) item.classList.add("active");
    const label = el("span", "label", session.title || "新会话");
    label.title = session.title || "";
    label.title = "双击重命名";
    label.addEventListener("dblclick", (event) => {
      event.stopPropagation();
      renameSession(session.id, label, item);
    });
    item.appendChild(label);
    const remove = el("button", "del", "×");
    remove.type = "button";
    remove.title = "删除会话";
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!confirm("删除这个会话？")) return;
      await api("/api/sessions/" + encodeURIComponent(session.id), { method: "DELETE" });
      if (state.sessionId === session.id) {
        state.sessionId = null;
        state.items = [];
        $("title").textContent = "新会话";
        renderTranscript();
      }
      await refresh();
    });
    item.appendChild(remove);
    item.addEventListener("click", () => openSession(session.id));
    list.appendChild(item);
  }
}

// ---------------------------------------------------------------- rename

async function renameSession(sessionId, label, item) {
  const input = el("input", "rename-input");
  input.type = "text";
  input.value = label.textContent;
  input.maxLength = 80;
  label.replaceWith(input);
  input.focus();
  input.select();

  let settled = false;
  const finish = async (save) => {
    if (settled) return;
    settled = true;
    const value = input.value.trim();
    if (!save || !value) {
      input.replaceWith(label);
      return;
    }
    try {
      await post(
        "/api/sessions/" + encodeURIComponent(sessionId) + "/rename",
        { title: value }
      );
      label.textContent = value;
      input.replaceWith(label);
      await refresh();
    } catch (error) {
      setStatus(error.message, true);
      input.replaceWith(label);
    }
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      finish(true);
    } else if (event.key === "Escape") {
      event.preventDefault();
      finish(false);
    }
  });
  input.addEventListener("blur", () => finish(true));
}

// ---------------------------------------------------------------- session

async function newSession() {
  const created = await post("/api/sessions", {});
  await refresh();
  await openSession(created.id);
}

async function openSession(id) {
  const viewVersion = state.viewVersion = (state.viewVersion || 0) + 1;
  if (typeof taskView !== "undefined") {
    taskView.current = null;
    if (taskView.timer) clearTimeout(taskView.timer);
    taskView.timer = null;
  }
  const value = await api("/api/sessions/" + encodeURIComponent(id));
  if (viewVersion !== state.viewVersion) return;
  state.sessionId = value.id;
  state.items = value.items || [];
  state.turns = state.items.filter((i) => i.role === "user").length;
  state.tokens = state.items.reduce((sum, item) => {
    const usage = item.usage || {};
    return sum + (usage.input_tokens || 0) + (usage.output_tokens || 0);
  }, 0);
  state.usage = state.items.reduce(
    (acc, item) => {
      const usage = item.usage || {};
      acc.input += usage.input_tokens || 0;
      acc.output += usage.output_tokens || 0;
      acc.cached += usage.cached_input_tokens || 0;
      acc.created += usage.cache_creation_input_tokens || 0;
      return acc;
    },
    { input: 0, output: 0, cached: 0, created: 0 }
  );
  $("title").textContent = value.title || "新会话";
  if (typeof taskView !== "undefined") {
    taskView.current = null;
    $("task-panel").hidden = true;
    renderTasks();
  }
  renderSessions();
  renderTranscript();
  $("input").focus();
}

function renderTranscript() {
  const box = $("transcript");
  box.innerHTML = "";
  box.classList.toggle("empty", state.items.length === 0);
  $("main").classList.toggle("empty", state.items.length === 0);
  if (!state.items.length) {
    const hello = el("div", "welcome");
    hello.appendChild(el("div", "welcome-title", "想做点什么？"));
    hello.appendChild(
      el("div", "welcome-sub", "描述一个任务，我会读文件、改代码、跑命令。")
    );
    box.appendChild(hello);
  }
  // A turn's reasoning and tool calls belong to one collapsible phase, so the
  // transcript reads as: your message, the phase, the answer.
  let batch = null;
  for (const item of state.items) {
    if (item.role === "user") {
      if (batch) { box.appendChild(phaseNode(batch)); batch = null; }
      box.appendChild(nodeFor(item));
      continue;
    }
    if (item.role === "tool" || (item.role === "assistant" && item.thinking)) {
      if (!batch) batch = { tools: [], thinking: [], elapsed: null, live: false };
      // A message can carry reasoning *and* prose (the final answer often
      // does). Keep the reasoning in the fold and let the prose fall through.
      const hasBody = Boolean((item.text || "").trim());
      if (typeof item.elapsed === "number") batch.elapsed = item.elapsed;
      if (item.role === "tool") {
        batch.tools.push(item);
        continue;
      }
      batch.thinking.push(item.thinking);
      if (!hasBody) continue;
    }
    if (item.role === "assistant") {
      if (batch) {
        // The turn's elapsed time sits on its final answer, which closes the
        // batch; adopt it before the fold is emitted.
        if (typeof item.elapsed === "number") batch.elapsed = item.elapsed;
        box.appendChild(phaseNode(batch));
        batch = null;
      }
      box.appendChild(nodeFor(item));
    } else {
      box.appendChild(nodeFor(item));
    }
  }
  if (batch) box.appendChild(phaseNode(batch));
  updateStats();
  scrollDown();
}

function formatElapsed(seconds) {
  if (typeof seconds !== "number" || seconds <= 0) return "";
  if (seconds < 60) return seconds < 10 ? seconds.toFixed(1) + " 秒" : Math.round(seconds) + " 秒";
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return minutes + " 分 " + rest + " 秒";
}

/** The outer fold: everything the agent did before it answered. */
function phaseNode(batch) {
  const details = el("details", "phase" + (batch.live ? " live" : ""));
  details.open = Boolean(batch.live);
  const summary = el("summary");
  summary.appendChild(el("span", "tw", "▸"));
  const state_ = batch.live ? "正在思考" : "思考完成";
  const label = el("span", "plabel", state_);
  summary.appendChild(label);
  const stamp = formatElapsed(batch.elapsed);
  if (stamp) summary.appendChild(el("span", "pnote", "（已思考 " + stamp + "）"));
  const meta = [];
  if (batch.thinking.length) meta.push(batch.thinking.length + " 段思考");
  if (batch.tools.length) meta.push(batch.tools.length + " 次工具");
  if (meta.length && !stamp) summary.appendChild(el("span", "pnote", "（" + meta.join(" · ") + "）"));
  details.appendChild(summary);

  const body = el("div", "pbody");
  for (const text of batch.thinking) body.appendChild(stepThinking(text));
  for (const tool of batch.tools) body.appendChild(stepTool(tool));
  details.appendChild(body);
  return details;
}

/** A step inside the phase. Collapsed by default; shows one line when closed. */
function stepThinking(text) {
  const details = el("details", "step");
  const summary = el("summary");
  summary.appendChild(el("span", "sicon", "◇"));
  const first = String(text).split("\n").find((line) => line.trim()) || "";
  summary.appendChild(el("span", "stitle", truncate(first.trim(), 120)));
  summary.appendChild(el("span", "scount", text.length + " 字"));
  details.appendChild(summary);
  details.appendChild(el("div", "sbody", text));
  return details;
}

function stepTool(item) {
  const details = el("details", "step");
  const summary = el("summary");
  summary.appendChild(el("span", "sicon", item.ok === false ? "✕" : "✓"));
  summary.appendChild(el("span", "sname", item.name || "tool"));
  const args = briefArgs(item.arguments);
  if (args) summary.appendChild(el("span", "sargs", args));
  summary.appendChild(
    el("span", "sstate " + (item.ok === false ? "bad" : "good"), item.ok === false ? "失败" : "完成")
  );
  details.appendChild(summary);
  const body = el("div", "sbody mono");
  if (item.arguments) body.appendChild(el("div", "sargs-full", JSON.stringify(item.arguments, null, 2)));
  body.appendChild(el("div", "", item.text || ""));
  details.appendChild(body);
  return details;
}

function truncate(text, size) {
  return text.length > size ? text.slice(0, size) + "…" : text;
}

function nodeFor(item) {
  if (item.role === "tool") return toolNode(item);
  if (item.role === "notice") {
    const wrap = el("div", "msg");
    wrap.appendChild(el("div", "small error", item.text || ""));
    return wrap;
  }
  if (item.role === "user") {
    const wrap = el("div", "msg user");
    if (item.text) wrap.appendChild(el("div", "bubble", item.text));
    const images = Array.isArray(item.images) ? item.images : [];
    if (images.length) {
      const gallery = el("div", "msg-images");
      images.forEach((image, index) => {
        if (!image || typeof image.mime !== "string" || typeof image.data !== "string") return;
        const picture = el("img");
        picture.src = "data:" + image.mime + ";base64," + image.data;
        picture.alt = "用户发送的图片 " + (index + 1);
        picture.loading = "lazy";
        gallery.appendChild(picture);
      });
      if (gallery.children.length) wrap.appendChild(gallery);
    }
    wrap.appendChild(el("div", "stamp", clock(item.at)));
    return wrap;
  }
  const wrap = el("div", "msg assistant");
  wrap.appendChild(el("div", "answer", item.text || ""));
  const stamp = el("div", "msg-meta");
  if (typeof item.elapsed === "number" && item.elapsed > 0) {
    stamp.textContent = "用时 " + formatElapsed(item.elapsed);
  }
  if (stamp.textContent) wrap.appendChild(stamp);
  return wrap;
}

function thinkingNode(text, live) {
  const details = el("details", "thinking" + (live ? " live" : ""));
  // Live reasoning opens itself: the point of streaming it is to be seen. A
  // settled turn stays collapsed so the answer is what the reader lands on.
  if (live) details.open = true;
  const summary = el("summary");
  summary.appendChild(el("span", "tw", "▸"));
  summary.appendChild(el("span", "tlabel", "思考"));
  summary.appendChild(el("span", "tnote", live ? "进行中" : text.length + " 字"));
  details.appendChild(summary);
  details.appendChild(el("div", "tbody", text || ""));
  return details;
}

function toolNode(item) {
  const details = el("details", "tool");
  const summary = el("summary");
  summary.appendChild(el("span", "ticon", item.ok === false ? "✕" : "✓"));
  summary.appendChild(el("span", "tname", item.name || "tool"));
  const args = briefArgs(item.arguments);
  if (args) summary.appendChild(el("span", "small", args));
  const mark = el("span", "tstate " + (item.ok === false ? "bad" : "good"), item.ok === false ? "失败" : "");
  summary.appendChild(mark);
  details.appendChild(summary);
  const body = el("div", "tbody");
  if (item.arguments) body.appendChild(el("div", "args", JSON.stringify(item.arguments, null, 2)));
  body.appendChild(el("div", "", item.text || ""));
  details.appendChild(body);
  return details;
}

function briefArgs(args) {
  if (!args) return "";
  if (typeof args === "string") return args.length > 60 ? args.slice(0, 60) + "…" : args;
  const value = args.path || args.command || args.pattern || args.name;
  return value ? (String(value).length > 60 ? String(value).slice(0, 60) + "…" : String(value)) : "";
}

function scrollDown() {
  const box = $("scroller");
  box.scrollTop = box.scrollHeight;
}

function updateStats() {
  $("stat-turn").textContent = state.turns + " 轮";
  $("stat-tokens").textContent = state.tokens ? state.tokens.toLocaleString() + " tok" : "0 tok";
  const limit = state.contextLimit;
  $("stat-context").textContent = limit
    ? "上下文 " + Math.min(100, Math.round((state.tokens / limit) * 100)) + "%"
    : "上下文 —";
}

// ---------------------------------------------------------------- send

// During a turn the DOM is built incrementally: one phase fold holds the
// thinking and tool steps, and the answer grows below it. The final render
// comes from the server transcript, so this only has to be good enough to
// watch.
async function send(text) {
  if (state.running) return;
  // Freeze the attachments for this turn. A new session request may yield, and
  // the composer's array must not decide later what was actually sent.
  const sentItems = pendingImages.slice();
  const images = sentItems.map((image) => ({ mime: image.mime, data: image.data }));
  if (!state.sessionId) await newSession();
  if (!state.sessionId) return;

  state.running = true;
  state.turns += 1;
  setRunning(true);
  setStatus("运行中…");

  state.items.push({
    role: "user",
    text,
    images,
    at: new Date().toISOString(),
  });
  pendingImages = pendingImages.filter((image) => !sentItems.includes(image));
  renderAttachments();
  const box = $("transcript");
  box.innerHTML = "";
  box.classList.remove("empty");
  $("main").classList.remove("empty");
  for (const item of state.items) box.appendChild(nodeFor(item));

  const phase = el("details", "phase live");
  phase.open = true;
  const summary = el("summary");
  summary.appendChild(el("span", "tw", "▸"));
  const plabel = el("span", "plabel", "正在思考");
  summary.appendChild(plabel);
  const pnote = el("span", "pnote", "（计时中）");
  summary.appendChild(pnote);
  phase.appendChild(summary);
  const pbody = el("div", "pbody");
  phase.appendChild(pbody);
  box.appendChild(phase);

  const wrap = el("div", "msg assistant");
  const answer = el("div", "answer streaming-caret");
  wrap.appendChild(answer);
  box.appendChild(wrap);
  scrollDown();

  let thinkingText = "";
  let stepEl = null;
  let stepBody = null;
  let started = Date.now();
  let active = false;

  const openThinkingStep = () => {
    const step = el("details", "step");
    step.open = true;
    const head = el("summary");
    head.appendChild(el("span", "sicon", "◇"));
    const title = el("span", "stitle", "思考中…");
    head.appendChild(title);
    const count = el("span", "scount", "");
    head.appendChild(count);
    step.appendChild(head);
    const body = el("div", "sbody");
    step.appendChild(body);
    pbody.appendChild(step);
    return { step, body, title, count };
  };

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, text, images }),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || response.statusText);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const event = JSON.parse(line);
        if (event.type === "thinking") {
          active = true;
          if (!stepEl) {
            const built = openThinkingStep();
            stepEl = built.step;
            stepBody = built.body;
            stepEl._title = built.title;
            stepEl._count = built.count;
          }
          thinkingText += event.text;
          stepBody.textContent = thinkingText;
          stepEl._count.textContent = thinkingText.length + " 字";
          const first = thinkingText.split("\n").find((l) => l.trim()) || "";
          stepEl._title.textContent = truncate(first.trim(), 120) || "思考中…";
          scrollDown();
        } else if (event.type === "delta") {
          answer.textContent += event.text;
          scrollDown();
        } else if (event.type === "tool_start") {
          answer.classList.remove("streaming-caret");
          if (stepEl) stepEl.open = false;
          stepEl = null;
          stepBody = null;
          thinkingText = "";
          const step = stepTool({
            name: event.tool.name,
            arguments: event.tool.arguments,
            text: "运行中…",
          });
          step.dataset.live = "1";
          step._toolArguments = event.tool.arguments;
          pbody.appendChild(step);
          scrollDown();
        } else if (event.type === "tool_result") {
          const steps = pbody.querySelectorAll('.step[data-live="1"]');
          const last = steps[steps.length - 1];
          if (last) {
            const argumentsValue =
              event.tool.arguments === undefined ? last._toolArguments : event.tool.arguments;
            last.replaceWith(
              stepTool({
                name: event.tool.name,
                arguments: argumentsValue,
                text: event.tool.content,
                ok: event.tool.ok,
              })
            );
          }
          scrollDown();
        } else if (event.type === "context") {
          state.context = event.data;
          renderUsage();
        } else if (event.type === "retrying") {
          const info = event.data || {};
          setStatus(
            "重试中 " + info.attempt + "/" + info.max + "（" + info.code + "）"
          );
        } else if (event.type === "question") {
          askQuestion(event).then((text) =>
            post("/api/answer", { token: event.token, text }).catch(() => {})
          );
        } else if (event.type === "approval") {
          askApproval(event).then((allowed) =>
            post("/api/approval", { token: event.token, allowed }).catch(() => {})
          );
        } else if (event.type === "error") {
          answer.textContent += "\n[错误] " + event.message;
          setStatus(event.message, true);
        } else if (event.type === "done") {
          answer.classList.remove("streaming-caret");
          if (stepEl) stepEl.open = false;
          phase.classList.remove("live");
          const elapsed =
            (event.data && typeof event.data.elapsed === "number" && event.data.elapsed) ||
            (Date.now() - started) / 1000;
          plabel.textContent = "思考完成";
          pnote.textContent = "（已思考 " + formatElapsed(elapsed) + "）";
          // Nothing happened before the answer: the phase is noise, drop it.
          if (!pbody.children.length) phase.remove();
          else phase.open = false;
          const usage = (event.data && event.data.usage) || {};
          const total = (usage.input_tokens || 0) + (usage.output_tokens || 0);
          if (total) {
            state.tokens += total;
            state.usage.input += usage.input_tokens || 0;
            state.usage.output += usage.output_tokens || 0;
            state.usage.cached += usage.cached_input_tokens || 0;
            state.usage.created += usage.cache_creation_input_tokens || 0;
            if (state.context) state.context.source = "actual";
            updateStats();
            renderUsage();
          }
          const stopped = event.data && event.data.stopped;
          if (stopped && !["complete", "cancelled"].includes(stopped)) {
            setStatus("模型输出未完整结束（" + stopped + "）", true);
          } else {
            setStatus("");
          }
        }
      }
    }
  } catch (error) {
    answer.classList.remove("streaming-caret");
    answer.textContent += "\n[错误] " + error.message;
    setStatus(error.message, true);
  } finally {
    state.running = false;
    setRunning(false);
    answer.classList.remove("streaming-caret");
    updateStats();
    await refresh().catch(() => {});
    if (state.sessionId) await openSession(state.sessionId).catch(() => {});
  }
}

// ---------------------------------------------------------------- attachments

// Images waiting to be sent. Kept outside `state` because they are transient
// per composition, not session data.
let pendingImages = [];
// Files still being read. Sending before these settle would silently omit the
// picture the user just attached, so the composer waits for read completion.
let readingImages = 0;

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_IMAGES = 8;

function renderAttachments() {
  const strip = $("attachments");
  strip.innerHTML = "";
  strip.hidden = pendingImages.length === 0;
  pendingImages.forEach((image, index) => {
    const chip = el("div", "attach-chip");
    const thumb = el("img");
    thumb.src = "data:" + image.mime + ";base64," + image.data;
    thumb.alt = image.name || "图片";
    chip.appendChild(thumb);
    const remove = el("button", "attach-x", "×");
    remove.type = "button";
    remove.title = "移除";
    remove.addEventListener("click", () => {
      pendingImages.splice(index, 1);
      renderAttachments();
    });
    chip.appendChild(remove);
    strip.appendChild(chip);
  });
}

function addImages(files) {
  const incoming = Array.from(files || []).filter((file) =>
    file.type.startsWith("image/")
  );
  for (const file of incoming) {
    if (pendingImages.length >= MAX_IMAGES) {
      setStatus("最多 " + MAX_IMAGES + " 张图片", true);
      break;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      setStatus("图片超过 5MB：" + file.name, true);
      continue;
    }
    readingImages += 1;
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      const comma = result.indexOf(",");
      pendingImages.push({
        mime: file.type,
        data: comma >= 0 ? result.slice(comma + 1) : result,
        name: file.name,
      });
      finishImageRead();
    };
    reader.onerror = () => {
      setStatus("读取图片失败：" + file.name, true);
      finishImageRead();
    };
    reader.onabort = finishImageRead;
    reader.readAsDataURL(file);
  }
}

function finishImageRead() {
  readingImages = Math.max(0, readingImages - 1);
  renderAttachments();
}

// ---------------------------------------------------------------- usage

/** Format a token count the way the panels show it: approximate, compact. */
function approx(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (value >= 1_000_000) return "~" + (value / 1_000_000).toFixed(2) + "M";
  if (value >= 1_000) return "~" + (value / 1_000).toFixed(1) + "K";
  return "~" + value;
}

/** Cache hit rate, or an em dash when there is nothing to divide by. */
function cacheRate(usage) {
  const denominator = usage.cached + usage.input;
  if (!denominator) return "—";
  return Math.round((usage.cached / denominator) * 100) + "%";
}

function renderUsage() {
  const panel = $("usage-panel");
  if (panel.hidden) return;

  const parts = [];
  const ctx = state.context;
  if (ctx) {
    const used = (ctx.system || 0) + (ctx.tools || 0) + (ctx.messages || 0);
    const window_ = ctx.window || 0;
    const percent = window_ ? Math.min(100, Math.round((used / window_) * 100)) : 0;
    parts.push(
      '<div class="usage-head">' +
        '<span>上下文已用 <b>' + percent + "%</b></span>" +
        '<span class="usage-total">' + approx(used) + " / " + approx(window_) + "</span>" +
        "</div>"
    );
    parts.push(
      '<div class="usage-bar"><span style="width:' + percent + '%"></span></div>'
    );
    parts.push(
      '<ul class="usage-list">' +
        '<li><i class="dot d-system"></i>系统提示词<em>' + approx(ctx.system) + "</em></li>" +
        '<li><i class="dot d-tools"></i>工具定义<em>' + approx(ctx.tools) + "</em></li>" +
        '<li><i class="dot d-messages"></i>对话消息<em>' + approx(ctx.messages) + "</em></li>" +
        "</ul>"
    );
  }

  const u = state.usage;
  const grand = u.cached + u.created + u.input + u.output;
  parts.push(
    '<div class="usage-head usage-q">' +
      "<span>Token 用量</span>" +
      '<span class="usage-total"><b>' + (grand ? grand.toLocaleString() : 0) + "</b> tok</span>" +
      "</div>"
  );
  parts.push(
    '<ul class="usage-list">' +
      "<li>缓存命中<em>" + cacheRate(u) + "</em></li>" +
      "<li>未缓存输入<em>" + (u.input ? u.input.toLocaleString() : 0) + " tok</em></li>" +
      "<li>缓存读取<em>" + (u.cached ? u.cached.toLocaleString() : 0) + " tok</em></li>" +
      "<li>输出<em>" + (u.output ? u.output.toLocaleString() : 0) + " tok</em></li>" +
      "</ul>"
  );
  parts.push('<p class="usage-note">~ 表示估算，不是计费口径。</p>');
  panel.innerHTML = parts.join("");
}

function toggleUsage() {
  const panel = $("usage-panel");
  panel.hidden = !panel.hidden;
  renderUsage();
}

// ---------------------------------------------------------------- run control

function setRunning(isRunning) {
  const button = $("send");
  button.disabled = false;
  button.classList.toggle("stop", isRunning);
  button.textContent = isRunning ? "■" : "↑";
  button.title = isRunning ? "停止" : "发送";
  button.setAttribute("aria-label", isRunning ? "停止" : "发送");
}

async function abortRun() {
  setStatus("正在停止…");
  try {
    await post("/api/chat/abort", {});
  } catch (error) {
    setStatus(error.message, true);
  }
}

function askQuestion(event) {
  // All questions are shown together and answered in one submit; asking them
  // one at a time turns a decision into a queue of dialogs.
  return new Promise((resolve) => {
    const dialog = $("question-dialog");
    const list = $("question-options");
    list.innerHTML = "";
    const questions = event.questions || [];
    const answers = new Array(questions.length).fill("");

    questions.forEach((item, index) => {
      const block = el("div", "question-block");
      block.appendChild(el("p", "question-text", item.question || ""));
      const choices = el("div", "question-choices");
      (item.options || []).forEach((choice) => {
        const button = el("button", "question-choice", choice);
        button.type = "button";
        button.addEventListener("click", () => {
          answers[index] = choice;
          choices.querySelectorAll(".question-choice").forEach((node) => {
            node.classList.toggle("picked", node === button);
          });
          const input = block.querySelector(".question-free");
          if (input) input.value = "";
        });
        choices.appendChild(button);
      });
      block.appendChild(choices);
      const input = el("input", "question-free");
      input.type = "text";
      input.placeholder = "或输入自定义回答";
      input.addEventListener("input", () => {
        answers[index] = input.value;
        choices.querySelectorAll(".question-choice").forEach((node) => {
          node.classList.remove("picked");
        });
      });
      block.appendChild(input);
      list.appendChild(block);
    });

    const finish = () => {
      dialog.close();
      resolve(answers);
    };
    $("question-send").onclick = finish;
    list.querySelectorAll(".question-free").forEach((input, index) => {
      if (index === 0) input.focus();
    });
    dialog.showModal();
  });
}

// ---------------------------------------------------------------- approval

function askApproval(event) {
  return new Promise((resolve) => {
    const dialog = $("approval-dialog");
    $("approval-what").textContent = event.tool + " · " + event.value;
    $("approval-detail").textContent = JSON.stringify(event.arguments, null, 2);
    const finish = (allowed) => {
      dialog.close();
      $("allow").onclick = null;
      $("deny").onclick = null;
      resolve(allowed);
    };
    $("allow").onclick = () => finish(true);
    $("deny").onclick = () => finish(false);
    dialog.showModal();
  });
}

// ---------------------------------------------------------------- menu

let menuOwner = null;

function closeMenu() {
  $("menu").hidden = true;
  menuOwner = null;
}

function openMenu(anchor, items) {
  const menu = $("menu");
  menu.innerHTML = "";
  for (const item of items) {
    if (item.group) {
      menu.appendChild(el("div", "group", item.group));
      continue;
    }
    const button = el("button");
    button.type = "button";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(Boolean(item.selected)));
    button.appendChild(el("span", "m-name", item.name));
    if (item.sub) button.appendChild(el("span", "m-sub", item.sub));
    button.addEventListener("click", () => {
      closeMenu();
      item.onPick();
    });
    menu.appendChild(button);
  }
  const rect = anchor.getBoundingClientRect();
  menu.hidden = false;
  const width = menu.offsetWidth;
  menu.style.left = Math.min(rect.left, window.innerWidth - width - 12) + "px";
  menu.style.top = Math.max(12, rect.top - menu.offsetHeight - 8) + "px";
  menuOwner = anchor;
}

function modelMenu(anchor) {
  const items = [];
  for (const provider of state.providers) {
    items.push({ group: provider.name + " · " + familyLabel(provider.family) });
    for (const model of provider.models || []) {
      const selected =
        provider.id === state.current.provider && model.id === state.current.model;
      items.push({
        name: model.id,
        selected,
        onPick: async () => {
          await post("/api/select", {
            provider: provider.id,
            model: model.id,
            effort: state.effort.current,
          });
          await refresh();
        },
      });
    }
  }
  if (!items.length) items.push({ name: "还没有配置提供商", onPick: openSettings });
  openMenu(anchor, items);
}

function effortMenu(anchor) {
  const labels = state.effort.labels || {};
  const items = [
    {
      group: "思考强度",
      name: "提供商默认",
      sub: "不发字段",
      selected: !state.effort.current,
      onPick: async () => {
        await post("/api/select", {
          provider: state.current.provider,
          model: state.current.model,
          effort: "off",
        });
        await refresh();
      },
    },
  ];
  for (const level of state.effort.levels || []) {
    items.push({
      name: (labels[level] || level) + " · " + level,
      selected: state.effort.current === level,
      onPick: async () => {
        await post("/api/select", {
          provider: state.current.provider,
          model: state.current.model,
          effort: level,
        });
        await refresh();
      },
    });
  }
  openMenu(anchor, items);
}

function familyLabel(family) {
  return (
    {
      anthropic_messages: "Anthropic",
      openai_chat_completions: "Chat",
      openai_responses: "Responses",
    }[family] || family
  );
}

// ---------------------------------------------------------------- settings

async function openSettings() {
  const value = await refresh();
  $("permission").value = value.permission || "ask";
  $("workspace_input").value = value.workspace || "";
  $("settings-error").textContent = "";
  renderProviders();
  $("settings-dialog").showModal();
}

function renderProviders() {
  const list = $("provider-list");
  list.innerHTML = "";
  for (const provider of state.providers) {
    const row = el("div", "provider-row");
    const dot = el("span", "pdot" + (provider.has_key ? " on" : ""));
    dot.title = provider.has_key ? "已配置密钥" : "未配置密钥";
    row.appendChild(dot);
    row.appendChild(el("span", "pname", provider.name));
    const info = el("span", "pinfo", provider.family + " · " + (provider.models || []).length + " 个模型");
    info.title = provider.base_url;
    row.appendChild(info);
    const edit = el("button", "pedit", "编辑");
    edit.type = "button";
    edit.addEventListener("click", () => openProviderDialog(provider));
    row.appendChild(edit);
    list.appendChild(row);
  }
}

function openProviderDialog(provider) {
  state.editingProvider = provider || null;
  $("provider-title").textContent = provider ? "编辑提供商" : "添加提供商";
  $("p-name").value = provider ? provider.name : "";
  $("p-family").value = provider ? provider.family : "anthropic_messages";
  $("p-base").value = provider ? provider.base_url : "";
  $("p-models").value = provider ? (provider.models || []).map((m) => m.id).join(", ") : "";
  $("p-key").value = "";
  $("p-key").placeholder = provider && provider.has_key ? "已配置，留空则不修改" : "sk-...";
  $("provider-error").textContent = "";
  $("p-delete").hidden = !provider;
  $("provider-dialog").showModal();
}

async function saveProvider() {
  const models = $("p-models")
    .value.split(/[,，\s]+/)
    .map((v) => v.trim())
    .filter(Boolean)
    .map((id) => ({ id }));
  const payload = {
    name: $("p-name").value.trim(),
    family: $("p-family").value,
    base_url: $("p-base").value.trim(),
    models,
    api_key: $("p-key").value.trim(),
  };
  try {
    if (state.editingProvider) {
      await put("/api/providers/" + encodeURIComponent(state.editingProvider.id), payload);
    } else {
      await post("/api/providers", payload);
    }
    $("provider-dialog").close();
    await refresh();
    renderProviders();
  } catch (error) {
    $("provider-error").textContent = error.message;
  }
}

async function deleteProvider() {
  if (!state.editingProvider) return;
  if (!confirm("删除这个提供商及其密钥？")) return;
  try {
    await api("/api/providers/" + encodeURIComponent(state.editingProvider.id), {
      method: "DELETE",
    });
    $("provider-dialog").close();
    await refresh();
    renderProviders();
  } catch (error) {
    $("provider-error").textContent = error.message;
  }
}

async function saveSettings() {
  try {
    await post("/api/settings", { permission: $("permission").value });
    const workspace = $("workspace_input").value.trim();
    if (workspace) await post("/api/workspace", { workspace });
    $("settings-dialog").close();
    await refresh();
  } catch (error) {
    $("settings-error").textContent = error.message;
  }
}

// ---------------------------------------------------------------- wiring

document.addEventListener("click", (event) => {
  if (menuOwner && !$("menu").contains(event.target) && event.target !== menuOwner) {
    closeMenu();
  }
});

document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => $(button.getAttribute("data-close")).close());
});

$("stat-tokens").addEventListener("click", toggleUsage);
$("stat-context").addEventListener("click", toggleUsage);
$("attach").addEventListener("click", () => $("image-input").click());
$("image-input").addEventListener("change", (event) => {
  addImages(event.target.files);
  event.target.value = "";
});
$("input").addEventListener("paste", (event) => {
  const files = Array.from(event.clipboardData?.files || []);
  if (files.length) {
    addImages(files);
    event.preventDefault();
  }
});
$("composer").addEventListener("dragover", (event) => event.preventDefault());
$("composer").addEventListener("drop", (event) => {
  if (event.dataTransfer?.files?.length) {
    event.preventDefault();
    addImages(event.dataTransfer.files);
  }
});

$("new").addEventListener("click", () => newSession().catch((e) => setStatus(e.message, true)));
$("settings").addEventListener("click", () => openSettings().catch((e) => setStatus(e.message, true)));
$("add-provider").addEventListener("click", () => openProviderDialog(null));
$("p-save").addEventListener("click", saveProvider);
$("p-delete").addEventListener("click", deleteProvider);
$("save-settings").addEventListener("click", saveSettings);

$("model-button").addEventListener("click", (event) => {
  event.stopPropagation();
  if (menuOwner === $("model-button")) return closeMenu();
  modelMenu($("model-button"));
});

$("effort-button").addEventListener("click", (event) => {
  event.stopPropagation();
  if (menuOwner === $("effort-button")) return closeMenu();
  effortMenu($("effort-button"));
});

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("input");
  const text = input.value.trim();
  if (state.running) {
    abortRun();
    return;
  }
  if (readingImages) {
    setStatus("图片还在读取，请稍候再发送", true);
    return;
  }
  if (!text && !pendingImages.length) return;
  if (typeof taskView !== "undefined") {
    if (taskView.current && pendingImages.length) {
      setStatus("任务输入暂不支持图片，请切换到普通会话发送", true);
      return;
    }
    if (text && !pendingImages.length && await naturalTaskControl(text)) {
      input.value = "";
      input.style.height = "auto";
      return;
    }
    if (taskView.current) {
      if (await sendTaskMessage(text)) {
        input.value = "";
        input.style.height = "auto";
      }
      return;
    }
  }
  input.value = "";
  input.style.height = "auto";
  send(text);
});

$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});

$("input").addEventListener("input", () => {
  const node = $("input");
  node.style.height = "auto";
  node.style.height = Math.min(node.scrollHeight, 220) + "px";
});

refresh()
  .then(() => {
    if (!state.providers.some((p) => p.has_key)) {
      setStatus("先添加模型提供商并填入密钥", true);
      // Still render the empty transcript, so the first screen is the real UI.
      renderTranscript();
      return;
    }
    setStatus("");
    if (state.sessions.length) return openSession(state.sessions[0].id);
    renderTranscript();
  })
  .catch((error) => setStatus(error.message, true));
