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
  contextWindow: null,
  contextOverride: null,
  editingProvider: null,
  editingModels: [],
  editingModelOrigins: new WeakMap(),
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
  state.contextWindow = value.context_window || state.contextLimit || null;
  state.contextOverride = value.context_limit || null;
  $("workspace").textContent = shortPath(value.workspace || "");
  $("workspace").title = value.workspace || "";
  renderModelLabel();
  renderEffortLabel();
  renderContextWindowInfo();
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

function renderContextWindowInfo() {
  const node = $("context-window-info");
  if (!node) return;
  const provider = currentProvider();
  const model = currentModel();
  const effective = state.contextWindow || state.contextLimit ||
    (provider && model && model.context_window_tokens) || null;
  if (!effective) {
    node.textContent = "";
    node.hidden = true;
    return;
  }
  node.hidden = false;
  node.textContent = "当前模型上下文预算：" + Number(effective).toLocaleString() +
    " tok" + (state.contextOverride
      ? "（受全局上下文上限覆盖；全局值优先于模型设置）"
      : "（可在编辑提供商时为每个模型单独设置）");
}

function syncTranscriptNodes(box, entries, create, update) {
  const old = new Map([...box.children].map(node => [node.dataset.key, node]));
  entries.forEach((entry, index) => {
    let node = old.get(entry.key);
    if (!node) node = create(entry);
    node.dataset.key = entry.key;
    update(node, entry);
    if (box.children[index] !== node) box.insertBefore(node, box.children[index] || null);
    old.delete(entry.key);
  });
  // Keys describe current identity; a poll that only adds ids must not leave
  // an earlier copy of the same visible item behind under a different key.
  for (const node of old.values()) node.remove();
}

function reconcileTranscript(box, items, workspace) {
  const entries = [];
  let phase = null;
  const flush = () => { if (phase) entries.push(phase); phase = null; };
  items.forEach((item, index) => {
    const key = item.role === "tool"
      ? String(item.parent_message_id ? item.parent_message_id + ":" + item.tool_call_id : (item.seq || index))
      : String(item.message_id || item.key || index);
    if (item.role === "user" || item.role === "notice") {
      flush(); entries.push({key: "message:" + key, item, workspace}); return;
    }
    if (item.thinking || item.role === "tool") {
      if (!phase) phase = {key: "phase:" + key, steps: [], live: false, elapsed: null};
      phase.live ||= Boolean(item.live);
      if (typeof item.elapsed === "number") phase.elapsed = item.elapsed;
      if (item.thinking) phase.steps.push({key: "thinking:" + key, thinking: item.thinking});
      if (item.role === "tool") phase.steps.push({key: "tool:" + key, item});
    }
    if (item.role === "assistant" && item.text) {
      if (phase && typeof item.elapsed === "number") phase.elapsed = item.elapsed;
      flush(); entries.push({key: "answer:" + key, item, workspace});
    }
  });
  flush();
  syncTranscriptNodes(box, entries, entry => entry.steps ? phaseNode(entry) : nodeFor(entry.item, entry.workspace),
    (node, entry) => {
      if (entry.steps) { updatePhase(node, entry); return; }
      const signature = JSON.stringify(entry.item);
      if (node._signature === signature) return;
      if (entry.item.role === "assistant") {
        const answer = node.querySelector(".answer");
        const content = renderContent(entry.item.text || "", entry.workspace);
        answer.replaceChildren(content);
        if (entry.item.elapsed > 0) {
          let meta = node.querySelector(".msg-meta");
          if (!meta) { meta = el("div", "msg-meta"); node.appendChild(meta); }
          meta.textContent = "用时 " + formatElapsed(entry.item.elapsed);
        }
      } else if (node._signature !== undefined) {
        const fresh = nodeFor(entry.item, entry.workspace);
        node.replaceChildren(...fresh.childNodes);
      }
      node._signature = signature;
    });
}

