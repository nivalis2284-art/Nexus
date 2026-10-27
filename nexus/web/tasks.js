"use strict";

// Work details stay beside the Ling conversation. The task store owns every
// fact and control; this module only renders them and reuses its HTTP routes.

const taskView = {
  tasks: [],
  current: null,
  lastOpen: null,
  request: 0,
  live: null,
  liveRevision: 0,
  refreshPending: false,
};

const taskPost = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

async function refreshTasks() {
  const value = await api("/api/tasks");
  taskView.tasks = value.tasks || [];
  renderTasks();
}

function taskPendingCount(task) {
  return Array.isArray(task && task.pending_interactions)
    ? task.pending_interactions.length
    : Number((task && task.pending_interactions) || 0);
}

function renderTasks() {
  const list = $("tasks");
  if (!list) return;
  list.innerHTML = "";
  for (const item of taskView.tasks) {
    const row = el("li", "work-card");
    if (taskView.current && item.id === taskView.current.id) row.classList.add("active");
    row.tabIndex = 0;
    const head = el("div", "work-card-head");
    head.appendChild(el("span", "label", item.title || "未命名工作"));
    head.appendChild(el("span", "task-list-state small", item.state_label || stateLabel(item.state)));
    row.appendChild(head);
    const pending = taskPendingCount(item);
    if (pending) row.appendChild(el("span", "work-card-pending", pending + " 项待处理"));
    const progress = String(item.progress_text || item.work_summary || "").trim();
    if (progress) row.appendChild(el("span", "task-list-detail", progress));
    if (item.wait_reason) row.appendChild(el("span", "task-list-detail", item.wait_reason));
    row.addEventListener("click", () => openTask(item.id).catch((error) => setStatus(error.message, true)));
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openTask(item.id).catch((error) => setStatus(error.message, true));
      }
    });
    list.appendChild(row);
  }
}

async function openTask(id, {refresh = false} = {}) {
  const request = ++taskView.request;
  const value = await api("/api/tasks/" + encodeURIComponent(id));
  if (request !== taskView.request) return;
  const previous = taskView.current;
  const sameTask = previous && previous.id === value.id;
  const snapshotRevision = Number(value.live_revision ?? (value.live && value.live.revision) ?? 0);
  if (!sameTask) {
    taskView.liveRevision = 0;
  }
  const newerLive = sameTask && snapshotRevision < taskView.liveRevision;
  if (!newerLive) {
    taskView.liveRevision = snapshotRevision;
  } else {
    value.live = previous?.live || null;
  }
  taskView.current = value;
  taskView.lastOpen = id;
  if (!lingView.active) {
    await openLing({keepTranscript: true});
  }
  $("task-panel").hidden = false;
  $("task-title").textContent = value.title || "未命名工作";
  $("task-status").textContent = value.status_detail || stateLabel(value.state);
  $("task-state-label").textContent = value.state_label || stateLabel(value.state);
  $("task-progress-text").textContent = value.progress_text || "暂无进展说明";
  $("task-display").hidden = false;
  $("title").textContent = "灵 · " + (value.title || "工作");
  $("work-reopen").hidden = false;

  renderTaskSummary(value);
  if (JSON.stringify(previous?.steps) !== JSON.stringify(value.steps)) {
    renderTaskSteps(value.steps || []);
  }
  renderTaskMessages(value.messages || [], value.live);
  renderTaskInteractions(value.pending_interactions || []);
  if (!refresh && typeof connectRuntimeStream === "function") connectRuntimeStream();
  if (typeof renderComposerTargets === "function") renderComposerTargets();
  await refreshTasks();
}

function closeTask() {
  taskView.current = null;
  taskView.request += 1;
  const panel = $("task-panel");
  if (panel) panel.hidden = true;
  $("title").textContent = "灵";
  $("work-reopen").hidden = false;
  renderTasks();
}

function reopenTask() {
  if (!taskView.lastOpen) return;
  $("work-reopen").hidden = true;
  openTask(taskView.lastOpen).catch((error) => setStatus(error.message, true));
}

