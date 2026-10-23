"use strict";

// Schedules and page watches share the Ling conversation with ordinary chat,
// but their UI stays here. Silent wake results are experiences, not messages.

const automationView = {
  initialized: false,
  open: false,
  snapshot: null,
  polling: false,
  pollTimer: null,
  mutating: 0,
  hydrated: false,
  seenUnreadReplies: new Set(),
  settingsDirty: false,
  settingsSignature: "",
  scheduleSignature: "",
  watchSignature: "",
  runSignature: "",
  editingSchedule: null,
  editingWatch: null,
  toastRunId: null,
};

const AUTOMATION_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

function automationEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function automationOn(id, event, handler) {
  const node = $(id);
  if (node) node.addEventListener(event, handler);
}

function automationNumber(value, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}

function automationDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function automationFormatDate(value) {
  const date = automationDate(value);
  if (!date) return "—";
  return date.toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function automationFormatDue(value) {
  const date = automationDate(value);
  if (!date) return "—";
  return date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function automationLocalInput(value) {
  const date = automationDate(value);
  if (!date) return "";
  const pad = (number) => String(number).padStart(2, "0");
  return [
    date.getFullYear(), "-", pad(date.getMonth() + 1), "-", pad(date.getDate()),
    "T", pad(date.getHours()), ":", pad(date.getMinutes()),
  ].join("");
}

function automationDefaultDue() {
  return automationLocalInput(new Date(Date.now() + 60 * 60 * 1000));
}

function automationWeekdays(value) {
  if (!Array.isArray(value)) return [];
  return value.map(Number).filter((day) => Number.isInteger(day) && day >= 0 && day <= 6);
}

function automationWeekdayLabel(day) {
  return AUTOMATION_WEEKDAYS[day] || "";
}

function automationReasonText(reason) {
  if (typeof reason === "string") return reason;
  if (!reason || typeof reason !== "object") return "";
  const labels = {
    task: "定时任务",
    wake: "定时唤醒",
    planning: "每日规划",
    web_change: "网站变化",
    time_floor: "定期回顾",
  };
  const type = labels[reason.type] || reason.type || "活动";
  const name = reason.title || reason.url || "";
  return name ? type + " · " + name : type;
}

function automationRunTitle(run) {
  const reasons = Array.isArray(run.reasons) ? run.reasons : [];
  const named = reasons.find((reason) => reason && typeof reason === "object" && (reason.title || reason.url));
  if (named) return automationReasonText(named);
  const prompt = String(run.prompt || "").trim();
  if (prompt) return prompt.split("\n")[0].slice(0, 90);
  return reasons.length ? automationReasonText(reasons[0]) : "自动活动";
}

function automationIsUnread(run) {
  return Boolean(run && run.reply && !run.read_at);
}

function automationUnreadRuns(snapshot) {
  const runs = snapshot && Array.isArray(snapshot.runs) ? snapshot.runs : [];
  return runs.filter(automationIsUnread);
}

function automationShowError(message) {
  const box = $("automation-error");
  if (!box) return;
  box.textContent = String(message || "操作失败");
  box.hidden = false;
}

function automationClearError() {
  const box = $("automation-error");
  if (!box) return;
  box.textContent = "";
  box.hidden = true;
}

function automationHideToast() {
  const toast = $("automation-toast");
  if (toast) toast.hidden = true;
  automationView.toastRunId = null;
}

function automationShowToast(run) {
  if (!automationIsUnread(run)) return;
  automationView.toastRunId = run.id;
  const toast = $("automation-toast");
  if (!toast) return;
  const kicker = toast.querySelector(".automation-toast-kicker");
  if (kicker) kicker.textContent = run.notify ? "提醒" : "新的回复";
  $("automation-toast-text").textContent = run.reply;
  $("automation-toast-meta").textContent = automationRunTitle(run) + " · " +
    automationFormatDate(run.finished_at || run.created_at);
  toast.hidden = false;
}

function automationApplySnapshot(value) {
  if (!value || typeof value !== "object") return;
  automationView.snapshot = value;
  if (automationView.editingSchedule && !Array.isArray(value.schedules)) {
    automationView.editingSchedule = null;
  }
  const unread = automationUnreadRuns(value);
  if (!automationView.hydrated) {
    for (const run of unread) automationView.seenUnreadReplies.add(run.id);
    automationView.hydrated = true;
  } else {
    const fresh = unread.filter((run) => !automationView.seenUnreadReplies.has(run.id));
    for (const run of unread) automationView.seenUnreadReplies.add(run.id);
    const live = new Set(unread.map((run) => run.id));
    for (const id of [...automationView.seenUnreadReplies]) {
      if (!live.has(id)) automationView.seenUnreadReplies.delete(id);
    }
    if (fresh.length) automationShowToast(fresh[0]);
  }
  automationRender();
}

function automationRender() {
  const snapshot = automationView.snapshot;
  if (!snapshot) return;
  const settings = snapshot.settings || {};
  const enabled = Boolean(settings.enabled);
  const unread = automationUnreadRuns(snapshot);
  const toggle = $("automation-open");
  if (toggle) toggle.classList.toggle("is-enabled", enabled);
  $("automation-state-short").textContent = enabled ? "已启用" : "未启用";
  $("automation-state").textContent = enabled ? "已启用" : "未启用";
  const badge = $("automation-unread-count");
  badge.textContent = String(unread.length);
  badge.hidden = unread.length === 0;
  const running = $("automation-running");
  running.textContent = snapshot.running ? "正在处理一次活动" : "";
  running.title = snapshot.error ? "后台错误：" + snapshot.error : "";
  if (snapshot.error) running.textContent += (running.textContent ? " · " : "") + "后台错误";
  automationRenderSettings(settings);
  automationRenderSchedules(Array.isArray(snapshot.schedules) ? snapshot.schedules : []);
  automationRenderWatches(Array.isArray(snapshot.watches) ? snapshot.watches : []);
  automationRenderRuns(Array.isArray(snapshot.runs) ? snapshot.runs : []);
}

function automationRenderSettings(settings) {
  const form = $("automation-settings-form");
  if (!form || automationView.settingsDirty) return;
  if (form.contains(document.activeElement)) return;
  const signature = JSON.stringify(settings || {});
  if (signature === automationView.settingsSignature) return;
  automationView.settingsSignature = signature;
  $("automation-enabled").checked = Boolean(settings.enabled);
  $("automation-interval-enabled").checked = Boolean(settings.interval_enabled);
  $("automation-interval-minutes").value = settings.interval_minutes || 60;
  $("automation-planning-enabled").checked = Boolean(settings.planning_enabled);
  $("automation-planning-time").value = settings.planning_time || "09:00";
  $("automation-watch-interval").value = settings.watch_interval_minutes || 5;
}

function automationScheduleSummary(schedule) {
  if (schedule.kind === "once") {
    return "一次 · " + (schedule.due_at ? automationFormatDue(schedule.due_at) : "未设置时间");
  }
  if (schedule.kind === "interval") {
    return "每 " + (schedule.interval_minutes || "?") + " 分钟";
  }
  if (schedule.kind === "daily") return "每天 " + (schedule.time || "—");
  if (schedule.kind === "weekly") {
    const days = automationWeekdays(schedule.weekdays);
    return "每周 " + (days.length ? days.map(automationWeekdayLabel).join("、") : "未选星期") +
      " " + (schedule.time || "—");
  }
  return schedule.kind || "定时";
}

function automationScheduleState(schedule) {
  if (schedule.enabled) return { text: "启用", cls: "automation-chip-on" };
  if (schedule.kind === "once" && !schedule.next_run_at) {
    return { text: "已完成", cls: "automation-chip-done" };
  }
  return { text: "暂停", cls: "automation-chip-paused" };
}

function automationFact(label, value) {
  const row = automationEl("div", "automation-fact");
  row.appendChild(automationEl("span", "automation-fact-label", label));
  row.appendChild(automationEl("span", "automation-fact-value", value));
  return row;
}

function automationActionButton(label, handler, danger) {
  const button = automationEl("button", danger ? "automation-link-danger" : "automation-link", label);
  button.type = "button";
  button.addEventListener("click", handler);
  return button;
}

function automationRenderSchedules(schedules) {
  const box = $("automation-schedules");
  const count = $("automation-schedule-count");
  if (!box) return;
  count.textContent = "· " + schedules.length;
  const signature = JSON.stringify(schedules);
  if (signature === automationView.scheduleSignature) return;
  automationView.scheduleSignature = signature;
  box.replaceChildren();
  if (!schedules.length) {
    box.appendChild(automationEl("p", "automation-empty", "还没有定时。"));
    return;
  }
  for (const schedule of schedules) {
    const card = automationEl("article", "automation-card" + (schedule.enabled ? "" : " is-off"));
    const head = automationEl("div", "automation-card-head");
    head.appendChild(automationEl("h3", "automation-card-title", schedule.title || schedule.prompt || "未命名定时"));
    const state = automationScheduleState(schedule);
    head.appendChild(automationEl("span", "automation-chip " + state.cls, state.text));
    card.appendChild(head);
    card.appendChild(automationFact("重复", automationScheduleSummary(schedule)));
    card.appendChild(automationFact("下次", automationFormatDate(schedule.next_run_at)));
    if (schedule.notify) card.appendChild(automationFact("通知", "完成后提醒"));
    if (schedule.title && schedule.prompt) card.appendChild(automationFact("内容", schedule.prompt));
    const actions = automationEl("div", "automation-card-actions");
    actions.appendChild(automationActionButton("立即运行", () => automationRunSchedule(schedule)));
    actions.appendChild(automationActionButton("编辑", () => automationOpenScheduleEditor(schedule)));
    actions.appendChild(automationActionButton(schedule.enabled ? "暂停" : "恢复",
      () => automationToggleSchedule(schedule)));
    actions.appendChild(automationActionButton("删除", () => automationDeleteSchedule(schedule), true));
    card.appendChild(actions);
    box.appendChild(card);
  }
}

function automationRenderWatches(watches) {
  const box = $("automation-watches");
  const count = $("automation-watch-count");
  if (!box) return;
  count.textContent = "· " + watches.length;
  const signature = JSON.stringify(watches);
  if (signature === automationView.watchSignature) return;
  automationView.watchSignature = signature;
  box.replaceChildren();
  if (!watches.length) {
    box.appendChild(automationEl("p", "automation-empty", "还没有网站监听。"));
    return;
  }
  for (const watch of watches) {
    const card = automationEl("article", "automation-card" + (watch.enabled ? "" : " is-off"));
    const head = automationEl("div", "automation-card-head");
    head.appendChild(automationEl("h3", "automation-card-title", watch.title || watch.url || "未命名网站"));
    head.appendChild(automationEl("span", "automation-chip " +
      (watch.enabled ? "automation-chip-on" : "automation-chip-paused"),
      watch.enabled ? "监听中" : "暂停"));
    card.appendChild(head);
    card.appendChild(automationFact("网址", watch.url || "—"));
    card.appendChild(automationFact("间隔", "每 " + (watch.interval_minutes || "?") + " 分钟"));
    card.appendChild(automationFact("上次检查", automationFormatDate(watch.last_checked_at)));
    card.appendChild(automationFact("下次检查", automationFormatDate(watch.next_check_at)));
    if (watch.error) {
      const details = document.createElement("details");
      details.className = "automation-details";
      details.appendChild(automationEl("summary", "", "查看检查错误"));
      details.appendChild(automationEl("p", "automation-details-text is-error", watch.error));
      card.appendChild(details);
    }
    const actions = automationEl("div", "automation-card-actions");
    actions.appendChild(automationActionButton("立即检查", () => automationCheckWatch(watch)));
    actions.appendChild(automationActionButton("编辑", () => automationOpenWatchEditor(watch)));
    actions.appendChild(automationActionButton(watch.enabled ? "暂停" : "恢复",
      () => automationToggleWatch(watch)));
    actions.appendChild(automationActionButton("删除", () => automationDeleteWatch(watch), true));
    card.appendChild(actions);
    box.appendChild(card);
  }
}

function automationRunStatus(run) {
  if (run.status === "failed") return { text: "失败", cls: "automation-chip-failed" };
  if (run.status === "running") return { text: "执行中", cls: "automation-chip-on" };
  if (run.status === "pending") return { text: "待执行", cls: "" };
  if (run.status === "done") {
    return run.reply ? { text: "已回复", cls: "automation-chip-done" } :
      { text: "静默", cls: "automation-chip-done" };
  }
  return { text: run.status || "活动", cls: "" };
}

function automationRenderRuns(runs) {
  const box = $("automation-runs");
  const count = $("automation-run-count");
  if (!box) return;
  count.textContent = "· " + runs.length;
  const signature = JSON.stringify(runs);
  if (signature === automationView.runSignature) return;
  automationView.runSignature = signature;
  box.replaceChildren();
  if (!runs.length) {
    box.appendChild(automationEl("p", "automation-empty", "还没有活动。"));
    return;
  }
  for (const run of runs) {
    const unread = automationIsUnread(run);
    const card = automationEl("article", "automation-card" + (unread ? " is-unread" : ""));
    const head = automationEl("div", "automation-card-head");
    head.appendChild(automationEl("h3", "automation-card-title", automationRunTitle(run)));
    const status = automationRunStatus(run);
    head.appendChild(automationEl("span", "automation-chip " + status.cls, status.text));
    card.appendChild(head);
    card.appendChild(automationFact("时间", automationFormatDate(run.finished_at || run.created_at)));
    const reasons = Array.isArray(run.reasons) ? run.reasons : [];
    if (reasons.length) {
      const list = document.createElement("ul");
      list.className = "automation-reasons";
      for (const reason of reasons.slice(0, 4)) {
        list.appendChild(automationEl("li", "", automationReasonText(reason)));
      }
      card.appendChild(list);
    }
    if (run.summary || run.reply || run.error) {
      const details = document.createElement("details");
      details.className = "automation-details";
      details.open = true;
      details.appendChild(automationEl("summary", "", "摘要、回复与错误"));
      if (run.reply) {
        details.appendChild(automationEl("div", "automation-fact-label", "回复"));
        details.appendChild(automationEl("p", "automation-details-text", run.reply));
      }
      if (run.summary) {
        details.appendChild(automationEl("div", "automation-fact-label", run.reply ? "摘要" : "静默摘要"));
        details.appendChild(automationEl("p", "automation-details-text", run.summary));
      }
      if (run.error) {
        details.appendChild(automationEl("div", "automation-fact-label", "错误"));
        details.appendChild(automationEl("p", "automation-details-text is-error", run.error));
      }
      card.appendChild(details);
    }
    if (unread) {
      const actions = automationEl("div", "automation-card-actions");
      actions.appendChild(automationEl("span", "automation-chip automation-chip-on", "未读"));
      actions.appendChild(automationActionButton("标为已读", () => automationMarkRead(run.id)));
      card.appendChild(actions);
    }
    box.appendChild(card);
  }
}

async function automationAction(action) {
  automationView.mutating += 1;
  try {
    const value = await action();
    automationClearError();
    if (value && typeof value === "object" && value.settings) {
      automationApplySnapshot(value);
    } else {
      await automationPoll(true);
    }
    return value;
  } catch (error) {
    automationShowError(error && error.message ? error.message : String(error));
    return null;
  } finally {
    automationView.mutating -= 1;
  }
}

async function automationPoll(force) {
  if (automationView.polling || automationView.mutating) return;
  if (!force && document.hidden) return;
  automationView.polling = true;
  try {
    const value = await api("/api/automation");
    automationClearError();
    automationApplySnapshot(value);
  } catch (error) {
    if (!automationView.snapshot) {
      automationShowError("暂时无法读取定时与监听：" + error.message);
    } else {
      $("automation-running").textContent = "状态暂时无法更新";
    }
  } finally {
    automationView.polling = false;
  }
}

function automationArmPolling() {
  if (automationView.pollTimer) clearTimeout(automationView.pollTimer);
  automationView.pollTimer = setTimeout(async () => {
    automationView.pollTimer = null;
    await automationPoll();
    automationArmPolling();
  }, 3000);
}

function automationOpen(open) {
  const next = typeof open === "boolean" ? open : !automationView.open;
  if (next && !automationView.open && typeof responsibilitiesView !== "undefined" &&
      responsibilitiesView.open) {
    // The goals panel hides the conversation. Restore the persistent Ling entry
    // before showing this panel so closing it leaves a usable composer.
    clearResponsibilitiesView();
    openLing({keepAutomation: true}).catch((error) => setStatus(error.message, true));
  }
  automationView.open = next;
  const region = $("automation-region");
  if (region) region.hidden = !next;
  const toggle = $("automation-open");
  if (toggle) toggle.setAttribute("aria-expanded", String(next));
  if (next) {
    const notifications = $("notification-region");
    if (notifications) notifications.hidden = true;
    const notificationsToggle = $("notifications-toggle");
    if (notificationsToggle) notificationsToggle.setAttribute("aria-expanded", "false");
    automationPoll(true);
  }
}

function automationUpdateScheduleFields() {
  const kind = $("automation-schedule-kind").value;
  const form = $("automation-schedule-form");
  if (!form) return;
  for (const node of form.querySelectorAll("[data-when]")) {
    const when = node.getAttribute("data-when");
    const show = when === "once" ? kind === "once" :
      when === "interval" ? kind === "interval" :
      when === "time" ? kind === "daily" || kind === "weekly" :
      when === "weekday" ? kind === "weekly" : false;
    node.hidden = !show;
    node.style.display = show ? "" : "none";
  }
}

function automationOpenScheduleEditor(schedule) {
  automationView.editingSchedule = schedule || null;
  const form = $("automation-schedule-form");
  form.hidden = false;
  $("automation-schedule-error").textContent = "";
  $("automation-schedule-title").value = schedule ? schedule.title || "" : "";
  $("automation-schedule-prompt").value = schedule ? schedule.prompt || "" : "";
  $("automation-schedule-kind").value = schedule ? schedule.kind || "once" : "once";
  $("automation-schedule-time").value = schedule && schedule.time ? schedule.time : "09:00";
  $("automation-schedule-minutes").value = schedule && schedule.interval_minutes ?
    schedule.interval_minutes : 60;
  $("automation-schedule-due").value = schedule && schedule.due_at ?
    automationLocalInput(schedule.due_at) : automationDefaultDue();
  $("automation-schedule-notify").checked = Boolean(schedule && schedule.notify);
  const days = schedule ? automationWeekdays(schedule.weekdays) : [];
  const selected = days.length ? days : AUTOMATION_WEEKDAYS.map((_, day) => day);
  for (const checkbox of form.querySelectorAll(".automation-weekdays input[type=checkbox]")) {
    checkbox.checked = selected.includes(Number(checkbox.value));
  }
  automationUpdateScheduleFields();
  form.scrollIntoView({ block: "nearest" });
}

function automationHideScheduleEditor() {
  automationView.editingSchedule = null;
  $("automation-schedule-form").hidden = true;
  $("automation-schedule-error").textContent = "";
}

function automationOpenWatchEditor(watch) {
  automationView.editingWatch = watch || null;
  const form = $("automation-watch-form");
  form.hidden = false;
  $("automation-watch-error").textContent = "";
  $("automation-watch-url").value = watch ? watch.url || "" : "";
  $("automation-watch-title").value = watch ? watch.title || "" : "";
  $("automation-watch-minutes").value = watch && watch.interval_minutes ? watch.interval_minutes : 5;
  form.scrollIntoView({ block: "nearest" });
}

function automationHideWatchEditor() {
  automationView.editingWatch = null;
  $("automation-watch-form").hidden = true;
  $("automation-watch-error").textContent = "";
}

async function automationSaveSettings(event) {
  event.preventDefault();
  const interval = automationNumber($("automation-interval-minutes").value, null);
  const watchInterval = automationNumber($("automation-watch-interval").value, null);
  if (!interval || !watchInterval) {
    $("automation-settings-status").textContent = "间隔必须是正整数分钟。";
    return;
  }
  const planningTime = $("automation-planning-time").value || "09:00";
  const body = {
    enabled: $("automation-enabled").checked,
    interval_enabled: $("automation-interval-enabled").checked,
    interval_minutes: interval,
    planning_enabled: $("automation-planning-enabled").checked,
    planning_time: planningTime,
    watch_interval_minutes: watchInterval,
  };
  $("automation-settings-status").textContent = "保存中…";
  const value = await automationAction(() => post("/api/automation/settings", body));
  if (!value) {
    $("automation-settings-status").textContent = "保存失败";
    return;
  }
  automationView.settingsDirty = false;
  automationView.settingsSignature = "";
  automationApplySnapshot(value);
  $("automation-settings-status").textContent = "已保存";
}

async function automationSaveSchedule(event) {
  event.preventDefault();
  const existing = automationView.editingSchedule;
  const title = $("automation-schedule-title").value.trim();
  const prompt = $("automation-schedule-prompt").value.trim();
  const kind = $("automation-schedule-kind").value;
  const error = $("automation-schedule-error");
  error.textContent = "";
  if (!prompt && (!existing || existing.purpose === "task")) {
    error.textContent = "请填写交给灵的内容。";
    return;
  }
  const body = {
    title: title || prompt.split("\n")[0].slice(0, 80),
    prompt: prompt,
    kind: kind,
    notify: $("automation-schedule-notify").checked,
    origin: existing && existing.origin ? existing.origin : "user",
    time: null,
    weekdays: [],
    interval_minutes: null,
    due_at: null,
  };
  if (existing) body.id = existing.id;
  let enabled = existing ? Boolean(existing.enabled) : true;
  if (existing && existing.kind === "once" && !existing.enabled && !existing.next_run_at) {
    enabled = true;
  }
  if (kind === "once") {
    const due = new Date($("automation-schedule-due").value);
    if (Number.isNaN(due.getTime())) {
      error.textContent = "请选择日期时间。";
      return;
    }
    if (due.getTime() <= Date.now()) {
      error.textContent = "请选择将来的时间。";
      return;
    }
    body.due_at = due.toISOString();
  } else if (kind === "interval") {
    const minutes = automationNumber($("automation-schedule-minutes").value, null);
    if (!minutes) {
      error.textContent = "间隔必须是正整数分钟。";
      return;
    }
    body.interval_minutes = minutes;
  } else {
    body.time = $("automation-schedule-time").value || "09:00";
    if (kind === "weekly") {
      const days = [...$("automation-schedule-form")
        .querySelectorAll(".automation-weekdays input[type=checkbox]:checked")]
        .map((checkbox) => Number(checkbox.value));
      if (!days.length) {
        error.textContent = "请至少选择一个星期。";
        return;
      }
      body.weekdays = days;
    }
  }
  body.enabled = enabled;
  const value = await automationAction(() => post("/api/automation/schedules", body));
  if (!value) return;
  automationHideScheduleEditor();
}

async function automationSaveWatch(event) {
  event.preventDefault();
  const existing = automationView.editingWatch;
  const url = $("automation-watch-url").value.trim();
  const title = $("automation-watch-title").value.trim();
  const minutes = automationNumber($("automation-watch-minutes").value, null);
  const error = $("automation-watch-error");
  error.textContent = "";
  if (!url) {
    error.textContent = "请填写网址。";
    return;
  }
  try {
    const parsed = new URL(url);
    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error("协议");
  } catch (_error) {
    error.textContent = "请填写 http 或 https 网址。";
    return;
  }
  if (!minutes) {
    error.textContent = "间隔必须是正整数分钟。";
    return;
  }
  const body = {
    title: title,
    url: url,
    interval_minutes: minutes,
    enabled: existing ? Boolean(existing.enabled) : true,
    origin: existing && existing.origin ? existing.origin : "user",
  };
  if (existing) body.id = existing.id;
  const value = await automationAction(() => post("/api/automation/watches", body));
  if (!value) return;
  automationHideWatchEditor();
}

async function automationRunSchedule(schedule) {
  await automationAction(() => post(
    "/api/automation/schedules/" + encodeURIComponent(schedule.id) + "/run", {}));
}

async function automationToggleSchedule(schedule) {
  await automationAction(() => post("/api/automation/schedules", {
    id: schedule.id, enabled: !schedule.enabled,
  }));
}

async function automationDeleteSchedule(schedule) {
  if (!window.confirm("删除这个定时？")) return;
  const value = await automationAction(() => post(
    "/api/automation/schedules/" + encodeURIComponent(schedule.id) + "/delete", {}));
  if (value && automationView.editingSchedule && automationView.editingSchedule.id === schedule.id) {
    automationHideScheduleEditor();
  }
}

async function automationCheckWatch(watch) {
  await automationAction(() => post(
    "/api/automation/watches/" + encodeURIComponent(watch.id) + "/check", {}));
}

async function automationToggleWatch(watch) {
  await automationAction(() => post("/api/automation/watches", {
    id: watch.id, enabled: !watch.enabled,
  }));
}

async function automationDeleteWatch(watch) {
  if (!window.confirm("删除这个网站监听？")) return;
  const value = await automationAction(() => post(
    "/api/automation/watches/" + encodeURIComponent(watch.id) + "/delete", {}));
  if (value && automationView.editingWatch && automationView.editingWatch.id === watch.id) {
    automationHideWatchEditor();
  }
}

async function automationMarkRead(id) {
  await automationAction(() => post(
    "/api/automation/runs/" + encodeURIComponent(id) + "/read", {}));
}

function automationInit() {
  if (automationView.initialized) return;
  automationView.initialized = true;
  automationOn("automation-open", "click", () => automationOpen(!automationView.open));
  automationOn("automation-close", "click", () => automationOpen(false));
  automationOn("automation-refresh", "click", () => automationPoll(true));
  automationOn("automation-settings-form", "submit", automationSaveSettings);
  for (const id of [
    "automation-enabled", "automation-interval-enabled", "automation-interval-minutes",
    "automation-planning-enabled", "automation-planning-time", "automation-watch-interval",
  ]) {
    automationOn(id, "input", () => {
      automationView.settingsDirty = true;
      $("automation-settings-status").textContent = "有未保存的修改";
    });
    automationOn(id, "change", () => {
      automationView.settingsDirty = true;
      $("automation-settings-status").textContent = "有未保存的修改";
    });
  }
  automationOn("automation-schedule-add", "click", () => automationOpenScheduleEditor(null));
  automationOn("automation-schedule-cancel", "click", automationHideScheduleEditor);
  automationOn("automation-schedule-form", "submit", automationSaveSchedule);
  automationOn("automation-schedule-kind", "change", automationUpdateScheduleFields);
  automationOn("automation-watch-add", "click", () => automationOpenWatchEditor(null));
  automationOn("automation-watch-cancel", "click", automationHideWatchEditor);
  automationOn("automation-watch-form", "submit", automationSaveWatch);
  automationOn("automation-toast-close", "click", automationHideToast);
  automationOn("automation-toast-view", "click", () => {
    const id = automationView.toastRunId;
    automationHideToast();
    automationOpen(true);
    if (id) automationMarkRead(id);
  });
  automationOn("notifications-toggle", "click", () => automationOpen(false));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) automationPoll();
  });
  automationPoll();
  automationArmPolling();
}

window.automationOpen = automationOpen;

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", automationInit, { once: true });
} else {
  automationInit();
}