function renderTranscript(options) {
  const box = $("transcript"), scroller = $("scroller");
  const following = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48;
  const previousTop = scroller.scrollTop;
  const preserve = Boolean(options && options.preserveScroll);
  box.classList.toggle("empty", !state.items.length);
  $("main").classList.toggle("empty", !state.items.length);
  if (!state.items.length) {
    if (!box.querySelector(".welcome")) {
      box.replaceChildren();
      const hello = el("div", "welcome ling-welcome");
      hello.appendChild(el("div", "welcome-title", "灵"));
      hello.appendChild(el("div", "welcome-sub", "你可以在这里继续交流。复杂工作会独立推进，需要你决定的请求会出现在上方。"));
      box.appendChild(hello);
    }
  } else {
    const welcome = box.querySelector(".welcome");
    if (welcome) welcome.remove();
    reconcileTranscript(box, state.items, state.workspacePath);
  }
  updateStats();
  if (!preserve || following) scroller.scrollTop = scroller.scrollHeight;
  else scroller.scrollTop = previousTop;
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
  const node = el("details", "phase");
  node.open = Boolean(batch.live);
  const summary = el("summary");
  summary.appendChild(el("span", "tw", "▸"));
  summary.appendChild(el("span", "plabel"));
  summary.appendChild(el("span", "pnote"));
  node.appendChild(summary);
  node.appendChild(el("div", "pbody"));
  return node;
}

function updatePhase(node, batch) {
  const body = node.querySelector(".pbody");
  const following = body.scrollHeight - body.scrollTop - body.clientHeight < 48;
  const previousTop = body.scrollTop;
  node.classList.toggle("live", batch.live);
  node.querySelector(".plabel").textContent = batch.live ? "正在处理" : "处理过程";
  node.querySelector(".pnote").textContent = batch.elapsed
    ? "（本轮用时 " + formatElapsed(batch.elapsed) + "）" : "（" + batch.steps.length + " 项）";
  syncTranscriptNodes(body, batch.steps,
    step => step.item ? stepTool(step.item) : stepThinking(step.thinking),
    (child, step) => {
      const signature = JSON.stringify(step);
      if (child._signature === signature) return;
      if (step.thinking) {
        child.querySelector(".stitle").textContent = truncate(step.thinking.split("\n").find(line => line.trim()) || "", 120);
        child.querySelector(".scount").textContent = step.thinking.length + " 字";
        child.querySelector(".sbody").textContent = step.thinking;
      } else if (child._signature !== undefined) {
        const fresh = stepTool(step.item);
        child.className = fresh.className;
        child.replaceChildren(...fresh.childNodes);
      }
      child._signature = signature;
    });
  body.scrollTop = following ? body.scrollHeight : previousTop;
}

/** A reasoning step keeps one readable line while collapsed. */
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

/** A step inside the phase. Collapsed by default; shows one line when closed. */
function stepTool(item) {
  return toolNode(item);
}

function truncate(text, size) {
  return text.length > size ? text.slice(0, size) + "…" : text;
}

