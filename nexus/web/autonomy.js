"use strict";

// Owner-facing autonomy controls. This module has its own snapshot poll and
// unread state; autonomy messages are not task notifications. It renders the
// records the backend already decided, without exposing internal reasoning.

const autonomyView = {
  snapshot: null,
  version: 0,
  initialized: false,
  polling: false,
  pollTimer: null,
  open: false,
  submitting: false,
  conflict: false,
  error: "",
  settingsError: "",
  settingsStatus: "",
  seenMessages: new Set(),
  hydratedMessages: false,
  toastMessage: null,
  toastDismissed: new Set(),
  settingsDirty: false,
  settingsRendered: "",
  settingsExpanded: false,
  settingsInitialized: false,
  sourceViews: new Map(),
};

const autonomyControlLabels = {
  disabled: "未启用",
  enabled: "运行中",
  paused: "已暂停",
  terminated: "已终止",
};

const autonomyGoalLabels = {
  candidate: "候选事项",
  active: "进行中",
  investigating: "调查中",
  waiting: "等待中",
  paused: "已暂停",
  completed: "已完成",
  withdrawn: "已撤回",
  abandoned: "已放弃",
  failed: "失败",
};

function autonomyArray(value) {
  return Array.isArray(value) ? value : [];
}

function autonomyText(value) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
}

