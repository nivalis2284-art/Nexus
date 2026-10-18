"use strict";

// One page, no framework. State lives in three variables and is re-rendered
// from scratch on change; the transcript is the only thing that appends.

const $ = (id) => document.getElementById(id);
const state = { sessions: [], current: null, running: false, items: [] };

async function api(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function setStatus(text, isError) {
  const node = $("status");
  node.textContent = text || "";
  node.className = "small" + (isError ? " error" : "");
}

// --- sessions ------------------------------------------------------------

async function refreshState() {
  const value = await api("/api/state");
  state.sessions = value.sessions || [];
  $("workspace").textContent = value.workspace || "";
  $("workspace").title = value.workspace || "";
  if (!value.has_key) {
    setStatus("未配置 API Key，打开设置填写", true);
  }
  renderSessions();
  return value;
}

function renderSessions() {
  const list = $("sessions");
  list.innerHTML = "";
  for (const session of state.sessions) {
    const item = el("li");
    if (session.id === state.current) item.classList.add("active");
    const label = el("span", "label", session.title || session.id);
    label.title = session.title || session.id;
    item.appendChild(label);
    const remove = el("button", "del", "\u00d7");
    remove.type = "button";
    remove.setAttribute("aria-label", "删除会话");
    remove.addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!confirm("删除这个会话？")) return;
      await api("/api/sessions/" + encodeURIComponent(session.id), { method: "DELETE" });
      if (state.current === session.id) {
        state.current = null;
        state.items = [];
        renderTranscript();
        $("title").textContent = "未选择会话";
      }
      await refreshState();
    });
    item.appendChild(remove);
    item.addEventListener("click", () => openSession(session.id));
    list.appendChild(item);
  }
}

async function newSession() {
  const created = await api("/api/sessions", { method: "POST" });
  await refreshState();
  await openSession(created.id);
}

async function openSession(id) {
  const value = await api("/api/sessions/" + encodeURIComponent(id));
  state.current = value.id;
  state.items = value.items || [];
  $("title").textContent = value.title || "新会话";
  renderSessions();
  renderTranscript();
  $("input").focus();
}

// --- transcript ----------------------------------------------------------

function renderTranscript() {
  const box = $("transcript");
  box.innerHTML = "";
  for (const item of state.items) box.appendChild(messageNode(item));
  box.scrollTop = box.scrollHeight;
}

function messageNode(item) {
  if (item.role === "tool") return toolNode(item);
  const wrap = el("div", "msg " + item.role);
  wrap.appendChild(el("div", "who", item.role === "user" ? "你" : item.role === "notice" ? "提示" : "Nexus"));
  const body = el("div", "body", item.text || "");
  wrap.appendChild(body);
  return wrap;
}

function toolNode(item) {
  const details = el("details", "tool");
  const summary = el("summary");
  const ok = item.ok !== false;
  summary.appendChild(el("span", "name", item.name || "tool"));
  summary.appendChild(el("span", ok ? "good" : "bad", ok ? "完成" : "失败"));
  if (item.arguments !== undefined && item.arguments !== null) {
    const brief = typeof item.arguments === "string" ? item.arguments : JSON.stringify(item.arguments);
    summary.appendChild(el("span", "small", brief.length > 80 ? brief.slice(0, 80) + "…" : brief));
  }
  details.appendChild(summary);
  details.appendChild(el("pre", "", item.text || ""));
  return details;
}

function appendStreamingBubble() {
  const box = $("transcript");
  const wrap = el("div", "msg assistant streaming");
  wrap.appendChild(el("div", "who", "Nexus"));
  const body = el("div", "body", "");
  wrap.appendChild(body);
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
  return body;
}

// --- send ----------------------------------------------------------------

