"use strict";

// Persistent task controls. A task is owned by the server; this module only
// submits commands and renders the facts returned by the task API.

const taskView = {
  tasks: [],
  current: null,
  timer: null,
};

const taskPost = (path, body) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

const taskPut = (path, body) =>
  api(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });

function newControlId(prefix) {
  return (prefix || "ui") + "-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
}

async function refreshTasks() {
  const value = await api("/api/tasks");
  taskView.tasks = value.tasks || [];
  renderTasks();
}

function renderTasks() {
  const list = $("tasks");
  list.innerHTML = "";
  for (const item of taskView.tasks) {
    const row = el("li");
    if (taskView.current && item.id === taskView.current.id) row.classList.add("active");
    const label = el("span", "label", item.title || "未命名任务");
    label.title = item.goal || "";
    row.appendChild(label);
    const stateLabelValue = item.state_label || stateLabel(item.state);
    row.appendChild(el("span", "task-list-state small", stateLabelValue + (item.active ? " · 当前" : "")));
    const progress = String(item.progress_text || "").trim();
    row.appendChild(el("span", "task-list-detail", progress || "暂无进展说明"));
    if (item.wait_reason) row.appendChild(el("span", "task-list-detail", item.wait_reason));
    if (item.schedule) row.appendChild(el("span", "task-list-schedule", item.schedule_label || "已安排"));
    row.addEventListener("click", () => openTask(item.id));
    list.appendChild(row);
  }
}

async function openTask(id, {refresh = false} = {}) {
  if (!refresh) rememberCurrentScroll();
  const viewVersion = refresh ? state.viewVersion : (state.viewVersion = (state.viewVersion || 0) + 1);
  if (!refresh && typeof clearLingView === "function" && lingView.active) clearLingView();
  if (taskView.timer) clearTimeout(taskView.timer);
  taskView.timer = null;
  const value = await api("/api/tasks/" + encodeURIComponent(id));
  if (viewVersion !== state.viewVersion || (refresh && taskView.current?.id !== id)) return;
  const previous = taskView.current;
  const sameTask = previous && previous.id === value.id;
  taskView.current = value;
  $("task-panel").hidden = false;
  $("task-title").textContent = value.title || "未命名任务";
  $("task-status").textContent = value.status_detail || stateLabel(value.state);
  $("task-state-label").textContent = value.state_label || stateLabel(value.state);
  $("task-schedule-label").textContent = value.schedule_label || "未安排定时。";
  $("task-progress-text").textContent = value.progress_text || "暂无进展说明";
  $("task-display").hidden = false;
  $("scroller").hidden = false;
  $("composer").hidden = false;
  renderTaskActions(value);
  const description = [];
  if (value.effective_goal || value.goal) description.push("目标：" + (value.effective_goal || value.goal));
  if (value.constraints) description.push("约束：" + value.constraints);
  if (value.next_step && value.state !== "completed") description.push("下一步：" + value.next_step);
  if (value.evidence && value.evidence.length) {
    description.push("证据：" + value.evidence.map((item) => item.summary).join("；"));
  }
  if (value.blocked) description.push("阻塞：" + value.blocked);
  if (value.unfinished_operations && value.unfinished_operations.length) {
    description.push("未确认操作：" + value.unfinished_operations.map((item) => item.tool_name).join("、"));
  }
  $("task-summary").textContent = description.join("\n");
  if (!sameTask || previous.state !== value.state || JSON.stringify(previous.steps) !== JSON.stringify(value.steps)) {
    renderTaskSteps(value.steps || []);
  }
  if (!sameTask || JSON.stringify(previous.pending_interactions) !== JSON.stringify(value.pending_interactions)) {
    renderTaskInteractions(value.pending_interactions || []);
  }
  if (!sameTask || JSON.stringify(previous.schedule) !== JSON.stringify(value.schedule)) {
    $("task-due").value = value.schedule ? localInput(value.schedule.due_at) : "";
    $("task-timezone").value = value.schedule ? value.schedule.timezone : Intl.DateTimeFormat().resolvedOptions().timeZone;
    $("task-mode").value = value.schedule ? value.schedule.mode : "confirm";
  }
  $("task-schedule-clear").hidden = !value.schedule || ["completed", "cancelled"].includes(value.state);
  $("task-schedule-save").hidden = ["completed", "cancelled"].includes(value.state);
  $("title").textContent = value.title || "任务";
  state.sessionId = null;
  state.items = (value.messages || []).map((message) => ({
    role: message.kind,
    text: message.text || "",
    at: message.created_at,
    name: message.name,
    arguments: message.arguments,
    ok: message.ok,
    thinking: message.thinking,
    attachments: message.attachments || [],
  }));
  renderTranscript({preserveScroll: sameTask});
  if (!sameTask) restoreViewScroll("task:" + value.id);
  armTaskPolling();
  await refreshTasks();
}

