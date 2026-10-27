"use strict";

// The continuous entry point. The server owns the Ling session and its input
// queue; this module renders snapshots and submits intent. Live events update
// the current transcript; full snapshots recover after reconnect or a durable
// boundary.

const lingView = {
  active: false,
  sessionId: null,
  items: [],
  itemsSignature: "",
  running: false,
  queueCount: 0,
  inputs: [],
  tasks: [],
  requests: [],
  requestDrafts: new Map(),
  polling: false,
  refreshPending: false,
  submitting: false,
  sentRequests: new Map(),
  initialized: false,
  renderedTaskState: "",
  renderedRequestsSignature: "",
  live: null,
  liveRevision: 0,
};

function lingRequestId(prefix) {
  return (prefix || "ling") + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

function showLingChrome(show) {
  $("ling-bar").hidden = !show;
  $("ling-mode-label").hidden = !show;
  $("ling-stop").hidden = !show || !lingView.running;
  $("ling-inputs").hidden = !show || !lingView.inputs.length;
  $("ling-requests").hidden = !show || !lingView.requests.length;
}

function clearLingView() {
  lingView.active = false;
  showLingChrome(false);
}

function renderLingStatus() {
  if (!lingView.active) {
    return;
  }
  if (lingView.connectionError) {
    $("ling-state-text").textContent = "连接暂时中断，稍后自动重试";
    $("ling-stop").hidden = !lingView.running;
    return;
  }
  const stateText = $("ling-state-text");
  if (lingView.running && (lingView.requests || []).some((item) => item.status !== "resolved")) {
    stateText.textContent = "灵在等你处理请求，其他交流仍可继续";
  }
  else if (lingView.running) stateText.textContent = "灵正在处理当前输入";
  else if (lingView.inputs.some((item) => item.status === "held")) {
    stateText.textContent = "有输入等待恢复";
  } else if (lingView.inputs.some((item) => item.status === "interrupted")) {
    stateText.textContent = "上次交互中断；核查后可发新消息继续";
  } else stateText.textContent = "灵在这里，随时可以继续";
  $("ling-queue-count").textContent = lingView.queueCount
    ? lingView.queueCount + " 条输入排队"
    : "";
  $("ling-stop").hidden = !lingView.running;
  $("ling-resume").hidden = !lingView.inputs.some((item) => item.status === "held");
  $("ling-pause-all").hidden = !lingView.tasks.some((task) =>
    ["running", "queued", "pending", "waiting_user"].includes(task.state));
  const send = $("send");
  send.classList.remove("stop");
  send.textContent = "↑";
  send.title = lingView.running ? "加入队列，不会停止当前输入" : "发送";
  send.setAttribute("aria-label", "发送");
  send.disabled = false;
}

function lingTaskLabel(state) {
  return {
    pending: "待执行", paused: "已挂起", queued: "排队", running: "执行中",
    pausing: "暂停中", waiting_user: "等待你", completed: "已完成", cancelled: "已取消",
  }[state] || state || "任务";
}

function renderLingTasks() {
  if (typeof taskView !== "undefined" && JSON.stringify(taskView.tasks) !== JSON.stringify(lingView.tasks)) {
    taskView.tasks = lingView.tasks;
    renderTasks();
  }
  const box = $("ling-tasks");
  const nextState = JSON.stringify(lingView.tasks);
  if (nextState === lingView.renderedTaskState) {
    renderComposerTargets();
    return;
  }
  lingView.renderedTaskState = nextState;
  box.innerHTML = "";
  for (const task of lingView.tasks.slice(0, 5)) {
    const button = el("button", "ling-task", task.title || "未命名任务");
    button.type = "button";
    button.title = (task.status_detail || task.goal || task.title || "") + " · 打开任务详情";
    const pending = Array.isArray(task.pending_interactions)
      ? task.pending_interactions.length
      : Number(task.pending_interactions || 0);
    button.appendChild(el("span", "ling-task-state", (task.state_label || lingTaskLabel(task.state)) +
      (pending > 0 ? " · 待处理" : "")));
    button.addEventListener("click", () => openLingTask(task.id));
    box.appendChild(button);
  }
  renderComposerTargets();
}

function renderComposerTargets() {
  const select = $("composer-task");
  if (!select) return;
  const signature = JSON.stringify(lingView.tasks.map((task) =>
    [task.id, task.title, task.state]));
  if (signature === lingView.renderedTargetsSignature) return;
  lingView.renderedTargetsSignature = signature;
  const selected = select.value;
  select.innerHTML = "";
  const ling = el("option", "", "灵");
  ling.value = "";
  select.appendChild(ling);
  for (const task of lingView.tasks) {
    if (task.state === "cancelled") continue;
    const option = el("option", "", (task.parent_id ? "↳ 子代理：" : "") + (task.title || "未命名工作") + (task.state === "completed" ? "（已完成）" : ""));
    option.value = task.id;
    select.appendChild(option);
  }
  if (selected && [...select.children].some((option) => option.value === selected)) {
    select.value = selected;
  }
}

function renderLingInputs() {
  const box = $("ling-inputs");
  box.innerHTML = "";
  const visible = (lingView.inputs || []).filter((item) =>
    ["pending", "queued", "running", "failed", "held", "interrupted"].includes(item.status));
  box.hidden = !lingView.active || !visible.length;
  if (!visible.length) return;
  for (const item of visible) {
    const row = el("div", "ling-input-row status-" + item.status);
    row.appendChild(el("span", "ling-input-dot", item.status === "running" ? "•" : "○"));
    const body = el("div", "ling-input-body");
    body.appendChild(el("span", "ling-input-text", item.text || "附件输入"));
    const label = {
      pending: "已接收，排队等待处理",
      queued: "已接收排队，当前输入完成后处理",
      running: "正在处理",
      held: "重启后暂停；需要你确认继续",
      interrupted: "上次处理已中断；检查已生效操作后发送新消息继续",
      failed: "处理失败" + (item.error ? "：" + item.error : "（未提供错误详情）"),
    }[item.status] || item.status;
    body.appendChild(el("span", "ling-input-status", label));
    row.appendChild(body);
    if (item.status === "failed") row.classList.add("has-error");
    box.appendChild(row);
  }
}

function liveRevision(event) {
  const value = event && (event.revision ?? (event.live && event.live.revision));
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function mergeLiveItems(items, live) {
  const out = items.filter((item) => !item.live);
  if (!live || typeof live !== "object") return out;
  if (live.text || live.thinking) {
    const messageId = live.message_id || "";
    const index = messageId
      ? out.findIndex((item) => item.role === "assistant" && item.message_id === messageId)
      : -1;
    const item = {role: "assistant", message_id: messageId, text: live.text || "",
      thinking: live.thinking || "", live: true,
      key: "live:" + (messageId || "current")};
    if (index >= 0) out[index] = item;
    else out.push(item);
  }
  if (live.tool) {
    const tool = live.tool;
    const callId = tool.id || tool.tool_call_id || "";
    const parentId = tool.parent_message_id || live.message_id || "";
    const index = callId
      ? out.findIndex((item) => item.role === "tool" &&
          (item.tool_call_id === callId || item.id === callId) &&
          (parentId ? item.parent_message_id === parentId : !item.parent_message_id))
      : -1;
    const item = {role: "tool", name: tool.name || "",
      text: tool.content || (tool.ok === undefined ? "正在调用" : ""),
      arguments: tool.arguments, ok: tool.ok, tool_call_id: callId,
      parent_message_id: parentId, live: true,
      key: "live-tool:" + (parentId || "-") + ":" + (callId || "current")};
    if (index >= 0) out[index] = item;
    else out.push(item);
  }
  if (live.error) out.push({role: "notice", text: live.error, key: "live-error"});
  return out;
}

function renderLingItems(preserveScroll) {
  if (!lingView.active) return;
  state.sessionId = lingView.sessionId;
  state.items = mergeLiveItems(lingView.items, lingView.live);
  state.turns = state.items.filter((item) => item.role === "user").length;
  renderTranscript({preserveScroll: Boolean(preserveScroll)});
}

function setLingLive(event) {
  const revision = liveRevision(event);
  if (revision < lingView.liveRevision) return false;
  lingView.liveRevision = revision;
  const live = event.live && typeof event.live === "object" ? event.live : null;
  lingView.live = live && Object.keys(live).length ? live : null;
  if (["delta", "thinking", "tool_start", "tool_result", "error"].includes(event.type)) {
    lingView.running = true;
    state.running = true;
    renderLingStatus();
  }
  if (["delta", "thinking", "tool_start"].includes(event.type)) {
    lingView.running = true;
    state.running = true;
  } else if (event.type === "done" || (event.type === "refresh" && event.clear)) {
    lingView.running = false;
    state.running = false;
  }
  if (lingView.live && lingView.live.context !== undefined &&
      typeof applyUsageSnapshot === "function") {
    applyUsageSnapshot({context: lingView.live.context});
  }
  return true;
}

function renderLingSnapshot(value) {
  if (!lingView.active) return;
  if (lingView.sessionId && value.session_id && value.session_id !== lingView.sessionId) return;
  if (!lingView.sessionId && value.session_id) lingView.sessionId = value.session_id;
  lingView.queueCount = Number(value.queue_count || 0);
  lingView.inputs = Array.isArray(value.inputs) ? value.inputs : [];
  lingView.tasks = Array.isArray(value.tasks) ? value.tasks : [];
  const activeMain = value.interaction || value.interaction_request || null;
  const mainRequests = Array.isArray(value.main_requests) ? value.main_requests.slice() : [];
  if (activeMain && !mainRequests.some((item) => requestKey(item) === requestKey(activeMain)) &&
      !(lingView.requestAnswered || new Map()).has(requestKey(activeMain))) {
    mainRequests.unshift({...activeMain, status: activeMain.status || "pending"});
  }
  lingView.requests = mainRequests;
  if (lingView.requestAnswered) {
    for (const [key, at] of [...lingView.requestAnswered]) {
      if (!mainRequests.some((item) => requestKey(item) === key) || Date.now() - at > 10000) {
        lingView.requestAnswered.delete(key);
      }
    }
  }

  const snapshotItems = (Array.isArray(value.items) ? value.items : []).filter((item) => !item.live);
  const snapshotRevision = Number(value.live_revision ?? (value.live && value.live.revision) ?? 0);
  const newerLive = snapshotRevision < lingView.liveRevision;
  if (!newerLive) {
    lingView.liveRevision = snapshotRevision;
    lingView.live = value.live && typeof value.live === "object" && Object.keys(value.live).length
      ? value.live : null;
  }
  if (!newerLive) {
    lingView.running = Boolean(value.running);
    state.running = lingView.running;
  }
  const signature = JSON.stringify(snapshotItems);
  if (signature !== lingView.itemsSignature) {
    lingView.itemsSignature = signature;
    lingView.items = snapshotItems;
    renderLingItems(true);
  }
  if (typeof applyUsageSnapshot === "function") applyUsageSnapshot(value);
  if (lingView.live) renderLingItems(true);
  renderLingTasks();
  renderLingInputs();
  renderLingRequests();
  renderLingStatus();
}

async function pollLing() {
  if (!lingView.active || document.hidden) return;
  if (lingView.polling) {
    lingView.refreshPending = true;
    return;
  }
  lingView.polling = true;
  try {
    lingView.connectionError = "";
    const value = await api("/api/ling");
    if (lingView.active) renderLingSnapshot(value);
  } catch (error) {
    if (lingView.active) {
      lingView.connectionError = error.message;
      $("ling-state-text").textContent = "连接暂时中断，稍后自动重试";
    }
  } finally {
    lingView.polling = false;
    if (lingView.refreshPending && lingView.active && !document.hidden) {
      lingView.refreshPending = false;
      pollLing();
    }
  }
}

async function openLing(options) {
  const settings = options || {};
  lingView.active = true;
  lingView.live = null;
  lingView.liveRevision = 0;
  lingView.renderedTargetsSignature = "";
  lingView.renderedRequestsSignature = "";
  if (typeof taskView !== "undefined" && taskView.current) {
    taskView.current = null;
    taskView.request = (taskView.request || 0) + 1;
    if (taskView.timer) clearTimeout(taskView.timer);
    taskView.timer = null;
    $("task-panel").hidden = true;
  }
  lingView.itemsSignature = "";
  rememberCurrentScroll();
  state.scrollKey = "ling";
  const viewVersion = state.viewVersion = (state.viewVersion || 0) + 1;
  if (typeof taskView !== "undefined") renderTasks();
  $("title").textContent = "灵";
  $("scroller").hidden = false;
  $("composer").hidden = false;
  showLingChrome(true);
  renderLingInputs();
  renderLingRequests();
  renderLingStatus();
  $("transcript").innerHTML = "";
  $("transcript").classList.add("empty");
  $("main").classList.add("empty");
  state.scrollAtBottom = state.scrollAtBottom || {};
  state.scrollAtBottom.ling = true;
  if (!settings.keepTranscript) {
    const hello = el("div", "welcome ling-welcome");
    hello.appendChild(el("div", "welcome-title", "灵"));
    hello.appendChild(el("div", "welcome-sub", "你可以在这里继续交流。复杂工作会独立推进，需要你决定的请求会出现在上方。"));
    $("transcript").appendChild(hello);
  }
  await pollLing();
  if (viewVersion !== state.viewVersion || !lingView.active) return;
  // Restore once the transcript is present.
  restoreViewScroll("ling");
  if (typeof connectRuntimeStream === "function") connectRuntimeStream();
  $("input").focus();
}

async function submitLingMessage(text) {
  const viewVersion = state.viewVersion;
  const isCurrentView = () => lingView.active && viewVersion === state.viewVersion;
  if (lingView.submitting || state.preparing) return false;
  if (!lingView.sessionId) await pollLing();
  if (!isCurrentView()) return false;
  if (!lingView.sessionId) {
    setStatus("灵还没有准备好，请稍后重试", true);
    return false;
  }
  const sessionId = lingView.sessionId;
  const submittedDraft = $("input").value;
  // Leaving and reopening Ling creates a new view version for the same
  // conversation. A pending send must still update that conversation when it
  // settles, while a different view must remain untouched.
  const isCurrentLing = () => lingView.active && lingView.sessionId === sessionId;
  const sentItems = pendingImages.slice();
  const sentFiles = pendingFiles.slice();
  const images = sentItems.map((item) => ({ mime: item.mime, data: item.data }));
  const target = $("composer-task") ? $("composer-task").value : "";
  if (!target && !text && !images.length && !sentFiles.length) {
    lingView.submitting = false;
    state.preparing = false;
    return false;
  }
  lingView.submitting = true;
  state.preparing = true;
  const visibleItems = lingView.items.slice();
  const visibleSignature = lingView.itemsSignature;
  const visibleInputs = lingView.inputs.slice();
  const visibleQueue = lingView.queueCount;
  let localId = "";
  let localInput = null;
  if (text || images.length) {
    localId = lingRequestId("local");
    appendLingMessages(text, images, [], localId);
  }
  try {
    const attachments = await uploadFiles("sessions", lingView.sessionId, sentFiles);
    const requestId = lingRequestId("input");
    const result = await post("/api/ling/input", {
      text,
      images,
      attachments: attachments.map((file) => file.id),
      request_id: requestId,
      task_id: target || undefined,
    });
    pendingImages = pendingImages.filter((item) => !sentItems.includes(item));
    pendingFiles = pendingFiles.filter((item) => !sentFiles.includes(item));
    renderAttachments();
    lingView.sentRequests.set(requestId, result.input_id);
    if (result.session_id) lingView.sessionId = result.session_id;
    const queued = {
      id: result.input_id || requestId,
      status: "pending",
      text: text || (attachments.length ? "附件" : ""),
      error: "",
    };
    localInput = queued;
    if (!lingView.inputs.some((item) => item.id === queued.id)) lingView.inputs.push(queued);
    lingView.queueCount += 1;
    if (target) {
      $("composer-task").value = "";
    } else if (text || images.length || attachments.length) {
      const optimistic = lingView.items.find((item) => item.client_request_id === localId);
      if (optimistic) optimistic.attachments = attachments;
      lingView.itemsSignature = JSON.stringify(lingView.items);
      if (isCurrentLing()) {
        state.items = lingView.items;
        renderTranscript();
      }
    }
    if (isCurrentLing()) {
      if (text) {
        const input = $("input");
        // Keep text typed while the request was in flight.
        if (input.value === submittedDraft) {
          input.value = "";
          input.style.height = "auto";
        }
      }
      renderLingInputs();
      renderLingStatus();
      const task = lingView.tasks.find((item) => item.id === target);
      setStatus(target
        ? "已交给「" + (task?.title || "这项工作") + "」，可以继续输入"
        : "已交给灵，可以继续输入");
    }
    await pollLing();
    return true;
  } catch (error) {
    lingView.items = visibleItems;
    lingView.itemsSignature = visibleSignature;
    if (localInput) lingView.inputs = visibleInputs;
    if (localInput) lingView.queueCount = visibleQueue;
    if (isCurrentLing()) {
      state.items = visibleItems;
      state.turns = state.items.filter((item) => item.role === "user").length;
      state.tokens = state.usage.total || 0;
      renderTranscript();
      renderLingInputs();
      renderLingStatus();
      if (!$("input").value) $("input").value = text;
      if (target && $("composer-task")) $("composer-task").value = target;
      setStatus(error.message, true);
    }
    return false;
  } finally {
    lingView.submitting = false;
    state.preparing = false;
  }
}

function appendLingMessages(text, images, attachments, clientId) {
  const optimistic = {
    role: "user",
    text,
    images,
    attachments,
    at: new Date().toISOString(),
  };
  if (clientId) optimistic.client_request_id = clientId;
  lingView.items = [...lingView.items, optimistic];
  lingView.itemsSignature = JSON.stringify(lingView.items);
  state.items = lingView.items;
  state.sessionId = lingView.sessionId;
  $("transcript").classList.remove("empty");
  $("main").classList.remove("empty");
  renderTranscript();
}

async function stopLing() {
  if (!lingView.running) return;
  setStatus("正在停止灵的当前输入…");
  $("ling-stop").disabled = true;
  try {
    await post("/api/ling/stop", {});
    await pollLing();
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("ling-stop").disabled = false;
  }
}

async function resumeLing() {
  setStatus("正在继续待恢复输入…");
  try {
    await post("/api/ling/resume", {});
    await pollLing();
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function pauseAllWorks() {
  setStatus("正在挂起全部工作…");
  $("ling-pause-all").disabled = true;
  try {
    await post("/api/ling/pause-all", {});
    await pollLing();
    setStatus("已请求挂起全部工作");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("ling-pause-all").disabled = false;
  }
}

function requestKey(request) {
  return String((request && (request.token || request.id)) || "");
}

function uniqueMainRequests(values) {
  const seen = new Set();
  const answered = lingView.requestAnswered || new Map();
  return (values || []).filter((request) => {
    if (!request || !["pending", "unknown"].includes(request.status || "pending")) return false;
    const key = request ? requestKey(request) : "";
    if (!key || seen.has(key)) return false;
    const at = answered.get(key);
    if (at && Date.now() - at < 10000) return false;
    seen.add(key);
    return true;
  });
}

function requestPayload(request) {
  return request && request.payload && typeof request.payload === "object" ? request.payload : (request || {});
}

function requestDraft(key) {
  const id = String(key || "");
  if (!id) return { answers: {} };
  if (!lingView.requestDrafts.has(id)) lingView.requestDrafts.set(id, { answers: {} });
  return lingView.requestDrafts.get(id);
}

function workRequestDraft(key) {
  const id = String(key || "");
  if (!id) return { answers: {} };
  lingView.workRequestDrafts = lingView.workRequestDrafts || new Map();
  if (!lingView.workRequestDrafts.has(id)) lingView.workRequestDrafts.set(id, { answers: {} });
  return lingView.workRequestDrafts.get(id);
}

function requestBusy(key) {
  return Boolean(lingView.requestBusy && lingView.requestBusy.has(String(key || "")));
}

function renderRequestQuestions(panel, request, key, busy) {
  const payload = requestPayload(request);
  const questions = Array.isArray(payload.questions) ? payload.questions : [];
  const draft = requestDraft(key);
  questions.forEach((question, index) => {
    const block = el("div", "ling-question-block");
    block.appendChild(el("p", "ling-question-text", question.question || question.text || "需要你的回答"));
    const choices = el("div", "ling-question-choices");
    for (const choice of question.options || []) {
      const button = el("button", "ling-question-choice", String(choice));
      button.type = "button";
      button.disabled = busy;
      button.addEventListener("click", () => {
        draft.answers[index] = String(choice);
        const input = block.querySelector(".ling-question-free");
        if (input) input.value = String(choice);
      });
      choices.appendChild(button);
    }
    if (choices.children.length) block.appendChild(choices);
    const input = el("input", "ling-question-free");
    input.type = "text";
    input.placeholder = "输入回答";
    input.value = draft.answers[index] || "";
    input.disabled = busy;
    input.addEventListener("input", () => { draft.answers[index] = input.value; });
    block.appendChild(input);
    panel.appendChild(block);
  });
  const submit = el("button", "primary ling-request-submit", "提交回答并继续");
  submit.type = "button";
  submit.disabled = busy;
  submit.addEventListener("click", () => {
    const answers = questions.map((_, index) => String(draft.answers[index] || ""));
    if (answers.some((answer) => !answer.trim())) {
      setStatus("请回答所有问题", true);
      return;
    }
    answerMainRequest(request, answers).catch((error) => setStatus(error.message, true));
  });
  panel.appendChild(submit);
}

function renderMainRequest(box, request) {
  const key = requestKey(request);
  const busy = requestBusy(key);
  const approval = request.kind === "approval";
  const panel = el("article", "ling-request" + (busy ? " submitting" : ""));
  panel.dataset.requestKey = key;
  const head = el("div", "ling-request-head");
  head.appendChild(el("span", "ling-request-owner", "灵"));
  head.appendChild(el("span", "ling-request-title", approval ? "需要你确认" : "需要你回答"));
  if (request.status === "unknown") {
    head.appendChild(el("span", "ling-request-state", "结果待核对"));
  }
  panel.appendChild(head);
  const payload = requestPayload(request);
  if (approval) {
    panel.appendChild(el("div", "ling-interaction-tool", String(payload.tool || payload.value || "请求确认")));
    if (payload.arguments && Object.keys(payload.arguments).length) {
      panel.appendChild(el("pre", "ling-interaction-detail", JSON.stringify(payload.arguments, null, 2)));
    }
    if (request.status === "unknown") {
      panel.appendChild(el("div", "ling-request-message",
        "这次操作的结果尚未确认。系统不会自动重放或重复授权；请先核对实际影响。"));
      const reviewed = el("button", "ling-request-reviewed", "我已核对，关闭提示");
      reviewed.type = "button";
      reviewed.disabled = busy;
      reviewed.addEventListener("click", () => reviewUnknownRequest(request)
        .catch((error) => setStatus(error.message, true)));
      panel.appendChild(reviewed);
      box.appendChild(panel);
      return;
    }
    const choices = el("div", "ling-approval-choices");
    const deny = el("button", "ling-approval-deny", "拒绝");
    deny.type = "button";
    deny.disabled = busy;
    deny.addEventListener("click", () => answerMainRequest(request, false)
      .catch((error) => setStatus(error.message, true)));
    const allow = el("button", "ling-approval-allow", "允许");
    allow.type = "button";
    allow.disabled = busy;
    allow.addEventListener("click", () => answerMainRequest(request, true)
      .catch((error) => setStatus(error.message, true)));
    choices.appendChild(deny);
    choices.appendChild(allow);
    panel.appendChild(choices);
  } else {
    renderRequestQuestions(panel, request, key, busy);
  }
  box.appendChild(panel);
}

function renderWorkRequest(box, task, interaction) {
  const key = "task:" + task.id + ":" + interaction.id;
  const busy = requestBusy(key);
  const payload = interaction.payload || {};
  const approval = interaction.kind === "approval";
  const panel = el("article", "ling-request work" + (busy ? " submitting" : ""));
  panel.dataset.requestKey = key;
  const head = el("div", "ling-request-head");
  head.appendChild(el("span", "ling-request-owner", task.title || "这项工作"));
  head.appendChild(el("span", "ling-request-title", approval ? "需要你确认" : "需要你回答"));
  panel.appendChild(head);
  if (approval) {
    panel.appendChild(el("div", "ling-interaction-tool", String(payload.tool || "请求确认")));
    if (payload.arguments && Object.keys(payload.arguments).length) {
      panel.appendChild(el("pre", "ling-interaction-detail", JSON.stringify(payload.arguments, null, 2)));
    }
    const choices = el("div", "ling-approval-choices");
    const deny = el("button", "ling-approval-deny", "拒绝");
    deny.type = "button";
    deny.disabled = busy;
    deny.addEventListener("click", () => answerWorkRequest(task, interaction, false)
      .catch((error) => setStatus(error.message, true)));
    const allow = el("button", "ling-approval-allow", "允许");
    allow.type = "button";
    allow.disabled = busy;
    allow.addEventListener("click", () => answerWorkRequest(task, interaction, true)
      .catch((error) => setStatus(error.message, true)));
    choices.appendChild(deny);
    choices.appendChild(allow);
    panel.appendChild(choices);
  } else {
    renderWorkQuestions(panel, task, interaction, key, busy);
  }
  const open = el("button", "ling-request-open", "查看工作");
  open.type = "button";
  open.addEventListener("click", () => openLingTask(task.id));
  panel.appendChild(open);
  box.appendChild(panel);
}

function renderWorkQuestions(panel, task, interaction, key, busy) {
  const questions = interaction.payload && Array.isArray(interaction.payload.questions)
    ? interaction.payload.questions : [];
  const draft = workRequestDraft(key);
  questions.forEach((question, index) => {
    const block = el("div", "ling-question-block");
    block.appendChild(el("p", "ling-question-text", question.question || question.text || "需要你的回答"));
    const choices = el("div", "ling-question-choices");
    for (const choice of question.options || []) {
      const button = el("button", "ling-question-choice", String(choice));
      button.type = "button";
      button.disabled = busy;
      button.addEventListener("click", () => {
        draft.answers[index] = String(choice);
        const input = block.querySelector(".ling-question-free");
        if (input) input.value = String(choice);
      });
      choices.appendChild(button);
    }
    if (choices.children.length) block.appendChild(choices);
    const input = el("input", "ling-question-free");
    input.type = "text";
    input.placeholder = "输入回答";
    input.value = draft.answers[index] || "";
    input.disabled = busy;
    input.addEventListener("input", () => { draft.answers[index] = input.value; });
    block.appendChild(input);
    panel.appendChild(block);
  });
  const submit = el("button", "primary ling-request-submit", "提交回答并继续");
  submit.type = "button";
  submit.disabled = busy;
  submit.addEventListener("click", () => {
    const answers = questions.map((_, index) => String(draft.answers[index] || ""));
    if (answers.some((answer) => !answer.trim())) {
      setStatus("请回答所有问题", true);
      return;
    }
    answerWorkRequest(task, interaction, answers).catch((error) => setStatus(error.message, true));
  });
  panel.appendChild(submit);
}

function visibleWorkRequests() {
  const values = [];
  const answered = lingView.workRequestAnswered || new Map();
  for (const task of lingView.tasks || []) {
    for (const interaction of task.pending_interactions || []) {
      if (!interaction || !interaction.id) continue;
      const key = "task:" + task.id + ":" + interaction.id;
      const at = answered.get(key);
      if (at && Date.now() - at < 10000) continue;
      values.push({ task, interaction });
    }
  }
  for (const [key, at] of [...answered]) {
    if (Date.now() - at > 10000) answered.delete(key);
  }
  return values;
}

function renderLingRequests() {
  const box = $("ling-requests");
  if (!box) return;
  const requests = uniqueMainRequests(lingView.requests);
  const works = visibleWorkRequests();
  const signature = JSON.stringify({requests, works});
  if (signature === lingView.renderedRequestsSignature) return;
  lingView.renderedRequestsSignature = signature;
  box.innerHTML = "";
  box.hidden = !lingView.active || (!requests.length && !works.length);
  if (box.hidden) return;
  if (requests.length) {
    const section = el("section", "ling-request-section");
    section.appendChild(el("div", "ling-request-kicker", "灵在等你"));
    for (const request of requests) renderMainRequest(section, request);
    box.appendChild(section);
  }
  if (works.length) {
    const section = el("section", "ling-request-section work");
    section.appendChild(el("div", "ling-request-kicker", "工作请求"));
    for (const value of works) renderWorkRequest(section, value.task, value.interaction);
    box.appendChild(section);
  }
}

async function answerMainRequest(request, value) {
  const key = requestKey(request);
  if (!key) return;
  lingView.requestBusy = lingView.requestBusy || new Set();
  lingView.requestBusy.add(key);
  lingView.renderedRequestsSignature = "";
  renderLingRequests();
  try {
    if (request.kind === "approval") {
      await post("/api/approval", { token: request.token || request.id, allowed: value === true });
    } else {
      await post("/api/answer", { token: request.token || request.id, answers: Array.isArray(value) ? value : [String(value)] });
    }
    lingView.requestAnswered = lingView.requestAnswered || new Map();
    lingView.requestAnswered.set(key, Date.now());
    await pollLing();
  } finally {
    lingView.requestBusy.delete(key);
    lingView.renderedRequestsSignature = "";
    renderLingRequests();
  }
}

async function reviewUnknownRequest(request) {
  const key = requestKey(request);
  if (!key) return;
  lingView.requestBusy = lingView.requestBusy || new Set();
  lingView.requestBusy.add(key);
  lingView.renderedRequestsSignature = "";
  renderLingRequests();
  try {
    await post("/api/approval", { token: request.token || request.id, reviewed_unknown: true });
    lingView.requestAnswered = lingView.requestAnswered || new Map();
    lingView.requestAnswered.set(key, Date.now());
    await pollLing();
  } finally {
    lingView.requestBusy.delete(key);
    lingView.renderedRequestsSignature = "";
    renderLingRequests();
  }
}

async function answerWorkRequest(task, interaction, value) {
  const key = "task:" + task.id + ":" + interaction.id;
  lingView.requestBusy = lingView.requestBusy || new Set();
  lingView.requestBusy.add(key);
  lingView.renderedRequestsSignature = "";
  renderLingRequests();
  try {
    const answers = Array.isArray(value) ? value : [String(value)];
    await post("/api/tasks/" + encodeURIComponent(task.id) + "/interactions/" +
      encodeURIComponent(interaction.id) + "/answer", {
        answer: interaction.kind === "approval" ? value === true
          : (answers.length === 1 ? answers[0] : answers),
        resume: true,
        expected_version: interaction.control_version ?? task.control_version,
      });
    lingView.workRequestDrafts.delete(key);
    lingView.workRequestAnswered = lingView.workRequestAnswered || new Map();
    lingView.workRequestAnswered.set(key, Date.now());
    await pollLing();
    if (typeof taskView !== "undefined" && taskView.current?.id === task.id) {
      await openTask(task.id, {refresh: true});
    }
  } finally {
    lingView.requestBusy.delete(key);
    lingView.renderedRequestsSignature = "";
    renderLingRequests();
  }
}

async function openLingTask(id) {
  if (typeof openTask !== "function") return;
  clearLingView();
  await openTask(id);
}

function receiveRuntimeEvent(event) {
  if (!event || typeof event !== "object") return;
  const ownerId = String(event.owner_id || "");
  if (event.scope === "ling") {
    if (ownerId && ownerId !== lingView.sessionId) return;
    if (!["delta", "thinking", "tool_start", "tool_result", "context", "error", "done", "refresh"].includes(event.type)) {
      return;
    }
    lingView.connectionError = "";
    const changed = setLingLive(event);
    if (changed && ["delta", "thinking", "tool_start", "tool_result", "error", "done", "refresh"].includes(event.type)) {
      renderLingItems(true);
    }
    if (event.type === "refresh" || event.type === "done") {
      if (lingView.active && !document.hidden) pollLing();
    }
    renderLingStatus();
    return;
  }
  if (event.scope === "task" && typeof taskRuntimeEvent === "function") {
    taskRuntimeEvent(event);
  }
}

function initLing() {
  if (lingView.initialized) return;
  lingView.initialized = true;
  $("ling-home").addEventListener("click", () => openLing().catch((error) => setStatus(error.message, true)));
  $("ling-stop").addEventListener("click", stopLing);
  $("ling-resume").addEventListener("click", resumeLing);
  $("ling-pause-all").addEventListener("click", pauseAllWorks);
  $("composer-task").addEventListener("change", () => {
    const task = lingView.tasks.find((item) => item.id === $("composer-task").value);
    $("input").placeholder = task
      ? "给「" + (task.title || "这项工作") + "」补充要求"
      : "和灵说话，或交代一件工作";
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      if (lingView.active) pollLing();
      if (typeof taskView !== "undefined" && taskView.current) {
        openTask(taskView.current.id, {refresh: true}).catch((error) => setStatus(error.message, true));
      }
    }
  });
}

function startLing() {
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initLing, { once: true });
    return;
  }
  initLing();
}

startLing();