async function send(text) {
  if (state.running) return;
  if (!state.current) {
    await newSession();
    if (!state.current) return;
  }
  state.running = true;
  $("send").disabled = true;
  setStatus("运行中…");
  state.items.push({ role: "user", text });
  renderTranscript();
  let body = appendStreamingBubble();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.current, text }),
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
        handleEvent(JSON.parse(line), body);
      }
    }
    if (buffer.trim()) handleEvent(JSON.parse(buffer), body);
  } catch (error) {
    body.parentElement.classList.remove("streaming");
    body.textContent += "\n[错误] " + error.message;
    setStatus(error.message, true);
  } finally {
    state.running = false;
    $("send").disabled = false;
    body.parentElement.classList.remove("streaming");
    await refreshState().catch(() => {});
    if (!setStatus.lastError) setStatus("");
  }
}

function handleEvent(event, body) {
  switch (event.type) {
    case "delta":
      body.textContent += event.text;
      body.parentElement.parentElement.scrollTop = 1e9;
      break;
    case "thinking":
      break;
    case "tool_start": {
      const node = toolNode({ name: event.tool.name, arguments: event.tool.arguments, text: "运行中…", ok: true });
      node.open = false;
      $("transcript").appendChild(node);
      body.parentElement.classList.remove("streaming");
      body = appendStreamingBubble();
      break;
    }
    case "tool_result": {
      const nodes = $("transcript").querySelectorAll(".tool");
      const last = nodes[nodes.length - 1];
      if (last) {
        last.replaceWith(toolNode({ name: event.tool.name, text: event.tool.content, ok: event.tool.ok, arguments: event.tool.arguments }));
      }
      break;
    }
    case "approval":
      askApproval(event).then((allowed) => {
        fetch("/api/approval", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: event.token, allowed }),
        });
      });
      break;
    case "error":
      body.parentElement.classList.remove("streaming");
      body.textContent += "\n[错误] " + event.message;
      setStatus(event.message, true);
      setStatus.lastError = true;
      break;
    case "done":
      body.parentElement.classList.remove("streaming");
      if (event.data && event.data.usage && event.data.usage.input_tokens) {
        const u = event.data.usage;
        setStatus(`输入 ${u.input_tokens || 0} · 输出 ${u.output_tokens || 0} tokens`);
      }
      break;
  }
}

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

// --- settings ------------------------------------------------------------

async function openSettings() {
  const value = await api("/api/state");
  const profile = value.profile || {};
  $("api_family").value = profile.api_family || "openai_chat_completions";
  $("base_url").value = profile.base_url || "";
  $("model").value = profile.model || "";
  $("context_window").value = profile.context_window_tokens || "";
  $("permission").value = value.permission || "ask";
  $("workspace_input").value = value.workspace || "";
  $("api_key").value = "";
  $("settings-error").textContent = "";
  $("settings-dialog").showModal();
}

async function saveSettings(event) {
  event.preventDefault();
  const payload = {
    profile: {
      api_family: $("api_family").value,
      base_url: $("base_url").value.trim(),
      model: $("model").value.trim(),
      context_window_tokens: Number($("context_window").value) || undefined,
    },
    permission: $("permission").value,
  };
  const key = $("api_key").value.trim();
  if (key) payload.api_key = key;
  try {
    await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const workspace = $("workspace_input").value.trim();
    if (workspace) {
      await api("/api/workspace", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace }),
      });
    }
    $("settings-dialog").close();
    await refreshState();
    setStatus("");
  } catch (error) {
    $("settings-error").textContent = error.message;
  }
}

// --- wiring --------------------------------------------------------------

$("new").addEventListener("click", () => newSession().catch((e) => setStatus(e.message, true)));
$("settings").addEventListener("click", () => openSettings().catch((e) => setStatus(e.message, true)));
$("save-settings").addEventListener("click", saveSettings);

$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("input");
  const text = input.value.trim();
  if (!text) return;
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
  node.style.height = Math.min(node.scrollHeight, 300) + "px";
});

$("hint").textContent = "回车发送 · Shift+回车换行";

refreshState()
  .then(() => {
    if (state.sessions.length) return openSession(state.sessions[0].id);
  })
  .catch((error) => setStatus(error.message, true));
