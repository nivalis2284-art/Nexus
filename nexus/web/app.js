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

// ---------------------------------------------------------------- session

async function newSession() {
  const created = await post("/api/sessions", {});
  await refresh();
  await openSession(created.id);
}

async function openSession(id) {
  const value = await api("/api/sessions/" + encodeURIComponent(id));
  state.sessionId = value.id;
  state.items = value.items || [];
  state.turns = state.items.filter((i) => i.role === "user").length;
  state.tokens = state.items.reduce((sum, item) => {
    const usage = item.usage || {};
    return sum + (usage.input_tokens || 0) + (usage.output_tokens || 0);
  }, 0);
  $("title").textContent = value.title || "新会话";
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
  for (const item of state.items) box.appendChild(nodeFor(item));
  updateStats();
  scrollDown();
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
    wrap.appendChild(el("div", "bubble", item.text || ""));
    wrap.appendChild(el("div", "stamp", clock(item.at)));
    return wrap;
  }
  const wrap = el("div", "msg assistant");
  if (item.thinking) wrap.appendChild(thinkingNode(item.thinking, false));
  wrap.appendChild(el("div", "answer", item.text || ""));
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

async function send(text) {
  if (state.running) return;
  if (!state.sessionId) await newSession();
  if (!state.sessionId) return;

  state.running = true;
  state.turns += 1;
  $("send").disabled = true;
  setStatus("运行中…");

  state.items.push({ role: "user", text, at: new Date().toISOString() });
  const box = $("transcript");
  box.innerHTML = "";
  for (const item of state.items) box.appendChild(nodeFor(item));

  const wrap = el("div", "msg assistant");
  let thinking = null;
  let thinkingText = "";
  let answer = el("div", "answer streaming-caret");
  wrap.appendChild(answer);
  box.appendChild(wrap);
  scrollDown();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, text }),
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
          thinkingText += event.text;
          if (!thinking) {
            thinking = thinkingNode("", true);
            wrap.insertBefore(thinking, answer);
          }
          thinking.querySelector(".tbody").textContent = thinkingText;
          if (thinking.querySelector(".tnote")) {
            thinking.querySelector(".tnote").textContent = thinkingText.length + " 字";
          }
          scrollDown();
        } else if (event.type === "delta") {
          answer.textContent += event.text;
          scrollDown();
        } else if (event.type === "tool_start") {
          answer.classList.remove("streaming-caret");
          if (thinking) thinking.classList.remove("live");
          box.insertBefore(toolNode({ name: event.tool.name, arguments: event.tool.arguments, text: "运行中…" }), wrap);
          thinking = null;
          thinkingText = "";
          scrollDown();
        } else if (event.type === "tool_result") {
          const nodes = box.querySelectorAll(".tool");
          const last = nodes[nodes.length - 1];
          if (last) {
            last.replaceWith(
              toolNode({
                name: event.tool.name,
                arguments: event.tool.arguments,
                text: event.tool.content,
                ok: event.tool.ok,
              })
            );
          }
          scrollDown();
        } else if (event.type === "approval") {
          askApproval(event).then((allowed) =>
            post("/api/approval", { token: event.token, allowed }).catch(() => {})
          );
        } else if (event.type === "error") {
          answer.textContent += "\n[错误] " + event.message;
          setStatus(event.message, true);
        } else if (event.type === "done") {
          answer.classList.remove("streaming-caret");
          if (thinking) thinking.classList.remove("live");
          const usage = (event.data && event.data.usage) || {};
          const total = (usage.input_tokens || 0) + (usage.output_tokens || 0);
          if (total) {
            state.tokens += total;
            updateStats();
          }
          setStatus("");
        }
      }
    }
  } catch (error) {
    answer.classList.remove("streaming-caret");
    answer.textContent += "\n[错误] " + error.message;
    setStatus(error.message, true);
  } finally {
    state.running = false;
    $("send").disabled = false;
    answer.classList.remove("streaming-caret");
    updateStats();
    await refresh().catch(() => {});
    if (state.sessionId) await openSession(state.sessionId).catch(() => {});
  }
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

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("input");
  const text = input.value.trim();
  if (!text || state.running) return;
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
