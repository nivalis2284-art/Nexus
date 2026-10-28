"use strict";

// Owner-facing view of what the model receives: editable instructions, roles,
// read-only assembled sections, and the shared compacted/recent history.

const contextView = {
  initialized: false,
  data: null,
  tab: "system",
  selected: new Set(),
  busy: false,
  undoBusy: false,
  canUndo: false,
  loading: false,
  loadError: "",
  request: 0,
  nodes: null,
};

const CONTEXT_ROLE_CAPTIONS = {
  main: "持续交流与工作监督",
  work: "围绕目标持续执行",
  subagent: "聚焦独立的委派范围",
};

const CONTEXT_MESSAGE_LABELS = {
  user: "所有者",
  assistant: "灵",
  tool: "工具",
  notice: "记录",
};

function initContext() {
  if (contextView.initialized) return;
  const root = $("context-view");
  if (!root) return;
  contextView.initialized = true;
  buildContextView(root);
  const open = $("context-open");
  if (open) {
    open.addEventListener("click", () => {
      openContext().catch((error) => setStatus(error.message, true));
    });
  }
  renderContext();
}

function buildContextView(root) {
  root.replaceChildren();
  const page = el("div", "context-page");

  const head = el("div", "context-head");
  const intro = el("div", "context-intro");
  intro.appendChild(el("div", "context-eyebrow", "CONTEXT WORKSPACE"));
  intro.appendChild(el("h1", "context-title", "上下文管理"));
  intro.appendChild(el("p", "context-subtitle", "查看灵正在参考的信息，让指令与历史各归其位。"));
  const back = el("button", "context-btn context-back", "← 返回主交流");
  back.type = "button";
  back.addEventListener("click", () => {
    contextView.request += 1;
    openLing({keepTranscript: true}).catch((error) => setStatus(error.message, true));
  });
  const headActions = el("div", "context-head-actions");
  const refresh = el("button", "context-btn", "刷新");
  refresh.type = "button";
  refresh.addEventListener("click", () => loadContext().catch((error) => setStatus(error.message, true)));
  headActions.append(refresh, back);
  head.append(intro, headActions);
  page.appendChild(head);

  const overview = el("section", "context-overview");
  overview.setAttribute("aria-label", "上下文概况");
  const overviewTop = el("div", "context-overview-top");
  const overviewLead = el("div", "context-overview-lead");
  overviewLead.appendChild(el("span", "context-kicker", "当前主交流"));
  const status = el("p", "context-status", "正在载入上下文…");
  overviewLead.appendChild(status);
  const windowInfo = el("span", "context-window");
  overviewTop.append(overviewLead, windowInfo);
  const metrics = el("div", "context-metrics");
  overview.append(overviewTop, metrics);
  page.appendChild(overview);

  const errorBar = el("div", "context-error");
  errorBar.hidden = true;
  const errorText = el("span");
  const retry = el("button", "context-btn", "重试");
  retry.type = "button";
  retry.addEventListener("click", () => loadContext().catch((error) => setStatus(error.message, true)));
  errorBar.append(errorText, retry);
  page.appendChild(errorBar);

  const tabs = el("div", "context-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "上下文类别");
  const tabButtons = [];
  const addTab = (tab, label) => {
    const button = el("button", "context-tab");
    button.type = "button";
    button.setAttribute("role", "tab");
    button.dataset.tab = tab;
    button.append(document.createTextNode(label));
    const count = el("span", "context-tab-count");
    button.appendChild(count);
    button.addEventListener("click", () => selectContextTab(tab));
    tabs.appendChild(button);
    tabButtons.push({tab, button, count});
  };
  addTab("system", "系统指令");
  addTab("history", "历史上下文");
  tabs.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "system"
      : event.key === "End" ? "history"
        : contextView.tab === "system" ? "history" : "system";
    selectContextTab(next);
    const active = tabButtons.find((item) => item.tab === next);
    if (active) active.button.focus();
  });
  page.appendChild(tabs);

  const systemPanel = el("div", "context-tab-panel");
  systemPanel.setAttribute("role", "tabpanel");
  systemPanel.dataset.panel = "system";

  const ownerCard = contextCard("所有者指令", "每条指令独立生效，可逐条编辑、删除或新增。", "可编辑");
  const addOwner = el("button", "context-btn soft", "＋ 添加指令");
  addOwner.type = "button";
  addOwner.addEventListener("click", addContextOwner);
  ownerCard.actions.appendChild(addOwner);
  const ownerList = el("div", "context-instructions");
  const ownerHelper = el("p", "context-helper", "当前明确要求优先于一般偏好；这里的内容不代替实际权限设置。");
  ownerCard.body.append(ownerList, ownerHelper);
  systemPanel.appendChild(ownerCard.card);

  const rolesCard = contextCard("角色与工作规则", "三个角色分别生效，编辑后在弹窗中保存。", "可编辑");
  const roles = el("div", "context-roles");
  rolesCard.body.appendChild(roles);
  systemPanel.appendChild(rolesCard.card);

  const sectionLabel = el("div", "context-section-label");
  sectionLabel.appendChild(el("h2", "", "本次自动组装的信息"));
  sectionLabel.appendChild(el("span", "", "由当前环境与运行状态提供 · 只读"));
  systemPanel.appendChild(sectionLabel);
  const sections = el("div", "context-sections");
  systemPanel.appendChild(sections);
  page.appendChild(systemPanel);

  const historyPanel = el("div", "context-tab-panel");
  historyPanel.setAttribute("role", "tabpanel");
  historyPanel.dataset.panel = "history";
  const undoBar = el("div", "context-undo");
  undoBar.hidden = true;
  const undoLabel = el("span", "", "已修改交流记录");
  const undoButton = el("button", "context-text-button", "撤销最近一次修改");
  undoButton.type = "button";
  undoButton.addEventListener("click", () => {
    undoContextHistory().catch((error) => setStatus(error.message, true));
  });
  undoBar.append(undoLabel, undoButton);
  historyPanel.appendChild(undoBar);

  const historyGrid = el("div", "context-history-grid");
  const summaryCard = contextCard("已压缩上下文", "此前交流收束为摘要；编辑会替换当前摘要。", "只读全文");
  const editSummary = el("button", "context-btn soft", "编辑摘要");
  editSummary.type = "button";
  editSummary.addEventListener("click", editContextSummary);
  summaryCard.actions.appendChild(editSummary);
  const summaryMeta = el("div", "context-summary-meta");
  const summaryText = el("div", "context-summary-text");
  const summaryEmpty = el("p", "context-empty", "当前还没有压缩摘要。");
  summaryCard.body.append(summaryMeta, summaryText, summaryEmpty,
    el("p", "context-helper", "摘要是此前的工作记录，最新的所有者要求仍然优先。"));
  historyGrid.appendChild(summaryCard.card);

  const messagesCard = contextCard("未压缩上下文", "保留的会话消息，可选择删除。", "");
  const messageCount = el("span", "context-pill", "0 条");
  messagesCard.actions.appendChild(messageCount);
  const toolbar = el("div", "context-message-toolbar");
  const selectLabel = el("label", "context-check-label");
  const selectAll = document.createElement("input");
  selectAll.type = "checkbox";
  selectAll.addEventListener("change", () => {
    const values = contextView.data ? contextView.data.messages || [] : [];
    contextView.selected = selectAll.checked ? new Set(values.map((item) => item.index)) : new Set();
    renderContext();
  });
  const selectedLabel = el("span", "");
  selectLabel.append(selectAll, document.createTextNode("全选消息"), selectedLabel);
  const deleteSelected = el("button", "context-btn danger", "删除所选");
  deleteSelected.type = "button";
  deleteSelected.addEventListener("click", deleteContextMessages);
  toolbar.append(selectLabel, deleteSelected);
  const messageList = el("div", "context-messages");
  const historyHint = el("p", "context-history-hint");
  messagesCard.body.append(toolbar, messageList, historyHint);
  historyGrid.appendChild(messagesCard.card);
  historyPanel.appendChild(historyGrid);
  page.appendChild(historyPanel);

  root.appendChild(page);
  contextView.nodes = {
    page, status, windowInfo, metrics, errorBar, errorText, tabButtons, refresh, addOwner,
    panels: {system: systemPanel, history: historyPanel},
    ownerList, roles, sections, summaryMeta, summaryText, summaryEmpty, editSummary,
    undoBar, undoButton, messageCount, selectAll, selectedLabel, deleteSelected,
    messageList, historyHint,
  };
}