// Each persisted state exposes only the controls that can apply to it.
function renderTaskActions(value) {
  const state = value.state;
  const pending = Array.isArray(value.pending_interactions) ? value.pending_interactions : [];
  const needsDecision = pending.some((item) => ["approval", "question", "start_confirmation"].includes(item.kind));
  const canStart = ["pending", "paused", "waiting_user"].includes(state) && !needsDecision;
  const canPause = ["running", "queued", "waiting_user"].includes(state);
  const canFinish = ["pending", "paused", "waiting_user"].includes(state);
  $("task-start").hidden = !canStart;
  $("task-start").textContent = state === "paused" ? "恢复" : "开始";
  $("task-pause").hidden = !canPause;
  $("task-cancel").hidden = ["completed", "cancelled"].includes(state);
  $("task-reopen").hidden = state !== "cancelled";
  $("task-mark-complete").hidden = !canFinish;
}

async function sendTaskMessage(text) {
  const current = taskView.current;
  if (!current || (!text && !pendingFiles.length && !pendingImages.length) || state.preparing) return false;
  const files = pendingFiles.slice();
  const images = pendingImages.slice();
  state.preparing = true;
  try {
    const attachments = await uploadFiles("tasks", current.id, [...files, ...images]);
    await taskPost("/api/tasks/" + encodeURIComponent(current.id) + "/messages", {
      text,
      attachments: attachments.map(file => file.id),
      resume: false,
      request_id: newControlId("message"),
    });
    pendingFiles = pendingFiles.filter(file => !files.includes(file));
    pendingImages = pendingImages.filter(image => !images.includes(image));
    renderAttachments();
    await openTask(current.id);
    return true;
  } catch (error) {
    setStatus(error.message, true);
    return false;
  } finally {
    state.preparing = false;
  }
}

function stateLabel(value) {
  return {
    pending: "待执行", paused: "已挂起", queued: "排队中", running: "执行中", pausing: "正在挂起",
    waiting_user: "等待你处理", completed: "已完成", cancelled: "已取消",
  }[value] || value;
}

function localInput(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value) => String(value).padStart(2, "0");
  return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate()) +
    "T" + pad(date.getHours()) + ":" + pad(date.getMinutes());
}

function renderTaskSteps(steps) {
  const box = $("task-steps");
  $("task-records").hidden = !steps.length;
  box.innerHTML = "";
  steps.forEach((step, index) => {
    const row = el("div", "task-step");
    const status = step.status === "completed" ? "完成" : step.status === "in_progress" ? "进行中" : step.status === "blocked" ? "受阻" : "未开始";
    const statusNode = el("span", "badge", "");
    const verifyNode = el("select", "badge");
    for (const [key, label] of [["unverified", "未验证"], ["verified", "已验证"], ["failed", "验证失败"], ["unknown", "未知"]]) {
      const option = el("option", "", label);
      option.value = key;
      option.selected = step.verification === key;
      verifyNode.appendChild(option);
    }
    verifyNode.disabled = currentTaskRunning();
    verifyNode.addEventListener("change", () => updateTaskStep(index, {verification: verifyNode.value}));
    statusNode.textContent = status + " / ";
    row.appendChild(statusNode);
    row.appendChild(verifyNode);
    row.appendChild(el("span", "", step.content));
    box.appendChild(row);
  });
}

function currentTaskRunning() {
  return Boolean(taskView.current && ["running", "pausing"].includes(taskView.current.state));
}

async function updateTaskStep(index, change) {
  const current = taskView.current;
  if (!current) return;
  try {
    const steps = (current.steps || []).map((step, position) =>
      position === index ? {...step, ...change} : step
    );
    const updated = await api("/api/tasks/" + encodeURIComponent(current.id), {
      method: "PUT",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({expected_version: current.control_version, steps}),
    });
    await openTask(updated.id);
  } catch (error) {
    setStatus(error.message, true);
  }
}

function renderTaskInteractions(interactions) {
  const box = $("task-interactions");
  box.innerHTML = "";
  for (const interaction of interactions) {
    const row = el("div", "task-step");
    const payload = interaction.payload || {};
    const text = payload.questions ? payload.questions.map((item) => item.question).join(" / ") : (payload.tool ? "需要批准 " + payload.tool : "任务需要你的确认");
    row.appendChild(el("span", "badge", interaction.kind === "start_confirmation" ? "定时确认" : interaction.kind === "approval" ? "审批" : "问题"));
    row.appendChild(el("span", "", text));
    if (interaction.kind === "start_confirmation") {
      const accept = el("button", "", "确认开始");
      accept.type = "button";
      accept.addEventListener("click", () => confirmTaskStart(interaction));
      row.appendChild(accept);
    }
    if (interaction.kind === "approval") {
      // Approval is an explicit binary choice, so no free text is parsed.
      const allow = el("button", "", "允许");
      allow.type = "button";
      allow.addEventListener("click", () => answerTaskInteraction(interaction, true));
      const deny = el("button", "", "拒绝");
      deny.type = "button";
      deny.addEventListener("click", () => answerTaskInteraction(interaction, false));
      row.appendChild(allow);
      row.appendChild(deny);
    } else if (interaction.kind === "question") {
      const answer = el("input", "question-free");
      answer.type = "text";
      answer.placeholder = "回答";
      const submit = el("button", "", "提交并继续");
      submit.type = "button";
      submit.addEventListener("click", () => answerTaskInteraction(interaction, answer.value));
      row.appendChild(answer);
      row.appendChild(submit);
    }
    box.appendChild(row);
  }
}