function autonomyDate(value) {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function autonomyMinutes(seconds, allowZero) {
  const value = autonomyNumber(seconds);
  if (value === null) return "";
  if (!allowZero && value <= 0) return "";
  const minutes = value / 60;
  return String(Number(minutes.toFixed(6)));
}

function autonomySeconds(minutes, allowZero) {
  const text = String($(minutes).value || "").trim();
  if (!text) return null;
  const value = Number(text);
  if (!Number.isFinite(value)) return null;
  if (allowZero && value === 0) return 0;
  if (!allowZero && value <= 0) return 0;
  return Math.max(1, Math.round(value * 60));
}

function autonomyNumber(value) {
  if (value === null || value === undefined || value === "" || value === "unknown") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function autonomyToken(value) {
  const number = autonomyNumber(value);
  if (number === null) return "未知";
  return Math.max(0, Math.round(number)).toLocaleString("zh-CN");
}

function autonomyState() {
  return autonomyView.snapshot?.control?.state || "disabled";
}

function autonomyStateLabel(state) {
  return autonomyControlLabels[state] || state || "状态未知";
}

function autonomySetText(id, value) {
  const node = $(id);
  if (node) node.textContent = value === undefined || value === null ? "" : String(value);
}

function autonomyShowError(message) {
  autonomyView.error = message || "请求失败";
  const node = $("autonomy-error");
  if (node) {
    node.textContent = autonomyView.error;
    node.hidden = !autonomyView.error;
  }
  if (typeof setStatus === "function") setStatus(autonomyView.error, true);
}

function autonomyClearError() {
  autonomyView.error = "";
  const node = $("autonomy-error");
  if (node) {
    node.textContent = "";
    node.hidden = true;
  }
}

function autonomyShowConflict() {
  autonomyView.conflict = true;
  const node = $("autonomy-conflict");
  if (node) node.hidden = false;
}

function autonomyClearConflict() {
  autonomyView.conflict = false;
  const node = $("autonomy-conflict");
  if (node) node.hidden = true;
}

function autonomyIsConflict(error) {
  return /\b409\b|conflict|version|stale|已过期|版本/i.test(String(error?.message || error || ""));
}

function autonomyPayload(snapshot) {
  return snapshot?.snapshot && snapshot.snapshot.control ? snapshot.snapshot : snapshot;
}

function autonomyApplySnapshot(value) {
  const snapshot = autonomyPayload(value);
  if (!snapshot || typeof snapshot !== "object") return false;
  const incoming = autonomySettingsSignature(snapshot.settings || {});
  if (autonomyView.snapshot && autonomyView.settingsDirty &&
      incoming !== autonomySettingsSignature(autonomyView.snapshot.settings || {})) {
    snapshot.settings = autonomyView.snapshot.settings;
  }
  autonomyView.snapshot = snapshot;
  autonomyView.version = Number(snapshot.control?.version) || autonomyView.version;
  autonomyClearError();
  autonomyRender();
  autonomyObserveMessages();
  return true;
}

function autonomySettingsSignature(settings) {
  return JSON.stringify({
    read_roots: autonomyArray(settings?.read_roots),
    output_root: settings?.output_root || "",
    token_limit: settings?.token_limit,
    max_output_tokens: settings?.max_output_tokens,
    fallback_seconds: settings?.fallback_seconds,
    contact_after_seconds: settings?.contact_after_seconds,
    observer_interval: settings?.observer_interval,
    allowed_mcp_tools: autonomyArray(settings?.allowed_mcp_tools),
  });
}

function autonomyFillSettings(force) {
  const settings = autonomyView.snapshot?.settings || {};
  const signature = autonomySettingsSignature(settings);
  if (!force && autonomyView.settingsDirty) return;
  if (!force && autonomyView.settingsRendered === signature && autonomyView.settingsInitialized) return;
  autonomyView.settingsRendered = signature;
  $("autonomy-read-roots").value = autonomyArray(settings.read_roots).join("\n");
  $("autonomy-output-root").value = settings.output_root || "";
  $("autonomy-token-limit").value = settings.token_limit ?? "";
  $("autonomy-max-output-tokens").value = settings.max_output_tokens ?? "";
  $("autonomy-fallback-minutes").value = autonomyMinutes(settings.fallback_seconds, false);
  $("autonomy-contact-minutes").value = autonomyMinutes(settings.contact_after_seconds, true);
  $("autonomy-observer-interval").value = autonomyNumber(settings.observer_interval) === null
    ? "" : String(settings.observer_interval);
  $("autonomy-allowed-mcp-tools").value = autonomyArray(settings.allowed_mcp_tools).join("\n");
  autonomyView.settingsDirty = false;
  if (!autonomyView.settingsInitialized) {
    autonomyView.settingsInitialized = true;
    const section = $("autonomy-settings-section");
    if (section) {
      // Start expanded only when there is no usable configuration to act on.
      // Once initialized, polling never changes the owner's open/closed choice.
      section.open = !(settings.read_roots?.length && settings.output_root && settings.token_limit > 0);
      autonomyView.settingsExpanded = section.open;
      section.addEventListener("toggle", () => {
        autonomyView.settingsExpanded = section.open;
      });
    }
  }
}

function autonomyReadSettings() {
  const lines = (id) => $(id).value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return {
    read_roots: lines("autonomy-read-roots"),
    output_root: $("autonomy-output-root").value.trim(),
    token_limit: Math.max(0, Math.round(Number($("autonomy-token-limit").value) || 0)),
    max_output_tokens: Math.max(0, Math.round(Number($("autonomy-max-output-tokens").value) || 0)),
    fallback_seconds: autonomySeconds("autonomy-fallback-minutes", false),
    contact_after_seconds: autonomySeconds("autonomy-contact-minutes", true),
    observer_interval: autonomyNumber($("autonomy-observer-interval").value),
    allowed_mcp_tools: lines("autonomy-allowed-mcp-tools"),
  };
}

function autonomyValidateSettings(settings) {
  if (!settings.read_roots.length) return "请填写至少一个获准读取的根目录。";
  if (!settings.output_root) return "请填写成果保存目录。";
  if (!settings.token_limit) return "请填写大于 0 的 Token 限额。";
  if (!settings.max_output_tokens || settings.max_output_tokens < 128 || settings.max_output_tokens > 65536) {
    return "单次输出 Token 上限需在 128 至 65536 之间。";
  }
  if (settings.fallback_seconds === null || settings.fallback_seconds <= 0) return "请填写大于 0 的保底回顾间隔。";
  if (settings.contact_after_seconds === null || settings.contact_after_seconds < 0) return "请填写主动联系等待时间，或填 0 表示不设等待。";
  if (!settings.observer_interval || settings.observer_interval < 0.1 || settings.observer_interval > 3600) return "观察检查间隔需在 0.1 至 3600 秒之间。";
  return "";
}

async function autonomySaveSettings(options) {
  const quiet = Boolean(options?.quiet);
  const settings = autonomyReadSettings();
  const problem = autonomyValidateSettings(settings);
  if (problem) {
    autonomyView.settingsError = problem;
    autonomySetText("autonomy-settings-status", problem);
    if (!quiet) autonomyShowError(problem);
    return false;
  }
  autonomyView.submitting = true;
  autonomySetText("autonomy-settings-status", "正在保存…");
  try {
    const result = await post("/api/autonomy/settings", settings);
    autonomyView.settingsError = "";
    autonomyView.settingsStatus = "设置已保存";
    autonomyView.settingsDirty = false;
    autonomySetText("autonomy-settings-status", autonomyView.settingsStatus);
    autonomyApplySnapshot(result);
    if (!autonomyView.snapshot) await autonomyPoll();
    return true;
  } catch (error) {
    autonomyView.settingsError = error.message;
    autonomySetText("autonomy-settings-status", "保存失败：" + error.message);
    if (!quiet) autonomyShowError(error.message);
    return false;
  } finally {
    autonomyView.submitting = false;
    autonomyRenderControls();
  }
}

async function autonomyControl(operation) {
  if (autonomyView.submitting) return false;
  if (!autonomyView.snapshot) await autonomyPoll();
  if (!autonomyView.snapshot) return false;
  if (operation === "enable" && autonomyView.settingsDirty) {
    const saved = await autonomySaveSettings({ quiet: true });
    if (!saved) return false;
  }
  if (autonomyView.conflict) {
    autonomyShowConflict();
    return false;
  }
  if (operation === "terminate" && !confirm("终止自主后，这次配置需要重新启用；已有记录仍会保留。继续吗？")) return false;
  autonomyView.submitting = true;
  autonomyRenderControls();
  try {
    const result = await post("/api/autonomy/control", {
      operation,
      expected_version: Number(autonomyView.snapshot.control?.version) || 0,
    });
    autonomyApplySnapshot(result);
    if (!autonomyView.snapshot) await autonomyPoll();
    if (!result?.control && !result?.snapshot) await autonomyPoll();
    return true;
  } catch (error) {
    if (autonomyIsConflict(error)) autonomyShowConflict();
    else autonomyShowError(error.message);
    return false;
  } finally {
    autonomyView.submitting = false;
    autonomyRenderControls();
  }
}

async function autonomyWithdrawGoal(goal) {
  if (!goal?.id || autonomyView.conflict || autonomyView.submitting) return;
  if (!confirm("撤回「" + (goal.title || "这个事项") + "」？相关计划与观察会随之后端控制生效。")) return;
  autonomyView.submitting = true;
  autonomyRenderGoals();
  try {
    const result = await post("/api/autonomy/goals/" + encodeURIComponent(goal.id) + "/control", {
      operation: "withdraw",
      expected_version: Number(goal.version ?? autonomyView.snapshot.control?.version) || 0,
    });
    autonomyApplySnapshot(result);
    if (!result?.goals && !result?.snapshot) await autonomyPoll();
  } catch (error) {
    if (autonomyIsConflict(error)) autonomyShowConflict();
    else autonomyShowError(error.message);
  } finally {
    autonomyView.submitting = false;
    autonomyRenderGoals();
  }
}

async function autonomyMarkRead(message) {
  if (!message?.id || message.read_at || autonomyView.submitting) return;
  try {
    const result = await post("/api/autonomy/messages/" + encodeURIComponent(message.id) + "/read", {});
    autonomyApplySnapshot(result);
    if (!result?.messages && !result?.snapshot) await autonomyPoll();
  } catch (error) {
    autonomyShowError(error.message);
  }
}

async function autonomyMarkAllRead() {
  if (autonomyView.submitting) return;
  const messages = autonomyArray(autonomyView.snapshot?.messages).filter((item) => !item.read_at);
  for (const message of messages) await autonomyMarkRead(message);
}

async function autonomyReview() {
  if (!autonomyView.snapshot || autonomyView.submitting) return;
  const button = $("autonomy-review");
  if (button) button.disabled = true;
  try {
    await post("/api/autonomy/review", { reason: "owner_requested" });
    autonomyView.settingsStatus = "已请求灵考虑一次；是否行动仍由灵判断。";
    autonomySetText("autonomy-settings-status", autonomyView.settingsStatus);
    await autonomyPoll();
  } catch (error) {
    autonomyShowError(error.message);
  } finally {
    if (button) button.disabled = false;
  }
}

function autonomySourceId(source) {
  if (typeof source === "string") return source;
  if (!source || typeof source !== "object") return "";
  const candidate = source.id || source.source_id || source.event_id || "";
  if (candidate) return candidate;
  if (source.path || source.uri || source.url) return source.path || source.uri || source.url;
  return "";
}

function autonomySourceLabel(source) {
  if (typeof source === "object" && source) {
    let label = source.title || source.label || source.name || source.path || source.uri || source.url || source.id || "来源";
    if (source.reason) label += " · " + source.reason;
    return label;
  }
  return autonomyText(source);
}

function autonomySourceTitle(source) {
  if (typeof source === "object" && source) {
    return source.title || source.label || source.name || source.path || source.uri || source.url || "依据";
  }
  return String(source || "依据");
}

function autonomySourceView(id) {
  if (!autonomyView.sourceViews.has(id)) {
    autonomyView.sourceViews.set(id, { open: false, content: "", error: "", loading: false, next: 0, total: null });
  }
  return autonomyView.sourceViews.get(id);
}

async function autonomyLoadSource(id, offset) {
  const view = autonomySourceView(id);
  const next = Number(offset ?? view.next ?? 0) || 0;
  if (view.loading) return;
  view.loading = true;
  view.error = "";
  autonomyRefreshSourceViews(id);
  try {
    const result = await api("/api/autonomy/sources/" + encodeURIComponent(id) +
      "?offset=" + encodeURIComponent(next) + "&limit=8000");
    if (result.id && result.id !== id) throw new Error("返回的依据与请求不一致");
    const content = typeof result.content === "string" ? result.content : autonomyText(result.content);
    view.content += content;
    view.next = result.next_offset === null || result.next_offset === undefined
      ? null : Number(result.next_offset);
    view.total = Number.isFinite(Number(result.total_chars)) ? Number(result.total_chars) : null;
    if (result.kind && !view.kind) view.kind = result.kind;
    if (result.at && !view.at) view.at = result.at;
  } catch (error) {
    view.error = error.message || "依据暂时无法读取";
  } finally {
    view.loading = false;
    autonomyRefreshSourceViews(id);
  }
}

function autonomySourceDetail(view) {
  const details = el("div", "autonomy-source-detail");
  const meta = [view.kind, view.at ? autonomyDate(view.at) : ""].filter(Boolean);
  if (meta.length) details.appendChild(el("div", "autonomy-source-meta", meta.join(" · ")));
  if (view.error) details.appendChild(el("p", "autonomy-source-error", "读取失败：" + view.error));
  if (view.content) {
    const pre = el("pre", "autonomy-source-content");
    pre.textContent = view.content;
    details.appendChild(pre);
  } else if (!view.error && view.loading) {
    details.appendChild(el("p", "autonomy-source-loading", "正在读取依据…"));
  } else if (!view.error) {
    details.appendChild(el("p", "autonomy-source-empty", "这份依据没有可显示的文本。"));
  }
  const foot = el("div", "autonomy-source-foot");
  if (view.total !== null) {
    const shown = view.content.length;
    foot.appendChild(el("span", "autonomy-source-progress", shown + " / " + view.total + " 字符"));
  }
  if (view.next !== null && view.next !== undefined) {
    const more = el("button", "autonomy-inline-button", "下一页");
    more.type = "button";
    more.disabled = view.loading;
    more.addEventListener("click", () => autonomyLoadSource(view.id, view.next));
    foot.appendChild(more);
  }
  if (view.error) {
    const retry = el("button", "autonomy-inline-button", "重试");
    retry.type = "button";
    retry.disabled = view.loading;
    retry.addEventListener("click", () => autonomyLoadSource(view.id, view.next || 0));
    foot.appendChild(retry);
  }
  if (foot.children.length) details.appendChild(foot);
  return details;
}

function autonomyRefreshSourceViews(id) {
  const view = autonomySourceView(String(id));
  const details = view.element;
  if (!details) return;
  details.open = Boolean(view.open);
  const content = details.querySelector(".autonomy-source-detail");
  if (content) content.replaceWith(autonomySourceDetail(view));
}

function autonomySources(parent, sources, className) {
  const list = autonomyArray(sources);
  if (!list.length) return;
  const box = el("div", className || "autonomy-sources");
  for (const source of list) {
    if (!source) continue;
    const id = autonomySourceId(source);
    if (typeof id === "string" && /^event:\d+$/.test(id)) {
      const view = autonomySourceView(id);
      view.id = id;
      const details = el("details", "autonomy-source-event");
      view.element = details;
      details.dataset.autonomySource = id;
      details.open = Boolean(view.open);
      details.addEventListener("toggle", () => {
        const current = autonomySourceView(id);
        current.open = details.open;
        if (details.open && !current.content && !current.loading && !current.error) {
          autonomyLoadSource(id, current.next || 0);
        }
      });
      const summary = el("summary", "autonomy-source-summary");
      summary.appendChild(el("span", "autonomy-source-name", autonomySourceTitle(source)));
      summary.appendChild(el("span", "autonomy-source-view-label", "查看依据"));
      details.appendChild(summary);
      details.appendChild(autonomySourceDetail(view));
      box.appendChild(details);
    } else {
      box.appendChild(el("span", "autonomy-source", autonomySourceLabel(source)));
    }
  }
  if (box.children.length) parent.appendChild(box);
}

function autonomyFact(parent, label, value) {
  const text = autonomyText(value).trim();
  if (!text) return;
  const row = el("div", "autonomy-fact");
  row.appendChild(el("span", "autonomy-fact-label", label));
  row.appendChild(el("span", "autonomy-fact-value", text));
  parent.appendChild(row);
}

function autonomyStatusChip(status) {
  return el("span", "autonomy-chip autonomy-chip-" + String(status || "unknown").replace(/[^\w-]/g, ""),
    autonomyGoalLabels[status] || autonomyText(status) || "状态未知");
}

function autonomyRenderBudget() {
  const box = $("autonomy-budget");
  if (!box) return;
  box.innerHTML = "";
  const budget = autonomyView.snapshot?.budget || {};
  const values = [
    ["限额", budget.limit], ["已用", budget.spent], ["预留", budget.reserved],
    ["剩余", budget.remaining], ["未知", budget.unknown],
  ];
  for (const [label, value] of values) {
    const item = el("div", "autonomy-budget-item");
    item.appendChild(el("span", "autonomy-budget-label", label));
    item.appendChild(el("span", "autonomy-budget-value", autonomyToken(value)));
    box.appendChild(item);
  }
}

function autonomyRenderControls() {
  const state = autonomyState();
  const control = autonomyView.snapshot?.control || {};
  autonomySetText("autonomy-state", autonomyStateLabel(state));
  autonomySetText("autonomy-state-short", autonomyStateLabel(state));
  const running = Boolean(autonomyView.snapshot?.running);
  autonomySetText("autonomy-running", running ? "正在考虑或活动" : "当前静候");
  const buttonState = {
    enable: state === "disabled" || state === "terminated",
    pause: state === "enabled",
    resume: state === "paused",
    terminate: state === "enabled" || state === "paused",
  };
  for (const [name, enabled] of Object.entries(buttonState)) {
    const button = $("autonomy-" + name);
    if (button) button.disabled = autonomyView.submitting || !enabled;
  }
  const review = $("autonomy-review");
  if (review) review.disabled = autonomyView.submitting;
  const settings = $("autonomy-save-settings");
  if (settings) settings.disabled = autonomyView.submitting;
  const hint = {
    disabled: "尚未授予自主读取或活动权限。",
    enabled: control.enabled_at ? "自 " + autonomyDate(control.enabled_at) + " 启用" : "已启用",
    paused: "暂停期间不会发起新的自主活动。",
    terminated: "本次自主已终止；重新启用需要明确操作。",
  }[state] || "";
  autonomySetText("autonomy-control-hint", hint);
  const toggle = $("autonomy-toggle");
  if (toggle) {
    toggle.classList.toggle("is-enabled", state === "enabled");
    toggle.classList.toggle("is-paused", state === "paused");
    toggle.classList.toggle("is-terminated", state === "terminated");
  }
}

function autonomyRenderSummary() {
  const box = $("autonomy-summary");
  if (!box) return;
  box.innerHTML = "";
  const snapshot = autonomyView.snapshot;
  if (!snapshot) {
    box.appendChild(el("p", "autonomy-empty", "正在读取自主状态…"));
    return;
  }
  if (snapshot.error) {
    const error = el("p", "autonomy-error autonomy-error-inline");
    error.textContent = snapshot.error;
    box.appendChild(error);
  }
  const goals = autonomyArray(snapshot.goals).filter((item) => item.status !== "withdrawn");
  const watches = autonomyArray(snapshot.watches);
  const requests = autonomyArray(snapshot.requests).filter((item) => item.status !== "answered");
  const messages = autonomyArray(snapshot.messages).filter((item) => !item.read_at);
  const summary = el("div", "autonomy-summary-line");
  summary.appendChild(el("span", "autonomy-summary-main",
    goals.length ? goals.length + " 个关注事项" : "暂无自主关注事项"));
  const parts = [];
  if (watches.length) parts.push(watches.length + " 项观察");
  if (requests.length) parts.push(requests.length + " 项待回答");
  if (messages.length) parts.push(messages.length + " 条未读消息");
  if (parts.length) summary.appendChild(el("span", "autonomy-summary-sub", parts.join(" · ")));
  box.appendChild(summary);
}

function autonomyRenderGoals() {
  const box = $("autonomy-goals");
  if (!box) return;
  box.innerHTML = "";
  const goals = autonomyArray(autonomyView.snapshot?.goals);
  autonomySetText("autonomy-goal-count", goals.length ? String(goals.length) : "");
  if (!goals.length) {
    box.appendChild(el("p", "autonomy-empty", "灵还没有形成自主关注事项。"));
    return;
  }
  for (const goal of goals) {
    const card = el("article", "autonomy-card");
    const head = el("div", "autonomy-card-head");
    head.appendChild(el("h4", "autonomy-card-title", goal.title || "未命名关注事项"));
    head.appendChild(autonomyStatusChip(goal.status));
    card.appendChild(head);
    autonomyFact(card, "问题", goal.question);
    autonomyFact(card, "依据", goal.reason);
    autonomyFact(card, "目前认识", goal.understanding);
    autonomySources(card, goal.sources);
    const actions = el("div", "autonomy-card-actions");
    const withdrawn = goal.status === "withdrawn";
    const button = el("button", "autonomy-link-danger", withdrawn ? "已撤回" : "撤回事项");
    button.type = "button";
    button.disabled = withdrawn || autonomyView.submitting || autonomyView.conflict;
    button.addEventListener("click", () => autonomyWithdrawGoal(goal));
    actions.appendChild(button);
    card.appendChild(actions);
    box.appendChild(card);
  }
}

function autonomyRenderPlans() {
  const box = $("autonomy-plans");
  if (!box) return;
  box.innerHTML = "";
  const plans = autonomyArray(autonomyView.snapshot?.plans);
  autonomySetText("autonomy-plan-count", plans.length ? String(plans.length) : "");
  if (!plans.length) {
    box.appendChild(el("p", "autonomy-empty", "暂无额外回顾安排；仍可能按保底机会重新考虑。"));
    return;
  }
  for (const plan of plans) {
    const row = el("div", "autonomy-row");
    const head = el("div", "autonomy-row-head");
    head.appendChild(el("span", "autonomy-row-time", autonomyDate(plan.due_at)));
    head.appendChild(autonomyStatusChip(plan.status));
    row.appendChild(head);
    autonomyFact(row, "原因", plan.reason);
    if (plan.goal_id) autonomyFact(row, "关联事项", plan.goal_id);
    box.appendChild(row);
  }
}

function autonomyRenderWatches() {
  const box = $("autonomy-watches");
  if (!box) return;
  box.innerHTML = "";
  const watches = autonomyArray(autonomyView.snapshot?.watches);
  const observer = autonomyView.snapshot?.observer || {};
  autonomySetText("autonomy-watch-count", watches.length ? String(watches.length) : "");
  const observerRow = el("div", "autonomy-observer" + (observer.connected ? " is-connected" : ""));
  observerRow.appendChild(el("span", "autonomy-observer-dot", observer.connected ? "●" : "○"));
  observerRow.appendChild(el("span", "autonomy-observer-text",
    observer.connected ? "观察服务已连接" : "观察服务未连接"));
  if (observer.error) observerRow.appendChild(el("span", "autonomy-observer-error", observer.error));
  box.appendChild(observerRow);
  if (!watches.length) {
    box.appendChild(el("p", "autonomy-empty", "没有实际观察规则。"));
    return;
  }
  for (const watch of watches) {
    const row = el("div", "autonomy-row");
    const head = el("div", "autonomy-row-head");
    head.appendChild(el("span", "autonomy-row-title", watch.path || watch.title || watch.target || watch.name || "观察"));
    head.appendChild(autonomyStatusChip(watch.status || watch.state || "unknown"));
    row.appendChild(head);
    autonomyFact(row, "原因", watch.reason);
    autonomyFact(row, "范围", watch.recursive ? "递归" : "仅此对象");
    autonomyFact(row, "观察状态", watch.applied_version !== undefined ? "已生效" : "尚未确认生效");
    const actual = watch.actual || {};
    autonomyFact(row, "最近确认", autonomyDate(actual.checked_at || watch.checked_at || watch.last_checked_at || watch.updated_at));
    if (actual.error || watch.error) autonomyFact(row, "状态说明", actual.error || watch.error);
    box.appendChild(row);
  }
}

function autonomyRenderDecisions() {
  const box = $("autonomy-decisions");
  if (!box) return;
  box.innerHTML = "";
  const decisions = autonomyArray(autonomyView.snapshot?.decisions);
  autonomySetText("autonomy-decision-count", decisions.length ? String(decisions.length) : "");
  if (!decisions.length) {
    box.appendChild(el("p", "autonomy-empty", "还没有可查看的自主决定。"));
    return;
  }
  for (const decision of decisions) {
    const row = el("div", "autonomy-row");
    const head = el("div", "autonomy-row-head");
    head.appendChild(el("span", "autonomy-row-title", decision.action || decision.decision || decision.title || "决定"));
    head.appendChild(el("span", "autonomy-row-time", autonomyDate(decision.at)));
    row.appendChild(head);
    autonomyFact(row, "原因", decision.reason);
    autonomySources(row, decision.sources);
    box.appendChild(row);
  }
}

function autonomyRenderActions() {
  const box = $("autonomy-actions");
  if (!box) return;
  box.innerHTML = "";
  const actions = autonomyArray(autonomyView.snapshot?.actions);
  autonomySetText("autonomy-action-count", actions.length ? String(actions.length) : "");
  if (!actions.length) {
    box.appendChild(el("p", "autonomy-empty", "灵还没有开展自主工作。"));
    return;
  }
  for (const action of actions) {
    const row = el("div", "autonomy-row");
    const head = el("div", "autonomy-row-head");
    head.appendChild(el("span", "autonomy-row-title", action.title || action.goal || "自主工作"));
    head.appendChild(autonomyStatusChip(action.status));
    row.appendChild(head);
    autonomyFact(row, "依据", action.goal);
      autonomyFact(row, "停止原因", action.stop_reason);
    if (action.task_id) {
      const open = el("button", "link-button", "打开关联任务");
      open.type = "button";
      open.addEventListener("click", () => {
        autonomyOpen(false);
        if (typeof openTask === "function") openTask(action.task_id).catch((error) => autonomyShowError(error.message));
      });
      row.appendChild(open);
    }
    box.appendChild(row);
  }
}

function autonomyMessageKey(message) {
  return message?.id === undefined || message?.id === null ? "" : String(message.id);
}

function autonomyUnreadCount() {
  return autonomyArray(autonomyView.snapshot?.messages).filter((item) => !item.read_at).length;
}

function autonomyRenderMessages() {
  const box = $("autonomy-messages");
  if (!box) return;
  box.innerHTML = "";
  const messages = autonomyArray(autonomyView.snapshot?.messages);
  autonomySetText("autonomy-message-count", messages.length ? String(messages.length) : "");
  if (!messages.length) {
    box.appendChild(el("p", "autonomy-empty", "灵还没有主动发来消息。"));
    return;
  }
  for (const message of messages) {
    const row = el("article", "autonomy-message" + (message.read_at ? " is-read" : " is-unread"));
    const head = el("div", "autonomy-row-head");
    head.appendChild(el("span", "autonomy-row-time", autonomyDate(message.at)));
    if (!message.read_at) head.appendChild(el("span", "autonomy-unread-dot", "未读"));
    row.appendChild(head);
    row.appendChild(el("p", "autonomy-message-text", message.text || ""));
    autonomySources(row, message.sources);
    const actions = el("div", "autonomy-row-actions");
    if (!message.read_at) {
      const read = el("button", "link-button", "标为已读");
      read.type = "button";
      read.addEventListener("click", () => autonomyMarkRead(message));
      actions.appendChild(read);
    }
    if (message.autonomy_message_id || message.id) {
      const view = el("button", "link-button", "在交流中查看");
      view.type = "button";
      view.addEventListener("click", () => autonomyOpenInLing());
      actions.appendChild(view);
    }
    row.appendChild(actions);
    box.appendChild(row);
  }
}

function autonomyRenderRequests() {
  const box = $("autonomy-requests");
  if (!box) return;
  box.innerHTML = "";
  const requests = autonomyArray(autonomyView.snapshot?.requests);
  // The current backend does not expose an autonomy request answer endpoint.
  // Specific out-of-scope actions are answered in their existing durable task
  // UI, so this section only reports recorded requests and never shows a
  // submission button that would have nowhere to go.
  const pending = requests.filter((item) => !["answered", "resolved", "expired", "withdrawn"].includes(item.status));
  autonomySetText("autonomy-request-count", pending.length ? String(pending.length) : "");
  if (!pending.length) {
    box.appendChild(el("p", "autonomy-empty", "没有独立的自主请求。具体外部动作的审批仍在对应任务中处理。"));
    return;
  }
  for (const request of pending) {
    const row = el("article", "autonomy-request");
    const head = el("div", "autonomy-row-head");
    head.appendChild(el("span", "autonomy-row-title", request.title || request.question || "需要你的决定"));
    row.appendChild(head);
    autonomyFact(row, "范围", request.scope);
    autonomyFact(row, "原因", request.reason);
    autonomySources(row, request.sources);
    row.appendChild(el("p", "autonomy-empty", request.task_id
      ? "请在对应持久任务中处理具体审批。"
      : "记录中的请求目前没有独立作答端点；不会在这里伪造已处理状态。"));
    box.appendChild(row);
  }
}

function autonomyRenderBadge() {
  const count = autonomyUnreadCount();
  autonomySetText("autonomy-unread-count", count);
  const badge = $("autonomy-unread-count");
  if (badge) badge.hidden = !count;
  const toggle = $("autonomy-toggle");
  if (toggle) toggle.classList.toggle("has-unread", count > 0);
  autonomyRenderToast();
}

function autonomyRender() {
  autonomyRenderControls();
  autonomyRenderBudget();
  autonomyRenderSummary();
  autonomyRenderGoals();
  autonomyRenderPlans();
  autonomyRenderWatches();
  autonomyRenderDecisions();
  autonomyRenderActions();
  autonomyRenderMessages();
  autonomyRenderRequests();
  autonomyRenderBadge();
  if (autonomyView.snapshot) autonomyFillSettings(false);
}

function autonomyOpen(open) {
  autonomyView.open = Boolean(open);
  const region = $("autonomy-region");
  const toggle = $("autonomy-toggle");
  if (region) region.hidden = !autonomyView.open;
  if (toggle) toggle.setAttribute("aria-expanded", String(autonomyView.open));
  if (autonomyView.open) {
    autonomyRender();
    if (!autonomyView.snapshot) autonomyPoll();
  }
}

function autonomyOpenInLing() {
  autonomyOpen(false);
  if (typeof openLing === "function") {
    openLing().catch((error) => autonomyShowError(error.message));
  } else {
    document.addEventListener("DOMContentLoaded", () => {
      if (typeof openLing === "function") openLing().catch((error) => autonomyShowError(error.message));
    }, { once: true });
  }
}

function autonomyShowToast(message) {
  if (!message || message.read_at || autonomyView.toastDismissed.has(autonomyMessageKey(message))) return;
  autonomyView.toastMessage = message;
  const box = $("autonomy-toast");
  if (!box) return;
  box.hidden = false;
  box.style.display = "";
  autonomySetText("autonomy-toast-text", message.text || "灵有新的主动消息。");
  autonomySetText("autonomy-toast-meta", autonomyDate(message.at));
  const sources = $("autonomy-toast-sources");
  if (sources) {
    sources.innerHTML = "";
    autonomySources(sources, message.sources, "autonomy-toast-source-list");
  }
}

function autonomyRenderToast() {
  const box = $("autonomy-toast");
  if (!box) return;
  const message = autonomyView.toastMessage;
  if (!message) {
    box.hidden = true;
    box.style.display = "none";
    return;
  }
  const current = autonomyArray(autonomyView.snapshot?.messages)
    .find((item) => autonomyMessageKey(item) === autonomyMessageKey(message));
  if (current?.read_at) {
    autonomyView.toastMessage = null;
    box.hidden = true;
    box.style.display = "none";
    return;
  }
  box.hidden = false;
  box.style.display = "";
}

function autonomyHideToast() {
  const message = autonomyView.toastMessage;
  if (message) autonomyView.toastDismissed.add(autonomyMessageKey(message));
  autonomyView.toastMessage = null;
  const box = $("autonomy-toast");
  if (box) {
    box.hidden = true;
    box.style.display = "none";
  }
}

function autonomyObserveMessages() {
  const messages = autonomyArray(autonomyView.snapshot?.messages);
  if (!autonomyView.hydratedMessages) {
    // Backfill remains visible in the panel. Only messages that arrive while
    // this page is already open raise a passive toast.
    for (const message of messages) autonomyView.seenMessages.add(autonomyMessageKey(message));
    autonomyView.hydratedMessages = true;
    return;
  }
  const fresh = messages.filter((message) => {
    const key = autonomyMessageKey(message);
    return key && !message.read_at && !autonomyView.seenMessages.has(key);
  });
  for (const message of messages) autonomyView.seenMessages.add(autonomyMessageKey(message));
  if (fresh.length) autonomyShowToast(fresh[fresh.length - 1]);
}

async function autonomyPoll() {
  if (autonomyView.polling || document.hidden) return;
  autonomyView.polling = true;
  try {
    const value = await api("/api/autonomy");
    // A poll may refresh the displayed version, but a conflict caused by a
    // stale submission stays explicit until the owner clicks refresh.
    autonomyApplySnapshot(value);
  } catch (error) {
    if (!autonomyView.snapshot) {
      autonomyShowError("暂时无法读取自主状态：" + error.message);
    } else {
      autonomySetText("autonomy-running", "状态暂时无法更新");
    }
  } finally {
    autonomyView.polling = false;
  }
}

function autonomyArmPolling() {
  if (autonomyView.pollTimer) clearTimeout(autonomyView.pollTimer);
  autonomyView.pollTimer = setTimeout(async () => {
    autonomyView.pollTimer = null;
    if (!document.hidden) await autonomyPoll();
    autonomyArmPolling();
  }, 5000);
}

function autonomyMarkSettingsDirty() {
  autonomyView.settingsDirty = true;
  autonomyView.settingsStatus = "有未保存的修改";
  autonomySetText("autonomy-settings-status", autonomyView.settingsStatus);
}

function autonomyInit() {
  if (autonomyView.initialized) return;
  autonomyView.initialized = true;
  $("autonomy-toggle").addEventListener("click", () => autonomyOpen(!autonomyView.open));
  $("autonomy-close").addEventListener("click", () => autonomyOpen(false));
  $("autonomy-refresh").addEventListener("click", () => {
    autonomyClearConflict();
    autonomyView.settingsDirty = false;
    autonomyView.settingsRendered = "";
    autonomyPoll().catch((error) => autonomyShowError(error.message));
  });
  $("autonomy-conflict-refresh").addEventListener("click", () => {
    autonomyClearConflict();
    autonomyView.settingsDirty = false;
    autonomyView.settingsRendered = "";
    autonomyPoll().catch((error) => autonomyShowError(error.message));
  });
  $("autonomy-open-ling").addEventListener("click", autonomyOpenInLing);
  $("autonomy-review").addEventListener("click", () => autonomyReview());
  for (const operation of ["enable", "pause", "resume", "terminate"]) {
    $("autonomy-" + operation).addEventListener("click", () => autonomyControl(operation));
  }
  $("autonomy-settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    autonomySaveSettings().catch((error) => autonomyShowError(error.message));
  });
  for (const id of [
    "autonomy-read-roots", "autonomy-output-root", "autonomy-token-limit",
    "autonomy-max-output-tokens", "autonomy-fallback-minutes", "autonomy-contact-minutes",
    "autonomy-observer-interval", "autonomy-allowed-mcp-tools",
  ]) $(id).addEventListener("input", autonomyMarkSettingsDirty);
  $("autonomy-toast-close").addEventListener("click", autonomyHideToast);
  $("autonomy-toast-view").addEventListener("click", () => {
    autonomyHideToast();
    autonomyOpen(true);
  });
  $("autonomy-toast-read").addEventListener("click", () => {
    const message = autonomyView.toastMessage;
    autonomyHideToast();
    if (message) autonomyMarkRead(message);
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) autonomyPoll();
  });
  const toast = $("autonomy-toast");
  if (toast) {
    toast.hidden = true;
    toast.style.display = "none";
  }
  autonomyPoll();
  autonomyArmPolling();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", autonomyInit, { once: true });
} else {
  // app.js supplies the shared DOM helpers. A normal browser has already
  // parsed those helpers by this point; a small test harness can load this
  // module before app.js and still reach initialization on the next turn.
  if (typeof $ === "function" && typeof api === "function" && typeof post === "function") {
    autonomyInit();
  } else {
    const deferred = function autonomyDeferredInit() {
      if (typeof $ === "function" && typeof api === "function" && typeof post === "function") autonomyInit();
      else setTimeout(deferred, 0);
    };
    setTimeout(deferred, 0);
  }
}