function contextCard(title, description, badge) {
  const card = el("section", "context-card");
  const head = el("div", "context-card-head");
  const lead = el("div", "context-card-lead");
  const titleLine = el("div", "context-card-titleline");
  titleLine.appendChild(el("span", "context-card-dot"));
  titleLine.appendChild(el("h2", "", title));
  if (badge) titleLine.appendChild(el("span", "context-pill", badge));
  lead.appendChild(titleLine);
  if (description) lead.appendChild(el("p", "context-card-desc", description));
  const actions = el("div", "context-card-actions");
  head.append(lead, actions);
  const body = el("div", "context-card-body");
  card.append(head, body);
  return {card, body, actions};
}

async function openContext() {
  if (!contextView.initialized) initContext();
  const root = $("context-view");
  if (!root || !contextView.nodes) return;
  rememberCurrentScroll();
  clearLingView();
  state.viewVersion = (state.viewVersion || 0) + 1;
  $("scroller").hidden = true;
  $("composer").hidden = true;
  $("task-panel").hidden = true;
  if (typeof taskView !== "undefined") {
    taskView.current = null;
    taskView.request = (taskView.request || 0) + 1;
  }
  $("title").textContent = "上下文管理";
  root.hidden = false;
  const trigger = $("context-open");
  if (trigger) trigger.classList.add("active");
  $("ling-home").classList.remove("active");
  renderContext();
  await loadContext();
}