function nodeFor(item, workspace) {
  if (item.role === "tool") {
    return el("div", "small", (item.ok === false ? "✕ " : "✓ ") + (item.name || "工具"));
  }
  if (item.role === "notice") {
    const wrap = el("div", "msg");
    wrap.appendChild(el("div", "small error", item.text || ""));
    return wrap;
  }
  if (item.role === "user") {
    const wrap = el("div", "msg user");
    if (item.text) wrap.appendChild(el("div", "bubble", item.text));
    if (item.attachments && item.attachments.length) {
      const files = el("div", "message-files");
      item.attachments.forEach((file) => {
        const link = el("a", "file-link", file.name + " · " + formatFileSize(file.size));
        link.href = file.url;
        link.download = file.name;
        link.title = file.note || "下载原文件";
        files.appendChild(link);
        files.appendChild(el("span", "small", file.note || "原文件已保存"));
        if (IMAGE_TYPES.has(file.mime)) {
          const picture = el("img", "file-thumbnail");
          picture.src = file.url;
          picture.alt = file.name;
          files.appendChild(picture);
        }
      });
      wrap.appendChild(files);
    }
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
  const answer = el("div", "answer");
  answer.appendChild(renderContent(item.text || "", workspace));
  wrap.appendChild(answer);
  const stamp = el("div", "msg-meta");
  if (typeof item.elapsed === "number" && item.elapsed > 0) {
    stamp.textContent = "用时 " + formatElapsed(item.elapsed);
  }
  if (stamp.textContent) wrap.appendChild(stamp);
  return wrap;
}

function scrollDown() {
  const box = $("scroller");
  box.scrollTop = box.scrollHeight;
}

function rememberCurrentScroll() {
  const box = $("scroller");
  if (!state.scrollKey || !box || box.hidden) return;
  state.scrollPositions = state.scrollPositions || {};
  state.scrollAtBottom = state.scrollAtBottom || {};
  state.scrollPositions[state.scrollKey] = Number(box.scrollTop) || 0;
  state.scrollAtBottom[state.scrollKey] =
    box.scrollHeight - box.scrollTop - (Number(box.clientHeight) || 0) < 48;
}

function restoreViewScroll(key) {
  const box = $("scroller");
  state.scrollPositions = state.scrollPositions || {};
  state.scrollKey = key || "";
  const saved = state.scrollPositions[state.scrollKey];
  const atBottom = Boolean(state.scrollAtBottom && state.scrollAtBottom[state.scrollKey]);
  const apply = () => {
    box.scrollTop = saved === undefined || atBottom ? box.scrollHeight : saved;
  };
  apply();
  setTimeout(apply, 0);
}

function applyUsageSnapshot(value) {
  if (value.usage) state.usage = value.usage;
  if (value.context !== undefined) state.context = value.context;
  state.tokens = state.usage.total || 0;
  updateStats();
}

function updateStats() {
  $("stat-turn").textContent = state.turns + " 轮";
  $("stat-tokens").textContent = (state.usage.estimated ? "~" : "") + (state.tokens ? state.tokens.toLocaleString() + " tok" : "0 tok");
  const limit = state.contextWindow || state.contextLimit;
  const ctx = state.context;
  const used = ctx ? (ctx.system || 0) + (ctx.tools || 0) + (ctx.messages || 0) : null;
  $("stat-context").textContent = used !== null && limit
    ? "上下文 ~" + Math.min(100, Math.round(used / limit * 100)) + "%" : "上下文 —";
  renderUsage();
}

// ---------------------------------------------------------------- attachments

// Images waiting to be sent. Kept outside `state` because they are transient
// per composition, not session data.
let pendingImages = [];
let pendingFiles = [];
// Files still being read. Sending before these settle would silently omit the
// picture the user just attached, so the composer waits for read completion.
let readingImages = 0;

const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
const MAX_IMAGES = 8;
const MAX_FILE_BYTES = 25 * 1024 * 1024;
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);

function formatFileSize(size) {
  return size >= 1024 * 1024 ? (size / (1024 * 1024)).toFixed(1) + " MiB" : Math.ceil(size / 1024) + " KiB";
}

function addFiles(files) {
  for (const file of Array.from(files || [])) {
    if (pendingImages.length + pendingFiles.length + readingImages >= MAX_IMAGES) {
      setStatus("每次最多 8 个附件", true);
      break;
    }
    if (IMAGE_TYPES.has(file.type)) {
      addImages([file]);
    } else if (file.size > MAX_FILE_BYTES) {
      setStatus("文件超过 25 MiB：" + file.name, true);
    } else {
      pendingFiles.push({file, name: file.name, size: file.size, status: "等待发送"});
    }
  }
  renderAttachments();
}

async function uploadFiles(kind, owner, items) {
  const result = [];
  for (const item of items) {
    const ownerKey = kind + "/" + owner;
    if (!item.uploaded || item.ownerKey !== ownerKey) {
      item.status = "上传并读取中…";
      renderAttachments();
      try {
        const response = await fetch("/api/" + ownerKey + "/attachments?name=" + encodeURIComponent(item.name), {
          method: "POST", headers: {"Content-Type": item.file.type || "application/octet-stream"}, body: item.file,
        });
        const value = await response.json();
        if (!response.ok) throw new Error(value.error || "附件上传失败");
        item.uploaded = value;
        item.ownerKey = ownerKey;
        item.status = value.note;
      } catch (error) {
        item.status = "上传失败，可重新发送";
        renderAttachments();
        throw error;
      }
    }
    result.push(item.uploaded);
  }
  return result;
}

