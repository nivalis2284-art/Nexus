"use strict";

// Durable responsibilities. Saving a record is a local database write; this
// panel never sends a model request or starts linked work.

const responsibilitiesView = {
  records: [],
  open: false,
  editing: null,
  timer: null,
};

function responsibilityRequest(path, method, body) {
  return api(path, {
    method: method || "GET",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
}

function responsibilityStatus(state) {
  return { active: "已启用", paused: "已暂停", ended: "已结束" }[state] || state;
}

function responsibilityOrigin(origin) {
  return origin === "ling" ? "灵建立" : "你建立";
}

function responsibilityError(message) {
  const box = $("responsibilities-error");
  box.textContent = message || "";
  box.hidden = !message;
}

function clearResponsibilitiesView() {
  if (!responsibilitiesView.open) return;
  responsibilitiesView.open = false;
  if (responsibilitiesView.timer) clearTimeout(responsibilitiesView.timer);
  responsibilitiesView.timer = null;
  const region = $("responsibilities-region");
  region.hidden = true;
  $("responsibilities-open").setAttribute("aria-expanded", "false");
  $("responsibilities-open").classList.remove("active");
}

async function refreshResponsibilities() {
  const value = await responsibilityRequest("/api/responsibilities");
  responsibilitiesView.records = Array.isArray(value.responsibilities) ? value.responsibilities : [];
  renderResponsibilities();
}

function renderResponsibilities() {
  const records = responsibilitiesView.records;
  const active = records.filter((item) => item.state === "active").length;
  $("responsibilities-summary").textContent = records.length
    ? `${records.length} 项长期交代 · ${active} 项已启用`
    : "还没有长期交代";
  const list = $("responsibilities-list");
  list.innerHTML = "";
  if (!records.length) {
    list.appendChild(el("p", "automation-empty", "保存长期方向后，灵会在已有唤醒中根据说明推进已有工作。"));
    return;
  }
  for (const record of records) list.appendChild(responsibilityCard(record));
}

function responsibilityCard(record) {
  const card = el("article", "responsibility-card " + record.state);
  const head = el("div", "responsibility-card-head");
  const title = el("h3", "responsibility-title", record.title || "未命名长期交代");
  title.title = record.title || "";
  head.appendChild(title);
  head.appendChild(el("span", "responsibility-chip " + record.state, responsibilityStatus(record.state)));
  card.appendChild(head);
  card.appendChild(el("div", "responsibility-origin", responsibilityOrigin(record.origin)));
  if (record.instruction) card.appendChild(el("p", "responsibility-instruction", record.instruction));
  if (record.summary) card.appendChild(el("p", "responsibility-summary", "进展：" + record.summary));

  const tasks = Array.isArray(record.tasks) ? record.tasks : [];
  if (tasks.length) {
    const links = el("div", "responsibility-tasks");
    links.appendChild(el("span", "responsibility-tasks-label", "关联任务"));
    for (const task of tasks) {
      const link = el("button", "responsibility-task-link", task.title || "未命名任务");
      link.type = "button";
      link.title = [task.state_label, task.status_detail].filter(Boolean).join(" · ");
      link.appendChild(el("span", "responsibility-task-state", task.state_label || task.state || ""));
      link.addEventListener("click", () => openTask(task.id));
      links.appendChild(link);
    }
    card.appendChild(links);
  }

  const actions = el("div", "responsibility-actions");
  const edit = el("button", "link-button", "编辑");
  edit.type = "button";
  edit.addEventListener("click", () => openResponsibilityEditor(record));
  actions.appendChild(edit);
  if (record.state !== "ended") {
    const operation = record.state === "paused" ? "resume" : "pause";
    const control = el("button", "link-button", record.state === "paused" ? "恢复" : "暂停");
    control.type = "button";
    control.addEventListener("click", () => controlResponsibility(record, operation));
    actions.appendChild(control);
    const end = el("button", "link-button danger-link", "结束");
    end.type = "button";
    end.addEventListener("click", () => controlResponsibility(record, "end"));
    actions.appendChild(end);
  }
  card.appendChild(actions);
  return card;
}

function openResponsibilityEditor(record) {
  responsibilitiesView.editing = record || null;
  const form = $("responsibility-form");
  form.hidden = false;
  $("responsibility-title").value = record ? record.title || "" : "";
  $("responsibility-instruction").value = record ? record.instruction || "" : "";
  $("responsibility-summary").value = record ? record.summary || "" : "";
  $("responsibility-form-error").textContent = "";
  if (form.scrollIntoView) form.scrollIntoView({block: "start"});
  $("responsibility-title").focus({preventScroll: true});
}

function closeResponsibilityEditor() {
  responsibilitiesView.editing = null;
  $("responsibility-form").hidden = true;
  $("responsibility-form-error").textContent = "";
}

async function saveResponsibility(event) {
  if (event) event.preventDefault();
  const current = responsibilitiesView.editing;
  const body = {
    title: $("responsibility-title").value.trim(),
    instruction: $("responsibility-instruction").value.trim(),
    summary: $("responsibility-summary").value.trim(),
  };
  if (!body.title || !body.instruction) {
    $("responsibility-form-error").textContent = "标题和说明都不能为空";
    return;
  }
  try {
    if (current) {
      await responsibilityRequest("/api/responsibilities/" + encodeURIComponent(current.id), "PUT", body);
    } else {
      await responsibilityRequest("/api/responsibilities", "POST", {
        title: body.title, instruction: body.instruction, summary: body.summary,
      });
    }
    closeResponsibilityEditor();
    await refreshResponsibilities();
  } catch (error) {
    $("responsibility-form-error").textContent = error.message;
  }
}

async function controlResponsibility(record, operation) {
  try {
    await responsibilityRequest(
      "/api/responsibilities/" + encodeURIComponent(record.id) + "/control",
      "POST", { operation },
    );
    await refreshResponsibilities();
  } catch (error) {
    responsibilityError(error.message);
  }
}

async function openResponsibilities(open) {
  const next = typeof open === "boolean" ? open : !responsibilitiesView.open;
  if (!next) {
    clearResponsibilitiesView();
    await openLing().catch((error) => setStatus(error.message, true));
    return;
  }
  if (responsibilitiesView.open) return;
  // This is a peer of a task/chat view, so the previous view must stop polling
  // before this panel replaces the transcript and composer.
  rememberCurrentScroll();
  const viewVersion = state.viewVersion = (state.viewVersion || 0) + 1;
  if (typeof clearLingView === "function") clearLingView();
  if (typeof taskView !== "undefined") {
    taskView.current = null;
    if (taskView.timer) clearTimeout(taskView.timer);
    taskView.timer = null;
    $("task-panel").hidden = true;
    renderTasks();
  }
  if (typeof automationOpen === "function") automationOpen(false);
  if (typeof setNotificationsOpen === "function") setNotificationsOpen(false);
  responsibilitiesView.open = true;
  const region = $("responsibilities-region");
  region.hidden = false;
  $("scroller").hidden = true;
  $("composer").hidden = true;
  $("title").textContent = "长期交代";
  $("responsibilities-open").setAttribute("aria-expanded", "true");
  $("responsibilities-open").classList.add("active");
  try {
    await refreshResponsibilities();
  } catch (error) {
    responsibilityError("暂时无法读取长期交代：" + error.message);
  }
  if (viewVersion !== state.viewVersion || !responsibilitiesView.open) return;
  armResponsibilitiesPolling();
}

function armResponsibilitiesPolling() {
  if (responsibilitiesView.timer) clearTimeout(responsibilitiesView.timer);
  responsibilitiesView.timer = setTimeout(async () => {
    responsibilitiesView.timer = null;
    if (responsibilitiesView.open && !document.hidden) {
      try {
        await refreshResponsibilities();
      } catch (error) {
        responsibilityError("长期交代状态暂时无法更新：" + error.message);
      }
    }
    if (responsibilitiesView.open) armResponsibilitiesPolling();
  }, 2500);
}

$("responsibilities-open").addEventListener("click", () => openResponsibilities());
$("responsibilities-close").addEventListener("click", () => {
  clearResponsibilitiesView();
  // The panel replaced the previous view; return to the persistent entry.
  openLing().catch((error) => setStatus(error.message, true));
});
$("responsibilities-refresh").addEventListener("click", () => {
  refreshResponsibilities().catch((error) => responsibilityError(error.message));
});
$("responsibility-add").addEventListener("click", () => openResponsibilityEditor(null));
$("responsibility-cancel").addEventListener("click", closeResponsibilityEditor);
$("responsibility-form").addEventListener("submit", saveResponsibility);