async function loadContext() {
  const request = ++contextView.request;
  contextView.loading = true;
  contextView.loadError = "";
  renderContext();
  try {
    const value = await api("/api/context");
    contextView.loading = false;
    if (request !== contextView.request || $("context-view").hidden) return;
    applyContext(value);
  } catch (error) {
    if (request !== contextView.request) return;
    contextView.loading = false;
    contextView.loadError = error.message;
    renderContext();
    setStatus(error.message, true);
  }
}

function applyContext(value) {
  if (!value || typeof value !== "object") return;
  contextView.data = value;
  const indices = new Set((value.messages || []).map((item) => item.index));
  contextView.selected = new Set([...contextView.selected].filter((index) => indices.has(index)));
  renderContext();
}

function selectContextTab(tab) {
  contextView.tab = tab === "history" ? "history" : "system";
  renderContext();
}

function renderContext() {
  const nodes = contextView.nodes;
  if (!nodes) return;
  const data = contextView.data;
  const running = Boolean(data && data.running);
  const owners = data && Array.isArray(data.owner_instructions) ? data.owner_instructions : [];
  const roles = data && Array.isArray(data.roles) ? data.roles : [];
  const sections = data && Array.isArray(data.sections) ? data.sections : [];
  const messages = data && Array.isArray(data.messages) ? data.messages : [];
  const summary = data ? data.summary : null;

  nodes.tabButtons.forEach(({tab, button, count}) => {
    const active = tab === contextView.tab;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
    count.textContent = tab === "system"
      ? String(owners.length + roles.length + sections.length) + " 项"
      : String(messages.length) + " 条";
  });
  Object.entries(nodes.panels).forEach(([tab, panel]) => {
    panel.hidden = tab !== contextView.tab;
  });

  nodes.status.textContent = contextView.loading
    ? (data ? "正在刷新上下文…" : "正在载入上下文…")
    : data
      ? (running ? "灵正在处理，历史修改需先停止" : "灵当前空闲，可调整后续请求参考的上下文")
      : "尚未载入";
  nodes.status.classList.toggle("is-locked", running);
  nodes.refresh.disabled = contextView.loading;
  nodes.errorBar.hidden = !contextView.loadError;
  nodes.errorText.textContent = contextView.loadError;

  const effectiveWindow = state.contextWindow || state.contextLimit || null;
  nodes.windowInfo.textContent = effectiveWindow
    ? "模型窗口 " + Number(effectiveWindow).toLocaleString() + " tok"
    : "";
  nodes.metrics.replaceChildren();
  const metric = (label, value) => {
    const item = el("div", "context-metric");
    item.append(el("span", "context-metric-label", label), el("strong", "context-metric-value", value));
    nodes.metrics.appendChild(item);
  };
  metric("所有者指令", owners.length + " 条");
  metric("角色", roles.length + " 个");
  metric("未压缩消息", messages.length + " 条");
  metric("压缩摘要", summary ? "覆盖 " + (Number(summary.through) + 1) + " 条" : "暂无");

  nodes.ownerList.replaceChildren();
  nodes.addOwner.disabled = contextView.loading || !data;
  if (!owners.length) {
    nodes.ownerList.appendChild(el("p", "context-empty", data ? "还没有所有者指令。" : "载入后显示所有者指令。"));
  }
  owners.forEach((text, index) => {
    const row = el("div", "context-instruction");
    row.appendChild(el("span", "context-index", String(index + 1).padStart(2, "0")));
    row.appendChild(el("div", "context-instruction-text", text));
    const actions = el("div", "context-instruction-actions");
    const edit = el("button", "context-btn", "编辑");
    edit.type = "button";
    edit.setAttribute("aria-label", "编辑第 " + (index + 1) + " 条所有者指令");
    edit.addEventListener("click", () => editContextOwner(index));
    const remove = el("button", "context-text-button danger", "删除");
    remove.type = "button";
    remove.setAttribute("aria-label", "删除第 " + (index + 1) + " 条所有者指令");
    remove.addEventListener("click", () => removeContextOwner(index));
    actions.append(edit, remove);
    row.appendChild(actions);
    nodes.ownerList.appendChild(row);
  });

  nodes.roles.replaceChildren();
  if (!roles.length) {
    nodes.roles.appendChild(el("p", "context-empty", data ? "没有可编辑的角色规则。" : "载入后显示角色规则。"));
  }
  roles.forEach((role) => {
    const card = el("article", "context-role");
    card.dataset.role = role.id || "";
    const top = el("div", "context-role-top");
    const title = el("div", "context-role-name", role.title || role.id || "角色");
    const edit = el("button", "context-btn", "编辑");
    edit.type = "button";
    edit.setAttribute("aria-label", "编辑" + (role.title || "角色") + "规则");
    edit.addEventListener("click", () => editContextRole(role));
    top.append(title, edit);
    const caption = el("p", "context-role-caption", CONTEXT_ROLE_CAPTIONS[role.id] || "生效中的角色规则");
    card.append(top, caption, el("div", "context-role-text", role.text || ""));
    nodes.roles.appendChild(card);
  });

  nodes.sections.replaceChildren();
  if (!sections.length) {
    nodes.sections.appendChild(el("p", "context-empty", data ? "没有其他系统段。" : "载入后显示自动组装的信息。"));
  }
  sections.forEach((section) => {
    const card = el("article", "context-read-card");
    const title = el("h3", "context-read-title");
    title.append(el("span", "context-read-dot"), document.createTextNode(section.title || section.id || "系统段"));
    title.appendChild(el("span", "context-lock", "只读"));
    card.append(title, el("div", "context-read-text", section.text || ""));
    nodes.sections.appendChild(card);
  });

  nodes.summaryMeta.hidden = !summary;
  nodes.summaryText.hidden = !summary;
  nodes.summaryEmpty.hidden = Boolean(summary);
  nodes.editSummary.disabled = !summary || running || contextView.loading;
  if (summary) {
    nodes.summaryMeta.replaceChildren(
      el("span", "", "覆盖此前 " + (Number(summary.through) + 1) + " 条记录"),
      el("span", "", "编辑后影响后续请求"),
    );
    nodes.summaryText.textContent = summary.text || "";
  }

  nodes.messageCount.textContent = messages.length + " 条";
  const selectedCount = contextView.selected.size;
  nodes.selectedLabel.textContent = selectedCount ? "· 已选 " + selectedCount + " 条" : "";
  nodes.deleteSelected.disabled = running || contextView.loading || !selectedCount;
  nodes.deleteSelected.textContent = selectedCount ? "删除所选 (" + selectedCount + ")" : "删除所选";
  nodes.selectAll.disabled = running || !messages.length;
  nodes.selectAll.checked = messages.length > 0 && selectedCount === messages.length;
  nodes.selectAll.indeterminate = selectedCount > 0 && selectedCount < messages.length;
  nodes.undoBar.hidden = !contextView.canUndo;
  nodes.undoButton.disabled = contextView.undoBusy || running;
  nodes.historyHint.textContent = running
    ? "灵正在处理当前输入，请先停止后再编辑或删除历史。"
    : "删除前会再次确认；会话尚未继续时，可撤销最近一次历史修改。";

  nodes.messageList.replaceChildren();
  if (!messages.length) {
    nodes.messageList.appendChild(el("p", "context-empty", data ? "没有未压缩的会话消息。" : "载入后显示未压缩消息。"));
  }
  messages.forEach((message) => {
    const selected = contextView.selected.has(message.index);
    const row = el("article", "context-message" + (selected ? " is-selected" : ""));
    const checkLabel = el("label", "context-message-select");
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = selected;
    check.disabled = running;
    check.setAttribute("aria-label", "选择第 " + (message.index + 1) + " 条" +
      (CONTEXT_MESSAGE_LABELS[message.role] || "") + "消息");
    check.addEventListener("change", () => {
      if (check.checked) contextView.selected.add(message.index);
      else contextView.selected.delete(message.index);
      row.classList.toggle("is-selected", check.checked);
      renderContext();
    });
    checkLabel.appendChild(check);
    const avatar = el("span", "context-message-avatar role-" + (message.role || "notice"),
      message.role === "user" ? "我" : message.role === "assistant" ? "灵" :
        message.role === "tool" ? "具" : "记");
    const main = el("div", "context-message-main");
    const meta = el("div", "context-message-meta");
    meta.append(el("strong", "", CONTEXT_MESSAGE_LABELS[message.role] || message.role || "记录"));
    const time = typeof clock === "function" ? clock(message.at) : "";
    if (time) meta.appendChild(el("span", "", time));
    if (message.role === "tool" && message.name) meta.appendChild(el("span", "", message.name));
    const content = message.text || (message.name ? "调用 " + message.name : "");
    main.append(meta, el("div", "context-message-text", content));
    row.append(checkLabel, avatar, main);
    nodes.messageList.appendChild(row);
  });
}