function renderAttachments() {
  const strip = $("attachments");
  strip.innerHTML = "";
  strip.hidden = pendingImages.length + pendingFiles.length === 0;
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
  pendingFiles.forEach((file, index) => {
    const chip = el("div", "attach-chip file-chip");
    chip.appendChild(el("span", "", file.name + " · " + formatFileSize(file.size)));
    chip.appendChild(el("span", "small", file.status || "等待发送"));
    const remove = el("button", "attach-x", "×");
    remove.type = "button";
    remove.title = "移除";
    remove.addEventListener("click", () => { pendingFiles.splice(index, 1); renderAttachments(); });
    chip.appendChild(remove);
    strip.appendChild(chip);
  });
}

function addImages(files) {
  const incoming = Array.from(files || []).filter((file) =>
    file.type.startsWith("image/")
  );
  for (const file of incoming) {
    if (pendingImages.length + pendingFiles.length + readingImages >= MAX_IMAGES) {
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
        file,
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
  if (usage.estimated) return "—";
  const denominator = usage.total_input || 0;
  if (!denominator) return "—";
  return Math.round((usage.cached / denominator) * 100) + "%";
}

function renderUsage() {
  const panel = $("usage-panel");
  if (panel.hidden) return;
  const previousTop = panel.scrollTop;

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
  const grand = u.total || 0;
  parts.push(
    '<div class="usage-head usage-q">' +
      "<span>Token 用量</span>" +
      '<span class="usage-total"><b>' + (u.estimated ? "~" : "") + (grand ? grand.toLocaleString() : 0) + "</b> tok</span>" +
      "</div>"
  );
  parts.push(
    '<ul class="usage-list">' +
      "<li>缓存命中<em>" + cacheRate(u) + "</em></li>" +
      "<li>未缓存输入<em>" + (u.input ? u.input.toLocaleString() : 0) + " tok</em></li>" +
      "<li>缓存读取<em>" + (u.cached ? u.cached.toLocaleString() : 0) + " tok</em></li>" +
      "<li>缓存写入<em>" + (u.created ? u.created.toLocaleString() : 0) + " tok</em></li>" +
      "<li>输出<em>" + (u.output ? u.output.toLocaleString() : 0) + " tok</em></li>" +
      "</ul>"
  );
  parts.push('<p class="usage-note">~ 表示估算；累计用量与上下文占用分别统计。</p>');
  if (u.has_usage && u.complete === false) {
    parts.push('<p class="usage-note">部分消息未报告完整用量，以上为已记录部分。</p>');
  }
  if (u.estimated) parts.push('<p class="usage-note">旧记录缺少缓存口径，累计用量为估算。</p>');
  panel.innerHTML = parts.join("");
  panel.scrollTop = previousTop;
  positionUsage();
}

let usageAnchor = null;

function positionUsage() {
  const panel = $("usage-panel");
  if (panel.hidden || !usageAnchor) return;
  const rect = usageAnchor.getBoundingClientRect();
  const width = panel.offsetWidth;
  panel.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + "px";
  panel.style.top = Math.max(8, rect.top - panel.offsetHeight - 8) + "px";
}

function toggleUsage(event) {
  const panel = $("usage-panel");
  panel.hidden = !panel.hidden;
  usageAnchor = event ? event.currentTarget : $("stat-context");
  renderUsage();
  positionUsage();
  usageAnchor.setAttribute("aria-expanded", String(!panel.hidden));
}

function closeUsage() {
  $("usage-panel").hidden = true;
  $("stat-tokens").setAttribute("aria-expanded", "false");
  $("stat-context").setAttribute("aria-expanded", "false");
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
  const [value, instructions] = await Promise.all([refresh(), api("/api/instructions")]);
  $("permission").value = value.permission || "ask";
  $("workspace_input").value = value.workspace || "";
  $("instructions").value = instructions.text || "";
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
  state.editingModels = provider
    ? (provider.models || []).map((model) => Object.assign({}, model))
    : [];
  state.editingModelOrigins = new WeakMap();
  for (const model of state.editingModels) {
    if (model.id) state.editingModelOrigins.set(model, model.id);
  }
  $("provider-title").textContent = provider ? "编辑提供商" : "添加提供商";
  $("p-name").value = provider ? provider.name : "";
  $("p-family").value = provider ? provider.family : "anthropic_messages";
  $("p-base").value = provider ? provider.base_url : "";
  $("p-model-new").value = "";
  $("p-models-error").textContent = "";
  $("p-key").value = "";
  $("p-key").placeholder = provider && provider.has_key ? "已配置，留空则不修改" : "sk-...";
  $("provider-error").textContent = "";
  $("p-delete").hidden = !provider;
  renderProviderModels();
  $("provider-dialog").showModal();
}

function renderProviderModels() {
  const list = $("p-models-list");
  list.innerHTML = "";
  if (!state.editingModels.length) {
    list.appendChild(el("p", "provider-model-empty",
      "还没有模型。至少添加一个，或先填写模型名后保存。"));
    return;
  }
  state.editingModels.forEach((model, rowIndex) => {
    const row = el("div", "provider-model-row");
    const name = el("input", "provider-model-name");
    name.type = "text";
    name.value = model.id || "";
    name.placeholder = "模型名";
    name.setAttribute("aria-label", "模型名");
    name.addEventListener("input", () => { model.id = name.value; });
    row.appendChild(name);

    const windowLabel = el("label", "provider-model-window");
    windowLabel.appendChild(el("span", "provider-model-window-label", "上下文窗口"));
    const windowInput = el("input", "provider-model-window-input");
    windowInput.type = "number";
    windowInput.min = "1";
    windowInput.step = "1";
    windowInput.inputMode = "numeric";
    windowInput.placeholder = "默认";
    windowInput.value = model.context_window_tokens
      ? String(model.context_window_tokens) : "";
    windowInput.setAttribute("aria-label", (model.id || "模型") + " 的上下文窗口 token");
    windowInput.addEventListener("input", () => {
      const raw = windowInput.value.trim();
      if (!raw) delete model.context_window_tokens;
      else model.context_window_tokens = raw;
    });
    windowLabel.appendChild(windowInput);
    windowLabel.appendChild(el("span", "provider-model-window-unit", "tok"));
    row.appendChild(windowLabel);

    const remove = el("button", "provider-model-remove", "移除");
    remove.type = "button";
    remove.setAttribute("aria-label", "移除模型 " + (model.id || ""));
    remove.addEventListener("click", () => {
      state.editingModels.splice(rowIndex, 1);
      renderProviderModels();
    });
    row.appendChild(remove);
    list.appendChild(row);
  });
}

function addProviderModel() {
  const input = $("p-model-new");
  const id = input.value.trim();
  if (!id) return;
  if (state.editingModels.some((model) => model.id === id)) {
    $("p-models-error").textContent = "模型名不能重复。";
    return;
  }
  $("p-models-error").textContent = "";
  const model = { id };
  state.editingModels.push(model);
  input.value = "";
  renderProviderModels();
  input.focus();
}

function providerModelsPayload() {
  if (!state.editingModels.length) throw new Error("请至少配置一个模型");
  const models = [];
  const seen = new Set();
  const rows = [];
  for (const model of state.editingModels) {
    const id = String(model.id || "").trim();
    if (!id) continue;
    if (seen.has(id)) throw new Error("模型名不能重复：" + id);
    seen.add(id);
    const entry = Object.assign({}, model, { id });
    rows.push({ id, origin: state.editingModelOrigins.get(model) || null });
    const raw = String(entry.context_window_tokens ?? "").trim();
    if (!raw) delete entry.context_window_tokens;
    else if (!/^\d+$/.test(raw) || Number(raw) <= 0) {
      throw new Error("模型“" + id + "”的上下文窗口需要填写正整数，或留空使用默认值。");
    } else {
      entry.context_window_tokens = Number(raw);
    }
    models.push(entry);
  }
  return { models, rows };
}

// Selection is stored by model name. Match the selected row by the original
// model id kept on its edit entry; row positions change when rows are removed.
function renamedSelectedModel(provider, rows) {
  if (!state.current || state.current.provider !== provider.id) return null;
  const selected = state.current.model;
  if (rows.some((model) => model.id === selected)) return null;
  const renamed = rows.find(
    (model) => model.origin === selected && model.id !== selected,
  );
  return renamed ? renamed.id : null;
}

async function saveProvider() {
  let payload;
  try {
    const edited = providerModelsPayload();
    payload = {
      name: $("p-name").value.trim(),
      family: $("p-family").value,
      base_url: $("p-base").value.trim(),
      models: edited.models,
    };
    if ($("p-key").value.trim()) payload.api_key = $("p-key").value.trim();
    if (state.editingProvider) {
      const previous = state.editingProvider;
      const renamed = renamedSelectedModel(previous, edited.rows);
      await put("/api/providers/" + encodeURIComponent(state.editingProvider.id), payload);
      if (renamed) {
        await post("/api/select", {
          provider: previous.id, model: renamed,
          effort: state.current.effort,
        });
      }
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
    await post("/api/settings", {
      permission: $("permission").value,
      instructions: $("instructions").value,
      workspace: $("workspace_input").value.trim(),
    });
    $("settings-dialog").close();
    await refresh();
  } catch (error) {
    $("settings-error").textContent = error.message;
  }
}

// ---------------------------------------------------------------- wiring

document.addEventListener("click", (event) => {
  if (!$("usage-panel").contains(event.target) && event.target !== $("stat-tokens") && event.target !== $("stat-context")) closeUsage();
  if (menuOwner && !$("menu").contains(event.target) && event.target !== menuOwner) {
    closeMenu();
  }
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape") { closeUsage(); closeMenu(); }
});
window.addEventListener("resize", positionUsage);

document.querySelectorAll("[data-close]").forEach((button) => {
  button.addEventListener("click", () => $(button.getAttribute("data-close")).close());
});

$("stat-tokens").addEventListener("click", toggleUsage);
$("stat-context").addEventListener("click", toggleUsage);
$("attach").addEventListener("click", () => $("image-input").click());
$("image-input").addEventListener("change", (event) => {
  addFiles(event.target.files);
  event.target.value = "";
});
$("input").addEventListener("paste", (event) => {
  const files = Array.from(event.clipboardData?.files || []);
  if (files.length) {
    addFiles(files);
    event.preventDefault();
  }
});
$("composer").addEventListener("dragover", (event) => event.preventDefault());
$("composer").addEventListener("drop", (event) => {
  if (event.dataTransfer?.files?.length) {
    event.preventDefault();
    addFiles(event.dataTransfer.files);
  }
});

$("settings").addEventListener("click", () => openSettings().catch((e) => setStatus(e.message, true)));
$("add-provider").addEventListener("click", () => openProviderDialog(null));
$("p-model-add").addEventListener("click", addProviderModel);
$("p-model-new").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    addProviderModel();
  }
});
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
  if (state.preparing) return;
  if (readingImages) {
    setStatus("图片还在读取，请稍候再发送", true);
    return;
  }
  if (!text && !pendingImages.length && !pendingFiles.length) return;
  if (typeof lingView !== "undefined" && lingView.active) {
    const viewVersion = state.viewVersion;
    const sent = await submitLingMessage(text);
    if (!sent) return;
    return;
  }
  await openLing({keepTranscript: lingView.itemsSignature !== "[]"});
  if (typeof lingView !== "undefined" && lingView.active) {
    const sent = await submitLingMessage(text);
    if (!sent) return;
  } else {
    setStatus("灵还没有准备好，请稍后重试", true);
  }
});

$("input").addEventListener("keydown", (event) => {
  if (event.isComposing || event.keyCode === 229) return;
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

async function startApp() {
  const initialViewVersion = state.viewVersion;
  try {
    await refresh();
    if (state.viewVersion !== initialViewVersion) return;
    setStatus("");
    await openLing({keepTranscript: true});
  } catch (error) {
    setStatus(error.message, true);
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", startApp, { once: true });
} else {
  startApp();
}
