"use strict";

// This page is loaded in the plugin center's iframe. It uses only the plugin
// RPC route and the two documented parent messages for task / Ling navigation.

const PLUGIN_ID = "autonomy";
const $ = (id) => document.getElementById(id);

const view = {
  snapshot: null,
  tab: "overview",
  editingSchedule: null,
  editingWatch: null,
  editingResponsibility: null,
  polling: false,
  timer: null,
  settingsSignature: "",
  scheduleSignature: "",
  watchSignature: "",
  runSignature: "",
  responsibilitySignature: "",
  hydrated: false,
  seenReplies: new Set(),
};

const WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function rpc(action, values) {
  const response = await fetch(`/api/plugins/${PLUGIN_ID}/call`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, values: values || {} }),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText);
  return payload;
}

function setStatus(message, error) {
  const node = $("status");
  node.textContent = message || "";
  node.classList.toggle("error", Boolean(error));
}

function postParent(message) {
  try {
    window.parent.postMessage(message, location.origin);
  } catch (_error) {
    setStatus("当前页面不是由插件中心打开，无法跳转。", true);
  }
}

function openTask(taskId) {
  if (!taskId) return;
  postParent({ type: "nexus.open_task", task_id: String(taskId) });
}

function dateValue(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDate(value) {
  const date = dateValue(value);
  if (!date) return "—";
  return date.toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function formatDue(value) {
  const date = dateValue(value);
  if (!date) return "—";
  return date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function localInput(value) {
  const date = dateValue(value);
  if (!date) return "";
  const pad = (number) => String(number).padStart(2, "0");
  return [
    date.getFullYear(), "-", pad(date.getMonth() + 1), "-", pad(date.getDate()),
    "T", pad(date.getHours()), ":", pad(date.getMinutes()),
  ].join("");
}

function defaultDue() {
  return localInput(new Date(Date.now() + 60 * 60 * 1000));
}

function positive(value) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

function scheduleKind(schedule) {
  if (schedule.kind === "once") {
    return schedule.due_at ? "一次 · " + formatDue(schedule.due_at) : "一次";
  }
  if (schedule.kind === "interval") return "每 " + (schedule.interval_minutes || "?") + " 分钟";
  if (schedule.kind === "daily") return "每天 " + (schedule.time || "—");
  if (schedule.kind === "weekly") {
    const days = (schedule.weekdays || []).map((day) => WEEKDAYS[day] || day).join("、");
    return "每周 " + (days || "—") + " " + (schedule.time || "—");
  }
  return schedule.kind || "定时";
}

function reasonText(reason) {
  if (!reason || typeof reason !== "object") return "活动";
  const labels = { wake: "唤醒", planning: "每日规划", task: "任务", web_change: "网页变化", work_result: "工作结果" };
  const label = labels[reason.type] || reason.type || "活动";
  const name = reason.title || reason.url || reason.summary || "";
  return name ? label + " · " + name : label;
}

function runTitle(run) {
  const reasons = Array.isArray(run.reasons) ? run.reasons : [];
  const named = reasons.find((reason) => reason && (reason.title || reason.url));
  if (named) return reasonText(named);
  const prompt = String(run.prompt || "").trim();
  return prompt ? prompt.split("\n")[0].slice(0, 90) : "自动活动";
}

function runState(run) {
  if (run.status === "failed" || run.status === "interrupted") return ["失败", "failed"];
  if (run.status === "running") return ["执行中", "on"];
  if (run.status === "pending") return ["待执行", ""];
  if (run.status === "done") return [run.reply ? "已回复" : "静默", "on"];
  return [run.status || "活动", ""];
}

function isUnread(run) {
  return Boolean(run.reply && !run.read_at);
}

function renderSummary(snapshot) {
  const box = $("summary");
  box.replaceChildren();
  const settings = snapshot.settings || {};
  const rows = [
    ["自动活动", settings.enabled ? "已启用" : "未启用"],
    ["定时", String((snapshot.schedules || []).length) + " 项"],
    ["网站监听", String((snapshot.watches || []).length) + " 项"],
    ["长期交代", String((snapshot.responsibilities || []).length) + " 项"],
    ["后台", snapshot.running ? "正在执行活动" : "空闲"],
  ];
  for (const [label, value] of rows) {
    const card = el("div", "card");
    card.appendChild(el("div", "meta", ""));
    const meta = card.firstChild;
    meta.appendChild(el("b", "", label));
    meta.appendChild(el("span", "", value));
    box.appendChild(card);
  }
}

function renderSettings(settings) {
  const signature = JSON.stringify(settings || {});
  if (signature === view.settingsSignature) return;
  if ($("settings-form").contains(document.activeElement)) return;
  view.settingsSignature = signature;
  $("enabled").checked = Boolean(settings.enabled);
  $("interval-enabled").checked = Boolean(settings.interval_enabled);
  $("interval-minutes").value = settings.interval_minutes || 60;
  $("planning-enabled").checked = Boolean(settings.planning_enabled);
  $("planning-time").value = settings.planning_time || "09:00";
  $("watch-interval").value = settings.watch_interval_minutes || 5;
}

function fact(label, value) {
  const meta = el("div", "meta");
  meta.appendChild(el("b", "", label));
  meta.appendChild(el("span", "", value));
  return meta;
}

function action(label, handler, danger) {
  const button = el("button", "link" + (danger ? " danger" : ""), label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function renderSchedules(schedules) {
  $("schedule-count").textContent = "· " + schedules.length;
  const signature = JSON.stringify(schedules);
  if (signature === view.scheduleSignature) return;
  view.scheduleSignature = signature;
  const box = $("schedules");
  box.replaceChildren();
  if (!schedules.length) {
    box.appendChild(el("p", "empty", "还没有定时。"));
    return;
  }
  for (const schedule of schedules) {
    const card = el("article", "card" + (schedule.enabled ? "" : " off"));
    const head = el("div", "card-head");
    head.appendChild(el("h3", "card-title", schedule.title || schedule.prompt || "未命名定时"));
    head.appendChild(el("span", "chip" + (schedule.enabled ? " on" : ""), schedule.enabled ? "启用" : "暂停"));
    card.appendChild(head);
    card.appendChild(fact("重复", scheduleKind(schedule)));
    card.appendChild(fact("下次", formatDate(schedule.next_run_at)));
    if (schedule.notify) card.appendChild(fact("通知", "到点后提醒"));
    if (schedule.prompt) card.appendChild(el("p", "body muted", schedule.prompt));
    const actions = el("div", "card-actions");
    actions.appendChild(action("立即唤醒", () => mutate("schedule.run", { id: schedule.id })));
    actions.appendChild(action("编辑", () => openSchedule(schedule)));
    actions.appendChild(action(schedule.enabled ? "暂停" : "恢复", () => mutate("schedule.save", {
      id: schedule.id, enabled: !schedule.enabled,
    })));
    actions.appendChild(action("删除", () => removeSchedule(schedule), true));
    card.appendChild(actions);
    box.appendChild(card);
  }
}

function renderWatches(watches) {
  $("watch-count").textContent = "· " + watches.length;
  const signature = JSON.stringify(watches);
  if (signature === view.watchSignature) return;
  view.watchSignature = signature;
  const box = $("watches");
  box.replaceChildren();
  if (!watches.length) {
    box.appendChild(el("p", "empty", "还没有网站监听。"));
    return;
  }
  for (const watch of watches) {
    const card = el("article", "card" + (watch.enabled ? "" : " off"));
    const head = el("div", "card-head");
    head.appendChild(el("h3", "card-title", watch.title || watch.url || "未命名网站"));
    head.appendChild(el("span", "chip" + (watch.enabled ? " on" : ""), watch.enabled ? "监听中" : "暂停"));
    card.appendChild(head);
    card.appendChild(fact("网址", watch.url || "—"));
    card.appendChild(fact("间隔", "每 " + (watch.interval_minutes || "?") + " 分钟"));
    card.appendChild(fact("上次检查", formatDate(watch.last_checked_at)));
    card.appendChild(fact("下次检查", formatDate(watch.next_check_at)));
    if (watch.error) card.appendChild(el("p", "body error", watch.error));
    const actions = el("div", "card-actions");
    actions.appendChild(action("立即检查", () => mutate("watch.check", { id: watch.id })));
    actions.appendChild(action("编辑", () => openWatch(watch)));
    actions.appendChild(action(watch.enabled ? "暂停" : "恢复", () => mutate("watch.save", {
      id: watch.id, enabled: !watch.enabled,
    })));
    actions.appendChild(action("删除", () => removeWatch(watch), true));
    card.appendChild(actions);
    box.appendChild(card);
  }
}

function renderRuns(runs) {
  $("run-count").textContent = "· " + runs.length;
  const signature = JSON.stringify(runs);
  if (signature === view.runSignature) return;
  view.runSignature = signature;
  const box = $("runs");
  box.replaceChildren();
  if (!runs.length) {
    box.appendChild(el("p", "empty", "还没有活动。"));
    return;
  }
  for (const run of runs) {
    const card = el("article", "card" + (isUnread(run) ? " unread" : ""));
    const head = el("div", "card-head");
    head.appendChild(el("h3", "card-title", runTitle(run)));
    const [status, chip] = runState(run);
    head.appendChild(el("span", "chip " + chip, status));
    card.appendChild(head);
    card.appendChild(fact("时间", formatDate(run.finished_at || run.created_at)));
    const reasons = Array.isArray(run.reasons) ? run.reasons : [];
    if (reasons.length) card.appendChild(el("p", "body muted", reasons.slice(0, 4).map(reasonText).join("；")));
    if (run.reply) card.appendChild(el("p", "body", "回复：" + run.reply));
    if (run.summary) card.appendChild(el("p", "body muted", "经历：" + run.summary));
    if (run.error) card.appendChild(el("p", "body error", run.error));
    if (isUnread(run)) {
      const actions = el("div", "card-actions");
      actions.appendChild(el("span", "chip on", "未读"));
      actions.appendChild(action("标为已读", () => mutate("run.read", { id: run.id })));
      card.appendChild(actions);
    }
    box.appendChild(card);
  }
}

function renderResponsibilities(records) {
  $("responsibility-count").textContent = "· " + records.length;
  const signature = JSON.stringify(records);
  if (signature === view.responsibilitySignature) return;
  view.responsibilitySignature = signature;
  const box = $("responsibilities");
  box.replaceChildren();
  if (!records.length) {
    box.appendChild(el("p", "empty", "还没有长期交代。"));
    return;
  }
  for (const record of records) {
    const card = el("article", "card" + (record.state === "active" ? "" : " off"));
    const head = el("div", "card-head");
    head.appendChild(el("h3", "card-title", record.title || "未命名长期交代"));
    const state = { active: "进行中", paused: "已暂停", ended: "已结束" }[record.state] || record.state;
    head.appendChild(el("span", "chip" + (record.state === "active" ? " on" : ""), state));
    card.appendChild(head);
    card.appendChild(fact("来源", record.origin === "ling" ? "灵建立" : "主人建立"));
    if (record.instruction) card.appendChild(el("p", "body", record.instruction));
    if (record.summary) card.appendChild(el("p", "body muted", "进展：" + record.summary));
    if ((record.tasks || []).length) {
      const links = el("div", "task-links");
      for (const task of record.tasks) {
        const button = el("button", "task-link", (task.title || "任务") + " · " + (task.state || ""));
        button.type = "button";
        button.addEventListener("click", () => openTask(task.id));
        links.appendChild(button);
      }
      card.appendChild(links);
    }
    const actions = el("div", "card-actions");
    actions.appendChild(action("编辑", () => openResponsibility(record)));
    if (record.state === "active") {
      actions.appendChild(action("暂停", () => controlResponsibility(record, "pause")));
    } else if (record.state === "paused") {
      actions.appendChild(action("恢复", () => controlResponsibility(record, "resume")));
    }
    if (record.state !== "ended") {
      actions.appendChild(action("结束", () => controlResponsibility(record, "end"), true));
    }
    card.appendChild(actions);
    box.appendChild(card);
  }
}

function showToast(run) {
  $("toast-text").textContent = run.reply;
  $("toast").hidden = false;
  $("toast-runs").onclick = () => {
    openTab("runs");
    $("toast").hidden = true;
  };
}

function apply(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return;
  view.snapshot = snapshot;
  const runs = Array.isArray(snapshot.runs) ? snapshot.runs : [];
  const unread = runs.filter(isUnread);
  if (!view.hydrated) {
    for (const run of unread) view.seenReplies.add(run.id);
    view.hydrated = true;
  } else {
    const fresh = unread.filter((run) => !view.seenReplies.has(run.id));
    for (const run of unread) view.seenReplies.add(run.id);
    if (fresh.length) showToast(fresh[0]);
    const live = new Set(unread.map((run) => run.id));
    for (const id of [...view.seenReplies]) if (!live.has(id)) view.seenReplies.delete(id);
  }
  renderSettings(snapshot.settings || {});
  renderSchedules(Array.isArray(snapshot.schedules) ? snapshot.schedules : []);
  renderWatches(Array.isArray(snapshot.watches) ? snapshot.watches : []);
  renderRuns(runs);
  renderResponsibilities(Array.isArray(snapshot.responsibilities) ? snapshot.responsibilities : []);
  renderSummary(snapshot);
  if (snapshot.error) setStatus("后台提示：" + snapshot.error, true);
  else setStatus("已更新 · " + new Date().toLocaleTimeString("zh-CN"));
}

async function refresh(force) {
  if (view.polling) return;
  if (!force && document.hidden) return;
  view.polling = true;
  try {
    const snapshot = await rpc("snapshot", {});
    apply(snapshot);
  } catch (error) {
    setStatus("暂时无法读取自主活动：" + error.message, true);
  } finally {
    view.polling = false;
  }
}

async function mutate(action, values) {
  try {
    const snapshot = await rpc(action, values);
    if (snapshot && typeof snapshot === "object" && snapshot.settings) apply(snapshot);
    else await refresh(true);
    setStatus("已保存");
    return snapshot;
  } catch (error) {
    setStatus(error.message, true);
    return null;
  }
}

function openTab(name) {
  view.tab = name;
  for (const button of document.querySelectorAll(".tab")) {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  for (const pane of document.querySelectorAll("[data-pane]")) {
    pane.hidden = pane.dataset.pane !== name;
  }
}

function updateScheduleFields() {
  const kind = $("schedule-kind").value;
  for (const node of $("schedule-form").querySelectorAll("[data-when]")) {
    const when = node.dataset.when;
    node.hidden = !(when === "once" ? kind === "once" :
      when === "interval" ? kind === "interval" :
      when === "time" ? kind === "daily" || kind === "weekly" :
      when === "weekday" ? kind === "weekly" : false);
  }
}

function openSchedule(schedule) {
  view.editingSchedule = schedule || null;
  $("schedule-form").hidden = false;
  $("schedule-error").textContent = "";
  $("schedule-title").value = schedule ? schedule.title || "" : "";
  $("schedule-prompt").value = schedule ? schedule.prompt || "" : "";
  $("schedule-kind").value = schedule ? schedule.kind || "once" : "once";
  $("schedule-time").value = schedule && schedule.time ? schedule.time : "09:00";
  $("schedule-minutes").value = schedule && schedule.interval_minutes ? schedule.interval_minutes : 60;
  $("schedule-due").value = schedule && schedule.due_at ? localInput(schedule.due_at) : defaultDue();
  $("schedule-notify").checked = Boolean(schedule && schedule.notify);
  const selected = schedule && schedule.weekdays && schedule.weekdays.length ? schedule.weekdays : [0, 1, 2, 3, 4, 5, 6];
  for (const checkbox of $("schedule-form").querySelectorAll(".weekdays input")) {
    checkbox.checked = selected.includes(Number(checkbox.value));
  }
  updateScheduleFields();
  $("schedule-form").scrollIntoView({ block: "nearest" });
}

function closeSchedule() {
  view.editingSchedule = null;
  $("schedule-form").hidden = true;
}

function openWatch(watch) {
  view.editingWatch = watch || null;
  $("watch-form").hidden = false;
  $("watch-error").textContent = "";
  $("watch-url").value = watch ? watch.url || "" : "";
  $("watch-title").value = watch ? watch.title || "" : "";
  $("watch-minutes").value = watch && watch.interval_minutes ? watch.interval_minutes : 5;
  $("watch-url").focus();
}

function closeWatch() {
  view.editingWatch = null;
  $("watch-form").hidden = true;
}

function openResponsibility(record) {
  view.editingResponsibility = record || null;
  $("responsibility-form").hidden = false;
  $("responsibility-error").textContent = "";
  $("responsibility-title").value = record ? record.title || "" : "";
  $("responsibility-instruction").value = record ? record.instruction || "" : "";
  $("responsibility-summary").value = record ? record.summary || "" : "";
  $("responsibility-title").focus();
}

function closeResponsibility() {
  view.editingResponsibility = null;
  $("responsibility-form").hidden = true;
}

async function saveSettings(event) {
  event.preventDefault();
  const interval = positive($("interval-minutes").value);
  const watchInterval = positive($("watch-interval").value);
  if (!interval || !watchInterval) {
    $("settings-status").textContent = "间隔必须是正整数分钟。";
    return;
  }
  $("settings-status").textContent = "保存中…";
  const value = await mutate("settings.save", {
    enabled: $("enabled").checked,
    interval_enabled: $("interval-enabled").checked,
    interval_minutes: interval,
    planning_enabled: $("planning-enabled").checked,
    planning_time: $("planning-time").value || "09:00",
    watch_interval_minutes: watchInterval,
  });
  $("settings-status").textContent = value ? "已保存" : "保存失败";
  view.settingsSignature = "";
}

async function saveSchedule(event) {
  event.preventDefault();
  const existing = view.editingSchedule;
  const title = $("schedule-title").value.trim();
  const prompt = $("schedule-prompt").value.trim();
  const kind = $("schedule-kind").value;
  if (!prompt && (!existing || existing.purpose === "task")) {
    $("schedule-error").textContent = "请填写交给灵的内容。";
    return;
  }
  const values = {
    title: title || prompt.split("\n")[0].slice(0, 80),
    prompt, kind, notify: $("schedule-notify").checked,
    origin: existing && existing.origin ? existing.origin : "user",
  };
  if (existing) values.id = existing.id;
  if (kind === "once") {
    const due = new Date($("schedule-due").value);
    if (Number.isNaN(due.getTime()) || due.getTime() <= Date.now()) {
      $("schedule-error").textContent = "请选择将来的时间。";
      return;
    }
    values.due_at = due.toISOString();
  } else if (kind === "interval") {
    const minutes = positive($("schedule-minutes").value);
    if (!minutes) {
      $("schedule-error").textContent = "间隔必须是正整数分钟。";
      return;
    }
    values.interval_minutes = minutes;
  } else {
    values.time = $("schedule-time").value || "09:00";
    if (kind === "weekly") {
      const days = [...$("schedule-form").querySelectorAll(".weekdays input:checked")]
        .map((checkbox) => Number(checkbox.value));
      if (!days.length) {
        $("schedule-error").textContent = "请至少选择一个星期。";
        return;
      }
      values.weekdays = days;
    } else {
      values.weekdays = [];
    }
  }
  values.enabled = existing ? Boolean(existing.enabled) : true;
  if (existing && existing.kind === "once" && !existing.enabled && !existing.next_run_at) values.enabled = true;
  const result = await mutate("schedule.save", values);
  if (result) closeSchedule();
}

async function saveWatch(event) {
  event.preventDefault();
  const existing = view.editingWatch;
  const url = $("watch-url").value.trim();
  const minutes = positive($("watch-minutes").value);
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
  } catch (_error) {
    $("watch-error").textContent = "请填写 http 或 https 网址。";
    return;
  }
  if (!minutes) {
    $("watch-error").textContent = "间隔必须是正整数分钟。";
    return;
  }
  const values = {
    title: $("watch-title").value.trim(), url, interval_minutes: minutes,
    enabled: existing ? Boolean(existing.enabled) : true,
    origin: existing && existing.origin ? existing.origin : "user",
  };
  if (existing) values.id = existing.id;
  const result = await mutate("watch.save", values);
  if (result) closeWatch();
}

async function saveResponsibility(event) {
  event.preventDefault();
  const existing = view.editingResponsibility;
  const title = $("responsibility-title").value.trim();
  const instruction = $("responsibility-instruction").value.trim();
  if (!title || !instruction) {
    $("responsibility-error").textContent = "标题和说明都不能为空。";
    return;
  }
  const values = { title, instruction, summary: $("responsibility-summary").value.trim() };
  if (existing) values.id = existing.id;
  const result = await mutate("responsibility.save", values);
  if (result) closeResponsibility();
}

async function removeSchedule(schedule) {
  if (!window.confirm("删除这个定时？")) return;
  const result = await mutate("schedule.delete", { id: schedule.id });
  if (result && view.editingSchedule && view.editingSchedule.id === schedule.id) closeSchedule();
}

async function removeWatch(watch) {
  if (!window.confirm("删除这个网站监听？")) return;
  const result = await mutate("watch.delete", { id: watch.id });
  if (result && view.editingWatch && view.editingWatch.id === watch.id) closeWatch();
}

async function controlResponsibility(record, operation) {
  if (operation === "end" && !window.confirm("结束这条长期交代？已经运行的工作会继续完成。")) return;
  await mutate("responsibility.control", { id: record.id, operation });
}

function armPolling() {
  if (view.timer) clearTimeout(view.timer);
  view.timer = setTimeout(async () => {
    await refresh();
    armPolling();
  }, 3000);
}

function init() {
  for (const button of document.querySelectorAll(".tab")) {
    button.addEventListener("click", () => openTab(button.dataset.tab));
  }
  const weekdays = $("schedule-form").querySelector(".weekdays");
  WEEKDAYS.forEach((name, index) => {
    const label = el("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = String(index);
    label.appendChild(checkbox);
    label.appendChild(document.createTextNode(name));
    weekdays.appendChild(label);
  });
  $("refresh").addEventListener("click", () => refresh(true));
  $("go-ling").addEventListener("click", () => postParent({ type: "nexus.open_ling" }));
  $("settings-form").addEventListener("submit", saveSettings);
  $("schedule-add").addEventListener("click", () => openSchedule(null));
  $("schedule-cancel").addEventListener("click", closeSchedule);
  $("schedule-form").addEventListener("submit", saveSchedule);
  $("schedule-kind").addEventListener("change", updateScheduleFields);
  $("watch-add").addEventListener("click", () => openWatch(null));
  $("watch-cancel").addEventListener("click", closeWatch);
  $("watch-form").addEventListener("submit", saveWatch);
  $("responsibility-add").addEventListener("click", () => openResponsibility(null));
  $("responsibility-cancel").addEventListener("click", closeResponsibility);
  $("responsibility-form").addEventListener("submit", saveResponsibility);
  $("toast-close").addEventListener("click", () => { $("toast").hidden = true; });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  refresh(true);
  armPolling();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}