function editContextOwner(index) {
  const values = contextView.data && contextView.data.owner_instructions;
  if (!Array.isArray(values) || typeof values[index] !== "string") return;
  editText({
    title: "编辑所有者指令 " + (index + 1),
    text: values[index],
    description: "保存后用于后续请求；取消不会修改现有指令。",
    save: async (text) => {
      const next = contextView.data.owner_instructions.slice();
      next[index] = text;
      await saveContextOwners(next);
    },
  }).catch((error) => setStatus(error.message, true));
}

function addContextOwner() {
  if (!contextView.data || contextView.loading) return;
  editText({
    title: "添加所有者指令",
    text: "",
    submitLabel: "添加指令",
    description: "保存后用于后续请求；新指令追加在现有指令之后。",
    save: async (text) => {
      await saveContextOwners([...(contextView.data.owner_instructions || []), text]);
    },
  }).catch((error) => setStatus(error.message, true));
}

function removeContextOwner(index) {
  const values = contextView.data && contextView.data.owner_instructions;
  if (!Array.isArray(values) || typeof values[index] !== "string") return;
  confirmEdit({
    title: "删除这条所有者指令？",
    description: values[index],
    submitLabel: "删除指令",
    save: async () => {
      const next = contextView.data.owner_instructions.slice();
      next.splice(index, 1);
      await saveContextOwners(next);
    },
  }).catch((error) => setStatus(error.message, true));
}