async function confirmTaskStart(interaction) {
  const current = taskView.current;
  if (!current) return;
  try {
    await taskPost("/api/tasks/" + encodeURIComponent(current.id) + "/interactions/" +
      encodeURIComponent(interaction.id) + "/confirm", {
        answer: true,
        expected_version: current.control_version,
      });
    await openTask(current.id);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function answerTaskInteraction(interaction, value) {
  const current = taskView.current;
  if (!current) return;
  const approval = interaction.kind === "approval";
  if (approval ? typeof value !== "boolean" : !String(value).trim()) return;
  const answer = approval ? value : String(value).trim();
  try {
    await taskPost("/api/tasks/" + encodeURIComponent(current.id) + "/interactions/" +
      encodeURIComponent(interaction.id) + "/answer", {
        answer, resume: true, expected_version: current.control_version,
      });
    await openTask(current.id);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function taskCommand(operation) {
  const current = taskView.current;
  if (!current) return;
  try {
    await taskPost("/api/tasks/" + encodeURIComponent(current.id) + "/commands", {
      operation,
      request_id: newControlId("command"),
      expected_version: current.control_version,
    });
    await openTask(current.id);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function createTask() {
  try {
    const created = await taskPost("/api/tasks", {
      title: $("task-name").value.trim(),
      goal: $("task-goal").value.trim(),
      constraints: $("task-constraints").value.trim(),
      request_id: newControlId("create"),
    });
    $("task-dialog").close();
    await refreshTasks();
    await openTask(created.id);
  } catch (error) {
    $("task-error").textContent = error.message;
  }
}

async function saveTaskSchedule() {
  const current = taskView.current;
  if (!current) return;
  try {
    const due = $("task-due").value;
    if (!due) throw new Error("请选择日期和时间");
    await taskPut("/api/tasks/" + encodeURIComponent(current.id) + "/schedule", {
      due_at: new Date(due).toISOString(),
      timezone: $("task-timezone").value.trim() || "UTC",
      mode: $("task-mode").value,
    });
    await openTask(current.id);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function clearTaskSchedule() {
  const current = taskView.current;
  if (!current) return;
  try {
    await api("/api/tasks/" + encodeURIComponent(current.id) + "/schedule", { method: "DELETE" });
    $("task-due").value = "";
    await openTask(current.id);
  } catch (error) {
    setStatus(error.message, true);
  }
}

function armTaskPolling() {
  if (taskView.timer) clearTimeout(taskView.timer);
  taskView.timer = setTimeout(async () => {
    taskView.timer = null;
    try {
      if (taskView.current && !document.hidden) {
        await openTask(taskView.current.id, {refresh: true});
      }
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      if (taskView.current && !taskView.timer) armTaskPolling();
    }
  }, 1500);
}

async function naturalTaskControl(text) {
  const current = taskView.current;
  try {
    await taskPost("/api/task-control", {
      task_id: current ? current.id : "", text, request_id: newControlId("control"),
    });
    await refreshTasks();
    if (current) await openTask(current.id);
    return true;
  } catch (error) {
    // A non-control message remains ordinary task input only after the server
    // has rejected the deterministic short-command path.
    if (/unambiguous command|identify exactly one task/.test(error.message)) return false;
    setStatus(error.message, true);
    return null;
  }
}

$("new").addEventListener("click", () => { taskView.current = null; });
$("task-new").addEventListener("click", () => {
  $("task-name").value = "";
  $("task-goal").value = "";
  $("task-constraints").value = "";
  $("task-error").textContent = "";
  $("task-dialog").showModal();
});
$("task-create").addEventListener("click", createTask);
$("task-start").addEventListener("click", () => taskCommand("start"));
$("task-pause").addEventListener("click", () => taskCommand("pause"));
$("task-cancel").addEventListener("click", () => taskCommand("cancel"));
$("task-reopen").addEventListener("click", () => taskCommand("reopen"));
$("task-mark-complete").addEventListener("click", () => taskLifecycle("mark_completed"));
$("task-schedule-save").addEventListener("click", saveTaskSchedule);
$("task-schedule-clear").addEventListener("click", clearTaskSchedule);

refreshTasks().catch((error) => setStatus(error.message, true));

async function taskLifecycle(operation) {
  const current = taskView.current;
  if (!current) return;
  try {
    await taskPost("/api/tasks/" + encodeURIComponent(current.id) + "/lifecycle", {
      operation, expected_version: current.control_version,
      request_id: newControlId("lifecycle"),
    });
    await openTask(current.id);
  } catch (error) {
    setStatus(error.message, true);
  }
}