function renderTaskSummary(value) {
  const description = [];
  if (value.effective_goal || value.goal) description.push("目标：" + (value.effective_goal || value.goal));
  if (value.constraints) description.push("约束：" + value.constraints);
  if (value.work_summary) description.push("结果：" + value.work_summary);
  if (value.next_step && value.state !== "completed") description.push("下一步：" + value.next_step);
  if (value.evidence && value.evidence.length) {
    description.push("证据：" + value.evidence.map((item) => item.summary).join("；"));
  }
  if (value.blocked) description.push("阻塞：" + value.blocked);
  if (value.unfinished_operations && value.unfinished_operations.length) {
    description.push("未确认操作：" + value.unfinished_operations.map((item) => item.tool_name).join("、"));
  }
  $("task-summary").textContent = description.join("\n");
}

function stateLabel(value) {
  return {
    pending: "待执行", paused: "已挂起", queued: "排队中", running: "执行中", pausing: "正在挂起",
    waiting_user: "等待你处理", completed: "已完成", cancelled: "已取消",
  }[value] || value;
}

function renderTaskSteps(steps) {
  const box = $("task-steps");
  $("task-records").hidden = !steps.length;
  box.innerHTML = "";
  steps.forEach((step) => {
    const row = el("div", "task-step");
    const status = step.status === "completed" ? "完成" : step.status === "in_progress" ? "进行中"
      : step.status === "blocked" ? "受阻" : "未开始";
    const verified = step.verification === "verified" ? "已验证" : step.verification === "failed"
      ? "验证失败" : step.verification === "unknown" ? "验证未知" : "未验证";
    row.appendChild(el("span", "badge", status + " / " + verified));
    row.appendChild(el("span", "", step.content || ""));
    box.appendChild(row);
  });
}

function renderTaskMessages(messages, live) {
  const box = $("task-messages");
  if (!box) return;
  const items = mergeLiveItems(taskMessageItems(messages), live);
  reconcileTranscript(box, items, taskView.current?.workspace);
  const panel = $("task-messages-panel");
  if (panel) panel.hidden = !items.length;
}

function taskMessageItems(messages, live) {
  return messages
    .filter((message) => message && (message.text || message.thinking || message.name || message.attachments?.length))
    .map((message, index) => ({
      ...message,
      role: message.kind,
      at: message.created_at,
      key: "task:" + (message.seq ?? index),
    }));
}

function renderTaskInteractions(interactions) {
  const box = $("task-interactions");
  if (!box) return;
  box.innerHTML = "";
  if (!interactions.length) return;
  box.appendChild(el("div", "small", "待处理请求已在主交流上方显示；回答后会作用到这项工作。"));
  for (const interaction of interactions) {
    const payload = interaction.payload || {};
    const text = payload.questions
      ? payload.questions.map((item) => item.question || item.text).join(" / ")
      : payload.tool ? "需要批准 " + payload.tool : "需要你的确认";
    const row = el("div", "task-step");
    row.appendChild(el("span", "badge",
      interaction.kind === "approval" ? "审批" : "问题"));
    row.appendChild(el("span", "", text));
    box.appendChild(row);
  }
}

function taskRuntimeEvent(event) {
  if (!taskView.current || event.owner_id !== taskView.current.id) return;
  const revision = liveRevision(event);
  if (revision < taskView.liveRevision) return;
  taskView.liveRevision = revision;
  const nextLive = event.live && typeof event.live === "object" && Object.keys(event.live).length
    ? event.live : null;
  taskView.current.live = nextLive;
  if (["delta", "thinking", "tool_start", "tool_result", "error", "done"].includes(event.type)) {
    renderTaskMessages(taskView.current.messages || [], taskView.current.live);
  }
  if (event.type === "refresh" || event.type === "done") {
    if (!document.hidden) openTask(taskView.current.id, {refresh: true})
      .catch((error) => setStatus(error.message, true));
  }
}

$("task-close").addEventListener("click", closeTask);
$("work-reopen").addEventListener("click", reopenTask);

refreshTasks().catch((error) => setStatus(error.message, true));