async function saveContextOwners(items) {
  if (contextView.busy) throw new Error("另一项保存还在进行，请稍候再试。");
  contextView.busy = true;
  try {
    const value = await put("/api/context/owners", {items});
    applyContext(value);
    setStatus("所有者指令已保存");
  } finally {
    contextView.busy = false;
  }
}

function editContextRole(role) {
  editText({
    title: "编辑" + (role.title || "角色") + "规则",
    text: role.text || "",
    description: "保存后用于后续请求；取消不会修改现有规则。",
    save: async (text) => {
      const value = await put("/api/context/roles", {id: role.id, text});
      applyContext(value);
      setStatus("角色规则已保存");
    },
  }).catch((error) => setStatus(error.message, true));
}

function editContextSummary() {
  const summary = contextView.data && contextView.data.summary;
  if (!summary) return;
  if (contextView.data.running) {
    setStatus("请先停止灵的当前输入再修改摘要", true);
    return;
  }
  editText({
    title: "编辑已压缩上下文",
    text: summary.text || "",
    description: "摘要会替代此前记录；最新的所有者要求仍然优先。",
    save: async (text) => {
      const current = contextView.data;
      const value = await put("/api/context/summary", {
        text,
        version: current.version,
        index: current.summary.index,
      });
      contextView.canUndo = true;
      applyContext(value);
      setStatus("历史摘要已保存");
    },
  }).catch((error) => setStatus(error.message, true));
}

