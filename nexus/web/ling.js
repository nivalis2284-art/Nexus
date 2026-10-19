"use strict";

// The continuous entry point. The server owns the Ling session and its input
// queue; this module renders snapshots and submits intent. It never starts a
// second request stream, and polling cannot replace a draft or the legacy view.

const lingView = {
  active: false,
  sessionId: null,
  items: [],
  itemsSignature: "",
  running: false,
  queueCount: 0,
  inputs: [],
  tasks: [],
  notifications: [],
  unreadCount: 0,
  interaction: null,
  interactionDraft: null,
  interactionHandled: false,
  notificationDraft: null,
  pollTimer: null,
  polling: false,
  submitting: false,
  requests: new Map(),
  notificationAfter: 0,
  notificationSeen: new Set(),
  notificationHydrated: false,
  notificationsOpen: false,
  notificationsDismissed: new Set(),
  autoOpened: new Set(),
  notificationTimer: null,
  initialized: false,
  renderedTaskState: "",
};

function lingRequestId(prefix) {
  return (prefix || "ling") + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

function showLingChrome(show) {
  $("ling-bar").hidden = !show;
  $("ling-mode-label").hidden = !show;
  $("ling-stop").hidden = !show || !lingView.running;
  $("ling-inputs").hidden = !show || !lingView.inputs.length;
  $("ling-interaction").hidden = !show || !lingView.interaction;
}

function clearLingView() {
  lingView.active = false;
  if (lingView.pollTimer) clearTimeout(lingView.pollTimer);
  lingView.pollTimer = null;
  showLingChrome(false);
}

function updateNotificationBadge() {
  $("notifications-toggle").classList.toggle("has-unread", lingView.unreadCount > 0);
  $("notification-count").textContent = String(lingView.unreadCount || 0);
  $("notification-count").hidden = !lingView.unreadCount;
}

function renderLingStatus() {
  if (!lingView.active) {
    updateNotificationBadge();
    return;
  }
  if (lingView.connectionError) {
    $("ling-state-text").textContent = "连接暂时中断，稍后自动重试";
    updateNotificationBadge();
    $("ling-stop").hidden = !lingView.running;
    return;
  }
  const stateText = $("ling-state-text");
  if (lingView.running && lingView.interaction) stateText.textContent = "灵在等待你处理请求";
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
  updateNotificationBadge();
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
  const box = $("ling-tasks");
  const nextState = JSON.stringify(lingView.tasks);
  if (nextState === lingView.renderedTaskState) return;
  lingView.renderedTaskState = nextState;
  box.innerHTML = "";
  for (const task of lingView.tasks.slice(0, 4)) {
    const button = el("button", "ling-task", task.title || "未命名任务");
    button.type = "button";
    button.title = (task.goal || task.title || "") + " · 打开任务详情";
    const pending = Array.isArray(task.pending_interactions)
      ? task.pending_interactions.length
      : Number(task.pending_interactions || 0);
    button.appendChild(el("span", "ling-task-state", lingTaskLabel(task.state) +
      (pending > 0 ? " · 待处理" : "")));
    button.addEventListener("click", () => openLingTask(task.id));
    box.appendChild(button);
  }
  if (lingView.tasks.length) {
    const all = el("button", "ling-task-all", "全部任务");
    all.type = "button";
    all.addEventListener("click", () => { document.querySelector(".side-group:nth-of-type(2)")?.scrollIntoView(); });
    box.appendChild(all);
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

function renderLingSnapshot(value) {
  if (!lingView.active) return;
  if (lingView.sessionId && value.session_id && value.session_id !== lingView.sessionId) return;
  if (lingView.submitting) {
    value = {...value, items: lingView.items};
  }
  if (!lingView.sessionId && value.session_id) lingView.sessionId = value.session_id;
  lingView.running = Boolean(value.running);
  lingView.queueCount = Number(value.queue_count || 0);
  lingView.inputs = Array.isArray(value.inputs) ? value.inputs : [];
  lingView.tasks = Array.isArray(value.tasks) ? value.tasks : [];
  if (typeof value.unread_count === "number") lingView.unreadCount = value.unread_count;

  const signature = JSON.stringify(value.items || []);
  if (signature !== lingView.itemsSignature) {
    lingView.itemsSignature = signature;
    lingView.items = Array.isArray(value.items) ? value.items : [];
    state.sessionId = lingView.sessionId;
    state.items = lingView.items;
    state.turns = state.items.filter((item) => item.role === "user").length;
    state.tokens = state.items.reduce((sum, item) => {
      const usage = item.usage || {};
      return sum + (usage.input_tokens || 0) + (usage.output_tokens || 0);
    }, 0);
    if (!taskView.current) {
      $("task-panel").hidden = true;
      renderTranscript();
    }
  }
  renderLingTasks();
  renderLingInputs();
  const interaction = value.interaction || null;
  if (interaction) {
    if (interaction.task_id === undefined && interaction.taskId !== undefined) interaction.task_id = interaction.taskId;
    if (interaction.type === "approval" || interaction.kind === "approval") interaction.kind = "approval";
    if (interaction.payload && typeof interaction.payload === "object") {
      if (!interaction.tool && interaction.payload.tool) interaction.tool = interaction.payload.tool;
      if (!interaction.arguments && interaction.payload.arguments) interaction.arguments = interaction.payload.arguments;
      if (!interaction.questions && interaction.payload.questions) interaction.questions = interaction.payload.questions;
    }
    if (interaction.type === "question" || interaction.kind === "question") interaction.kind = "question";
  }
  renderLingInteraction(interaction);
  const merged = mergeNotifications(value.notifications || []);
  maybeAutoOpenNotifications(merged.freshUnread);
  renderNotifications();
  renderLingStatus();
}

async function pollLing() {
  if (!lingView.active || lingView.polling || document.hidden) return;
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
  }
}

function armLingPolling() {
  if (lingView.pollTimer) clearTimeout(lingView.pollTimer);
  lingView.pollTimer = setTimeout(async () => {
    lingView.pollTimer = null;
    if (lingView.active && !document.hidden) await pollLing();
    if (lingView.active) armLingPolling();
  }, 1000);
}

async function openLing(options) {
  const settings = options || {};
  lingView.active = true;
  lingView.itemsSignature = "";
  state.viewVersion = (state.viewVersion || 0) + 1;
  if (typeof taskView !== "undefined") {
    taskView.current = null;
    if (taskView.timer) clearTimeout(taskView.timer);
    taskView.timer = null;
    $("task-panel").hidden = true;
    renderTasks();
  }
  $("title").textContent = "灵";
  showLingChrome(true);
  renderLingInputs();
  renderLingInteraction(lingView.interaction);
  renderLingStatus();
  $("transcript").innerHTML = "";
  $("transcript").classList.add("empty");
  $("main").classList.add("empty");
  if (!settings.keepTranscript) {
    const hello = el("div", "welcome ling-welcome");
    hello.appendChild(el("div", "welcome-title", "灵"));
    hello.appendChild(el("div", "welcome-sub", "你可以在这里继续交流。复杂工作会独立推进，重要进展会出现在通知里。"));
    $("transcript").appendChild(hello);
  }
  await pollLing();
  if (!settings.keepTranscript && lingView.itemsSignature === "[]") {
    const hello = el("div", "welcome ling-welcome");
    hello.appendChild(el("div", "welcome-title", "灵"));
    hello.appendChild(el("div", "welcome-sub", "你可以在这里继续交流。复杂工作会独立推进，重要进展会出现在通知里。"));
    $("transcript").appendChild(hello);
  }
  armLingPolling();
  await pollNotifications({ startup: true });
  armNotificationPolling();
  $("input").focus();
}

async function submitLingMessage(text) {
  if (lingView.submitting || state.preparing) return false;
  if (!lingView.sessionId) await pollLing();
  if (!lingView.sessionId) {
    setStatus("灵还没有准备好，请稍后重试", true);
    return false;
  }
  const sentItems = pendingImages.slice();
  const sentFiles = pendingFiles.slice();
  const images = sentItems.map((item) => ({ mime: item.mime, data: item.data }));
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
    $("input").value = "";
    $("input").style.height = "auto";
  }
  try {
    const attachments = await uploadFiles("sessions", lingView.sessionId, sentFiles);
    const requestId = lingRequestId("input");
    const result = await post("/api/ling/input", {
      text,
      images,
      attachments: attachments.map((file) => file.id),
      request_id: requestId,
    });
    pendingImages = pendingImages.filter((item) => !sentItems.includes(item));
    pendingFiles = pendingFiles.filter((item) => !sentFiles.includes(item));
    renderAttachments();
    lingView.requests.set(requestId, result.input_id);
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
    if (text || images.length || attachments.length) {
      const optimistic = lingView.items.find((item) => item.client_request_id === localId);
      if (optimistic) optimistic.attachments = attachments;
      lingView.itemsSignature = JSON.stringify(lingView.items);
      state.items = lingView.items;
      renderTranscript();
    }
    renderLingInputs();
    renderLingStatus();
    setStatus("已交给灵，可以继续输入");
    await pollLing();
    return true;
  } catch (error) {
    lingView.items = visibleItems;
    lingView.itemsSignature = visibleSignature;
    state.items = visibleItems;
    if (localInput) lingView.inputs = visibleInputs;
    if (localInput) lingView.queueCount = visibleQueue;
    renderTranscript();
    renderLingInputs();
    renderLingStatus();
    if (!$("input").value) $("input").value = text;
    setStatus(error.message, true);
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

function interactionKey(interaction) {
  if (!interaction) return "";
  return JSON.stringify([interaction.type, interaction.token, interaction.task_id,
    interaction.interaction_id]);
}

function interactionVersion(interaction) {
  return Number(interaction && (interaction.control_version || interaction.version)) || 0;
}

function readInteractionDraft(box) {
  const draft = { answers: {}, approval: "" };
  for (const block of box.querySelectorAll(".ling-question-block")) {
    const input = block.querySelector(".ling-question-free");
    draft.answers[block.dataset.index] = input ? input.value : "";
  }
  const approval = box.querySelector(".ling-approval-answer");
  if (approval) draft.approval = approval.value;
  return draft;
}

function renderLingInteraction(interaction) {
  const box = $("ling-interaction");
  const key = interactionKey(interaction);
  if (key === lingView.interactionDraft?.key && box.children.length) {
    lingView.interaction = interaction;
    const current = readInteractionDraft(box);
    const hasText = Boolean(current.approval) ||
      Object.values(current.answers || {}).some((answer) => String(answer || "").trim());
    if (hasText) lingView.interactionDraft.value = current;
    return;
  }
  if (!key && lingView.interactionHandled && box.children.length) return;
  if (key) lingView.interactionHandled = false;
  box.innerHTML = "";
  lingView.interaction = interaction;
  lingView.interactionDraft = null;
  box.hidden = !lingView.active || !interaction;
  if (!interaction) return;
  const draft = { key, value: { answers: {}, approval: "" } };
  lingView.interactionDraft = draft;
  box.appendChild(el("div", "ling-interaction-label", "灵在等待你处理"));
  if (interaction.type === "approval" || interaction.kind === "approval") {
    box.appendChild(el("div", "ling-interaction-tool", String(interaction.tool || interaction.value || "需要确认")));
    const detail = el("pre", "ling-interaction-detail", JSON.stringify(interaction.arguments || {}, null, 2));
    box.appendChild(detail);
    const input = el("input", "ling-approval-answer");
    input.type = "text";
    input.placeholder = "允许 / 拒绝，或说明";
    const submit = el("button", "primary", "提交回答");
    submit.type = "button";
    submit.addEventListener("click", () => answerLingInteraction(input.value));
    input.addEventListener("input", () => { draft.value.approval = input.value; });
    box.appendChild(input);
    box.appendChild(submit);
    input.focus();
  } else {
    const questions = interaction.questions || [];
    questions.forEach((item, index) => {
      const block = el("div", "ling-question-block");
      block.dataset.index = String(index);
      block.appendChild(el("p", "ling-question-text", item.question || item.text || ""));
      const choices = el("div", "ling-question-choices");
      for (const choice of item.options || []) {
        const button = el("button", "ling-question-choice", choice);
        button.type = "button";
        button.addEventListener("click", () => {
          const input = block.querySelector(".ling-question-free");
          if (input) input.value = choice;
          draft.value.answers[index] = choice;
        });
        choices.appendChild(button);
      }
      block.appendChild(choices);
      const input = el("input", "ling-question-free");
      input.type = "text";
      input.placeholder = "或输入自定义回答";
      input.addEventListener("input", () => { draft.value.answers[index] = input.value; });
      block.appendChild(input);
      box.appendChild(block);
      if (index === 0) input.focus();
    });
    const submit = el("button", "primary", "提交回答");
    submit.type = "button";
    submit.addEventListener("click", () => {
      const answers = questions.map((_, index) => draft.value.answers[index] || "");
      answerLingInteraction(answers);
    });
    box.appendChild(submit);
  }
}

async function answerLingInteraction(value) {
  const interaction = lingView.interaction;
  if (!interaction) return;
  const box = $("ling-interaction");
  box.classList.add("submitting");
  try {
    if (interaction.task_id) {
      const answers = Array.isArray(value) ? value : [String(value)];
      await post("/api/tasks/" + encodeURIComponent(interaction.task_id) + "/interactions/" +
        encodeURIComponent(interaction.interaction_id) + "/answer", {
          answer: interaction.kind === "approval"
            ? /^(allow|yes|允许|同意)$/i.test(String(value).trim())
            : (answers.length === 1 ? answers[0] : answers),
          resume: true,
          expected_version: interaction.control_version,
        });
    } else if (interaction.type === "approval" || interaction.kind === "approval") {
      const allowed = /^(allow|yes|允许|同意)$/i.test(String(value).trim());
      await post("/api/approval", { token: interaction.token, allowed });
    } else {
      await post("/api/answer", { token: interaction.token, text: value });
    }
    lingView.interactionDraft = null;
    lingView.interaction = null;
    box.innerHTML = "";
    box.appendChild(el("div", "ling-interaction-expired", "已处理，任务正在继续"));
    lingView.interactionHandled = true;
    await pollLing();
  } catch (error) {
    if (/not found|resolved|expired|409|404/i.test(error.message)) {
      lingView.interactionDraft = null;
      lingView.interaction = null;
      box.innerHTML = "";
      box.appendChild(el("div", "ling-interaction-expired", "这项交互已处理或已过期，当前状态已刷新。"));
    } else {
      setStatus(error.message, true);
    }
  } finally {
    box.classList.remove("submitting");
  }
}

function notificationId(item) {
  return item && item.id !== undefined && item.id !== null ? String(item.id) : "";
}

function notificationCursor(item) {
  return Number(item && (item.id || item.cursor || item.seq)) || 0;
}

function mergeNotifications(items) {
  let freshUnread = [];
  let changed = false;
  for (const item of items || []) {
    const id = notificationId(item);
    const cursor = notificationCursor(item);
    const existing = id ? lingView.notifications.find((old) => notificationId(old) === id) : null;
    if (existing) {
      const wasUnread = !existing.read_at;
      const before = JSON.stringify(existing);
      Object.assign(existing, item);
      if (before !== JSON.stringify(existing)) changed = true;
      if (!wasUnread && !item.read_at) freshUnread.push(item);
    } else if (id) {
      lingView.notificationSeen.add(id);
      lingView.notifications.push(item);
      changed = true;
      if (!item.read_at) freshUnread.push(item);
    } else if (!id && cursor && !lingView.notifications.some((old) => notificationCursor(old) === cursor)) {
      lingView.notifications.push(item);
      changed = true;
      if (!item.read_at) freshUnread.push(item);
    }
    if (cursor > lingView.notificationAfter) lingView.notificationAfter = cursor;
  }
  const limit = 100;
  if (lingView.notifications.length > limit) lingView.notifications = lingView.notifications.slice(-limit);
  return {freshUnread, changed};
}

async function pollNotifications(options) {
  const settings = options || {};
  const hydrate = settings.startup || !lingView.notificationHydrated;
  const after = hydrate ? 0 : lingView.notificationAfter;
  try {
    const hadBadge = lingView.unreadCount > 0;
    const value = await api("/api/notifications?after=" + encodeURIComponent(after) +
      (hydrate ? "&unread=0" : "") + "&limit=100");
    const merged = mergeNotifications(value.notifications || []);
    if (typeof value.unread_count === "number") lingView.unreadCount = value.unread_count;
    if (Number.isFinite(Number(value.next))) lingView.notificationAfter = Number(value.next);
    lingView.notificationHydrated = true;
    const caughtUp = !hadBadge && settings.startup && lingView.unreadCount > 0 && !merged.changed;
    maybeAutoOpenNotifications(caughtUp ? lingView.notifications.filter((item) => !item.read_at) : merged.freshUnread);
    renderNotifications();
    renderLingStatus();
  } catch (error) {
    if (settings.startup) return;
    throw error;
  }
}

function syncLegacyNotifications() {
  mergeNotifications(lingView.notifications);
  renderNotifications();
  renderLingStatus();
}

function renderNotifications() {
  $("notification-summary").textContent = lingView.unreadCount
    ? lingView.unreadCount + " 条未读"
    : (lingView.notifications.length ? "全部已读" : "暂无通知");
  const list = $("notification-list");
  const draft = lingView.notificationDraft?.open ? lingView.notificationDraft : null;
  const preserved = draft ? list.querySelector(".notification-action-panel") : null;
  const nextList = el("div", "notification-list-content");
  list.innerHTML = "";
  if (!lingView.notifications.length) {
    nextList.appendChild(el("div", "notification-empty", "任务需要你、完成或受阻时，会在这里主动提示。"));
    list.appendChild(nextList);
    updateNotificationBadge();
    return;
  }
  for (const item of lingView.notifications.slice().reverse()) {
    const row = el("article", "notification-item" + (item.read_at ? " read" : " unread"));
    row.dataset.notificationId = notificationId(item);
    const head = el("div", "notification-item-head");
    head.appendChild(el("span", "notification-dot"));
    head.appendChild(el("span", "notification-title", item.title || "工作进展"));
    head.appendChild(el("span", "notification-time", clock(item.created_at)));
    row.appendChild(head);
    if (item.text) row.appendChild(el("div", "notification-text", item.text));
    const actions = el("div", "notification-item-actions");
    if (item.task_id) {
      const task = el("button", "notification-task", "查看关联任务");
      task.type = "button";
      task.addEventListener("click", () => openLingTask(item.task_id));
      actions.appendChild(task);
      if (item.interaction_id && !item.interaction_expired) {
        const handle = el("button", "notification-handle", "处理请求");
        handle.type = "button";
        handle.addEventListener("click", () => openNotificationAction(item, row, handle));
        actions.appendChild(handle);
      } else if (item.interaction_id) {
        actions.appendChild(el("span", "small", "请求已处理或已过期"));
      }
    }
    if (!item.read_at) {
      const read = el("button", "notification-read", "标为已读");
      read.type = "button";
      read.addEventListener("click", () => markNotificationRead(item));
      actions.appendChild(read);
    }
    row.appendChild(actions);
    if (preserved && draft?.key === notificationId(item)) {
      if (draft.row !== row) {
        draft.row = row;
        draft.panel = preserved;
      }
      row.appendChild(preserved);
    }
    nextList.appendChild(row);
  }
  list.appendChild(nextList);
  updateNotificationBadge();
}

async function markNotificationRead(item) {
  if (!item || !item.id || item.read_at) return;
  try {
    await post("/api/notifications/" + encodeURIComponent(item.id) + "/read", {});
    item.read_at = new Date().toISOString();
    lingView.unreadCount = Math.max(0, lingView.unreadCount - 1);
    renderNotifications();
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function markAllNotificationsRead() {
  const unread = lingView.notifications.filter((item) => !item.read_at);
  for (const item of unread) await markNotificationRead(item);
}

function setNotificationsOpen(open) {
  if (!open) {
    for (const item of lingView.notifications) {
      if (!item.read_at) lingView.notificationsDismissed.add(notificationId(item));
    }
  }
  lingView.notificationsOpen = open;
  $("notification-region").hidden = !open;
  $("notifications-toggle").setAttribute("aria-expanded", String(open));
  if (open) {
    renderNotifications();
    if (!lingView.notificationHydrated) {
      pollNotifications({ startup: true }).catch((error) => setStatus(error.message, true));
    }
  } else {
    const draft = lingView.notificationDraft;
    if (draft && draft.open && draft.status !== "submitting") lingView.notificationDraft = null;
  }
}

function maybeAutoOpenNotifications(freshItems) {
  const candidates = (freshItems || []).filter((item) => {
    const id = notificationId(item);
    return id && !lingView.notificationsDismissed.has(id) && !lingView.autoOpened.has(id);
  });
  if (!candidates.length || lingView.notificationsOpen) return;
  for (const item of candidates) lingView.autoOpened.add(notificationId(item));
  setNotificationsOpen(true);
}

function notificationInteractionPayload(interaction) {
  const payload = interaction.payload && typeof interaction.payload === "object" ? interaction.payload : interaction;
  return {
    kind: interaction.kind,
    tool: payload.tool || "",
    arguments: payload.arguments || {},
    questions: Array.isArray(payload.questions) ? payload.questions : [],
  };
}

function findPendingInteraction(detail, notification) {
  const pending = Array.isArray(detail.pending_interactions) ? detail.pending_interactions : [];
  return pending.find((item) => item.id === notification.interaction_id) || null;
}

function renderNotificationAction(panel, state) {
  panel.innerHTML = "";
  if (state.status === "loading") {
    panel.appendChild(el("div", "notification-action-message", "正在读取最新任务状态…"));
    return;
  }
  if (state.status === "submitting") {
    panel.appendChild(el("div", "notification-action-message", "正在提交处理结果…"));
    return;
  }
  if (state.status === "done") {
    panel.appendChild(el("div", "notification-action-message", "已处理，通知状态已更新。"));
    return;
  }
  if (state.status === "stale") {
    panel.appendChild(el("div", "notification-action-message", "这项请求已处理或已过期。"));
    return;
  }
  if (state.status === "error") {
    panel.appendChild(el("div", "notification-action-message error", state.error || "读取任务失败"));
    return;
  }
  const interaction = state.interaction;
  const info = notificationInteractionPayload(interaction);
  if (info.tool) {
    panel.appendChild(el("div", "notification-action-tool", "需要批准：" + info.tool));
    panel.appendChild(el("pre", "notification-action-detail", JSON.stringify(info.arguments, null, 2)));
  }
  if (info.questions.length) {
    info.questions.forEach((question, index) => {
      panel.appendChild(el("div", "notification-action-question", question.question || question.text || ""));
      if (Array.isArray(question.options) && question.options.length) {
        panel.appendChild(el("div", "notification-action-options", question.options.join(" / ")));
      }
      const answer = el("input", "notification-action-input");
      answer.type = "text";
      answer.dataset.index = String(index);
      answer.placeholder = "回答这个问题";
      answer.value = state.draft?.answers?.[index] || "";
      answer.addEventListener("input", () => {
        if (!state.draft) state.draft = { answers: {}, approval: false };
        state.draft.answers[index] = answer.value;
      });
      panel.appendChild(answer);
    });
  }
  if (interaction.kind === "approval") {
    const input = el("input", "notification-action-input notification-approval-note");
    input.type = "text";
    input.placeholder = "可选说明";
    input.value = state.draft?.note || "";
    input.addEventListener("input", () => {
      if (!state.draft) state.draft = { answers: {}, approval: false };
      state.draft.note = input.value;
    });
    panel.appendChild(input);
    const actions = el("div", "notification-action-buttons");
    const deny = el("button", "notification-deny", "拒绝");
    deny.type = "button";
    deny.addEventListener("click", () => submitNotificationAction(false));
    const allow = el("button", "notification-allow", "允许并继续");
    allow.type = "button";
    allow.addEventListener("click", () => submitNotificationAction(true));
    actions.appendChild(deny);
    actions.appendChild(allow);
    panel.appendChild(actions);
  } else if (info.questions.length) {
    const submit = el("button", "notification-allow", "提交并继续");
    submit.type = "button";
    submit.addEventListener("click", () => submitNotificationAction());
    panel.appendChild(submit);
  } else {
    panel.appendChild(el("div", "notification-action-message", "当前任务没有待回答的问题。"));
  }
}

async function openNotificationAction(item, row, button) {
  const key = notificationId(item);
  let panel = row.querySelector(".notification-action-panel");
  if (!panel) {
    panel = el("div", "notification-action-panel");
    row.appendChild(panel);
  }
  if (lingView.notificationDraft?.key === key && lingView.notificationDraft.open) {
    if (lingView.notificationDraft.status === "submitting") return;
    lingView.notificationDraft = null;
    panel.remove();
    button.textContent = "处理请求";
    return;
  }
  lingView.notificationDraft = { key, open: true, status: "loading", interaction: null, draft: null, row, panel };
  button.textContent = "收起";
  renderNotificationAction(panel, lingView.notificationDraft);
  try {
    const detail = await api("/api/tasks/" + encodeURIComponent(item.task_id));
    const draft = lingView.notificationDraft;
    if (!draft || draft.key !== key) return;
    const interaction = findPendingInteraction(detail, item);
    if (!interaction) {
      draft.status = "stale";
      renderNotificationAction(panel, draft);
      await markNotificationRead(item);
      return;
    }
    draft.interaction = interaction;
    draft.controlVersion = detail.control_version;
    draft.taskId = item.task_id;
    draft.status = "ready";
    renderNotificationAction(panel, draft);
  } catch (error) {
    const draft = lingView.notificationDraft;
    if (!draft || draft.key !== key) return;
    draft.status = /not found|404/i.test(error.message) ? "stale" : "error";
    draft.error = error.message;
    renderNotificationAction(panel, draft);
  }
}

async function submitNotificationAction(approval) {
  const state = lingView.notificationDraft;
  if (!state || state.status !== "ready" || !state.interaction) return;
  const panel = state.panel;
  const taskId = state.taskId;
  const key = state.key;
  const interaction = state.interaction;
  const info = notificationInteractionPayload(interaction);
  const draft = state.draft || { answers: {}, note: "" };
  const answers = info.questions.map((_, index) => String(draft.answers?.[index] || ""));
  if (info.questions.length && answers.every((answer) => !answer.trim())) return;
  state.status = "submitting";
  renderNotificationAction(panel, state);
  try {
    const fresh = await api("/api/tasks/" + encodeURIComponent(taskId));
    const pending = findPendingInteraction(fresh, { interaction_id: interaction.id });
    if (!pending) {
      state.status = "stale";
      renderNotificationAction(panel, state);
      return;
    }
    await post("/api/tasks/" + encodeURIComponent(taskId) + "/interactions/" +
      encodeURIComponent(interaction.id) + "/answer", {
        answer: interaction.kind === "approval" ? approval === true
          : (answers.length === 1 ? answers[0] : answers),
        resume: true,
        expected_version: fresh.control_version,
      });
    state.status = "done";
    renderNotificationAction(panel, state);
    const item = lingView.notifications.find((value) => notificationId(value) === key);
    if (item) await markNotificationRead(item);
    await pollLing();
  } catch (error) {
    state.status = /not found|resolved|expired|409|404|interaction/i.test(error.message) ? "stale" : "error";
    state.error = error.message;
    renderNotificationAction(panel, state);
  }
}

function findLingTask(id) {
  return lingView.tasks.find((item) => item.id === id) ||
    (typeof taskView !== "undefined" ? taskView.tasks.find((item) => item.id === id) : null);
}

async function openLingTask(id) {
  if (typeof openTask !== "function") return;
  clearLingView();
  await openTask(id);
}

function armNotificationPolling() {
  if (lingView.notificationTimer) clearTimeout(lingView.notificationTimer);
  lingView.notificationTimer = setTimeout(async () => {
    lingView.notificationTimer = null;
    if (!document.hidden) {
      try {
        await pollNotifications();
      } catch (error) {
        setStatus(error.message, true);
      }
    }
    armNotificationPolling();
  }, 5000);
}

function initLing() {
  if (lingView.initialized) return;
  lingView.initialized = true;
  $("ling-home").addEventListener("click", () => openLing().catch((error) => setStatus(error.message, true)));
  $("ling-stop").addEventListener("click", stopLing);
  $("ling-resume").addEventListener("click", resumeLing);
  $("notifications-toggle").addEventListener("click", () => setNotificationsOpen(!lingView.notificationsOpen));
  $("notifications-close").addEventListener("click", () => setNotificationsOpen(false));
  $("notifications-read-all").addEventListener("click", markAllNotificationsRead);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      pollNotifications({ startup: true }).catch((error) => setStatus(error.message, true));
      if (lingView.active) pollLing();
      else syncLegacyNotifications();
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