function deleteContextMessages() {
  const data = contextView.data;
  if (!data) return;
  if (data.running) {
    setStatus("请先停止灵的当前输入再删除历史", true);
    return;
  }
  const indices = [...contextView.selected].sort((a, b) => a - b);
  if (!indices.length) return;
  confirmEdit({
    title: "删除 " + indices.length + " 条消息？",
    description: "删除后可在会话继续前撤销最近一次修改；已执行的工具与后台工作不会回滚。",
    submitLabel: "删除消息",
    save: async () => {
      if (contextView.busy) throw new Error("另一项保存还在进行，请稍候再试。");
      contextView.busy = true;
      try {
        const result = await post("/api/ling/history", {
          operation: "delete",
          indices,
          version: contextView.data.version,
        });
        contextView.selected.clear();
        contextView.canUndo = true;
        applyContext(result.context);
        setStatus("已删除所选消息");
      } finally {
        contextView.busy = false;
      }
    },
  }).catch((error) => setStatus(error.message, true));
}

async function undoContextHistory() {
  const data = contextView.data;
  if (!data) return;
  if (data.running) {
    setStatus("请先停止灵的当前输入再撤销历史", true);
    return;
  }
  if (contextView.undoBusy) return;
  contextView.undoBusy = true;
  renderContext();
  try {
    const result = await post("/api/ling/history", {operation: "undo", version: data.version});
    contextView.canUndo = false;
    contextView.selected.clear();
    applyContext(result.context);
    setStatus("已撤销最近一次历史修改");
  } finally {
    contextView.undoBusy = false;
    renderContext();
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initContext, {once: true});
} else {
  initContext();
}
