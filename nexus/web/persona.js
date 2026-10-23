"use strict";

// The persona editor owns one JSON document. A save always sends the whole
// snapshot, so entry ids stay stable when a name or title changes.

const personaView = {
  open: false,
  viewSeq: 0,
  editSeq: 0,
  snapshot: null,
  editing: null,
  sectionEditing: null,
  saving: false,
  savingKind: "",
  summaryGenerating: false,
  suggestion: "",
};

const PERSONA_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function personaNode(id) {
  return document.getElementById(id);
}

function personaEl(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function personaClear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function personaText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function personaError(id, message) {
  const box = personaNode(id);
  box.textContent = message || "";
  box.hidden = !message;
}

function personaStatus(id, message, tone) {
  const node = personaNode(id);
  node.className = "small persona-status" + (tone ? " persona-status-" + tone : "");
  node.textContent = message || "";
}

function personaRequest(path, method, body) {
  const options = { method: method || "GET" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  return fetch(path, options).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || response.statusText || "请求失败");
    return payload;
  });
}

function personaSection(raw) {
  return {
    id: raw.id,
    title: personaText(raw.title),
    content: personaText(raw.content),
  };
}

function personaEntry(raw) {
  return {
    id: raw.id,
    name: personaText(raw.name),
    description: personaText(raw.description),
    body: personaText(raw.body),
    enabled: raw.enabled !== false,
  };
}

function personaNormalize(value) {
  if (!value || typeof value.persona !== "object" || value.persona === null) {
    throw new Error("返回数据缺少 persona");
  }
  const sections = Array.isArray(value.persona.sections)
    ? value.persona.sections.filter((item) => item && item.id != null).map(personaSection)
    : [];
  const entries = Array.isArray(value.persona.entries)
    ? value.persona.entries.filter((item) => item && item.id != null).map(personaEntry)
    : [];
  const last = value.last_request;
  return {
    persona: { sections, entries },
    preview: personaText(value.preview),
    last_request: last && typeof last === "object" ? {
      content: personaText(last.content),
      time: personaText(last.time),
      source: personaText(last.source),
      model: personaText(last.model),
    } : null,
  };
}

function personaDocument() {
  const persona = personaView.snapshot.persona;
  return {
    sections: persona.sections.map((item) => ({
      id: item.id,
      title: item.title,
      content: item.content,
    })),
    entries: persona.entries.map((item) => ({
      id: item.id,
      name: item.name,
      description: item.description,
      body: item.body,
      enabled: item.enabled,
    })),
  };
}

function personaCurrent(viewSeq) {
  return personaView.open && personaView.viewSeq === viewSeq;
}

function personaFormatTime(value) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return personaText(value);
  return date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function personaSourceLabel(source) {
  return { chat: "聊天", wake: "自主唤醒" }[source] || personaText(source) || "未记录";
}

function personaRenderPreview() {
  personaNode("persona-preview").textContent = personaView.snapshot
    ? personaView.snapshot.preview || "当前没有可注入内容。"
    : "正在载入当前已保存内容…";
}

function personaRenderLast() {
  const box = personaNode("persona-last");
  personaClear(box);
  const last = personaView.snapshot ? personaView.snapshot.last_request : null;
  if (!last) {
    box.appendChild(personaEl("p", "persona-empty", personaView.snapshot
      ? "还没有发送记录。上面的预览只表示已保存内容，不代表已经发送。"
      : "正在载入发送记录…"));
    return;
  }
  box.appendChild(personaEl("div", "persona-last-meta",
    "时间：" + personaFormatTime(last.time) +
    " · 来源：" + personaSourceLabel(last.source) +
    " · 模型：" + (last.model || "未记录")));
  box.appendChild(personaEl("pre", "persona-last-content",
    last.content || "本次请求未携带额外预设人设"));
}

function personaSmallButton(text, action, cls) {
  const button = personaEl("button", cls || "persona-action", text);
  button.type = "button";
  button.disabled = personaView.saving;
  button.addEventListener("click", action);
  return button;
}

function personaRenderSections() {
  const list = personaNode("persona-sections");
  personaClear(list);
  const sections = personaView.snapshot ? personaView.snapshot.persona.sections : [];
  if (!personaView.snapshot) {
    list.appendChild(personaEl("p", "persona-empty", "正在载入设定…"));
    return;
  }
  if (!sections.length) {
    list.appendChild(personaEl("p", "persona-empty", "还没有设定。"));
    return;
  }
  sections.forEach((section, index) => {
    const card = personaEl("article", "persona-section-card");
    const head = personaEl("div", "persona-section-card-head");
    head.appendChild(personaEl("h4", "persona-entry-name", section.title || "未命名设定"));
    card.appendChild(head);
    card.appendChild(personaEl("p", "persona-entry-summary", section.content || "暂无正文"));
    const actions = personaEl("div", "persona-entry-actions");
    actions.appendChild(personaSmallButton("改名", () => renamePersonaSection(section, index)));
    actions.appendChild(personaSmallButton("编辑", () => editPersonaSection(section, index)));
    actions.appendChild(personaSmallButton("上移", () => movePersonaSection(index, -1)));
    actions.appendChild(personaSmallButton("下移", () => movePersonaSection(index, 1)));
    actions.appendChild(personaSmallButton("删除", () => deletePersonaSection(section), "persona-action persona-delete"));
    card.appendChild(actions);
    list.appendChild(card);
  });
}

function personaRenderEntries() {
  const list = personaNode("persona-entries");
  personaClear(list);
  const entries = personaView.snapshot ? personaView.snapshot.persona.entries : [];
  const enabled = entries.filter((item) => item.enabled).length;
  personaNode("persona-entry-count").textContent = personaView.snapshot
    ? entries.length + " 个条目 · " + enabled + " 个已启用" : "";
  if (!personaView.snapshot) {
    list.appendChild(personaEl("p", "persona-empty", "正在载入条目…"));
    return;
  }
  if (!entries.length) {
    list.appendChild(personaEl("p", "persona-empty", "还没有条目。"));
    return;
  }
  entries.forEach((entry, index) => {
    const card = personaEl("article", "persona-entry" + (entry.enabled ? "" : " is-disabled"));
    const head = personaEl("div", "persona-entry-head");
    head.appendChild(personaEl("h4", "persona-entry-name", entry.name || "未命名条目"));
    const toggle = personaEl("label", "persona-toggle");
    const check = personaEl("input");
    check.type = "checkbox";
    check.checked = entry.enabled;
    check.disabled = personaView.saving;
    check.addEventListener("change", () => togglePersonaEntry(entry, check.checked));
    toggle.appendChild(check);
    toggle.appendChild(personaEl("span", "", "启用"));
    head.appendChild(toggle);
    card.appendChild(head);
    card.appendChild(personaEl("p", "persona-entry-summary", entry.description || "暂无摘要"));
    const actions = personaEl("div", "persona-entry-actions");
    actions.appendChild(personaSmallButton("编辑", () => openPersonaEntry(entry)));
    actions.appendChild(personaSmallButton("删除", () => deletePersonaEntry(entry), "persona-action persona-delete"));
    card.appendChild(actions);
    list.appendChild(card);
  });
}

function personaRenderBusy() {
  const saving = personaView.saving;
  personaNode("persona-refresh").disabled = saving;
  personaNode("persona-add-section").disabled = saving || !personaView.snapshot;
  personaNode("persona-add").disabled = saving || !personaView.snapshot;
  const save = personaNode("persona-entry-save");
  save.disabled = saving || !personaView.editing;
  save.textContent = saving && personaView.savingKind === "entry" ? "保存中…" : "保存条目";
  personaNode("persona-entry-cancel").disabled = saving;
  const generate = personaNode("persona-summary-generate");
  generate.disabled = saving || personaView.summaryGenerating;
  generate.textContent = personaView.summaryGenerating ? "生成中…" : "生成摘要建议";
  for (const node of [
    "persona-name", "persona-description", "persona-body", "persona-enabled",
  ]) personaNode(node).disabled = saving;
  const sectionSave = personaNode("persona-section-save");
  sectionSave.disabled = saving || !personaView.sectionEditing;
  sectionSave.textContent = saving && personaView.savingKind === "section" ? "保存中…" : "保存设定";
  personaNode("persona-section-cancel").disabled = saving;
  for (const node of ["persona-section-title", "persona-section-content"]) {
    personaNode(node).disabled = saving;
  }
  for (const node of personaNode("persona-sections").querySelectorAll("button")) node.disabled = saving;
  for (const node of personaNode("persona-entries").querySelectorAll("button")) node.disabled = saving;
  for (const node of personaNode("persona-entries").querySelectorAll("input")) node.disabled = saving;
}

function personaApply(value) {
  personaView.snapshot = personaNormalize(value);
  personaRenderSections();
  personaRenderEntries();
  personaRenderPreview();
  personaRenderLast();
  personaRenderBusy();
}

function personaHideSuggestion() {
  personaView.suggestion = "";
  personaNode("persona-suggestion-text").textContent = "";
  personaNode("persona-suggestion").hidden = true;
}

function personaResetEntry() {
  personaView.closingEntry = true;
  personaView.editing = null;
  personaView.summaryGenerating = false;
  personaHideSuggestion();
  const dialog = personaNode("persona-entry-dialog");
  dialog.hidden = true;
  if (dialog.open && dialog.close) {
    dialog.close();
  }
  for (const node of ["persona-name", "persona-description", "persona-body"]) personaNode(node).value = "";
  personaNode("persona-enabled").checked = true;
  personaError("persona-form-error", "");
  personaStatus("persona-summary-status", "");
  personaView.closingEntry = false;
}

function personaResetSection() {
  personaView.sectionEditing = null;
  const dialog = personaNode("persona-section-dialog");
  personaView.closingSection = true;
  if (dialog.open && dialog.close) {
    dialog.close();
  }
  dialog.hidden = true;
  personaNode("persona-section-title").value = "";
  personaNode("persona-section-content").value = "";
  personaError("persona-section-error", "");
  personaView.closingSection = false;
}

function openPersonaEntry(entry) {
  if (personaView.saving || !personaView.snapshot) return;
  personaView.editSeq += 1;
  personaView.editing = entry ? { mode: "entry", id: entry.id } : { mode: "new" };
  personaView.summaryGenerating = false;
  personaHideSuggestion();
  personaError("persona-form-error", "");
  personaStatus("persona-summary-status", "");
  personaNode("persona-form-title").textContent = entry ? "编辑条目" : "新建条目";
  personaNode("persona-name").value = entry ? entry.name : "";
  personaNode("persona-description").value = entry ? entry.description : "";
  personaNode("persona-body").value = entry ? entry.body : "";
  personaNode("persona-enabled").checked = entry ? entry.enabled : true;
  personaNode("persona-entry-dialog").hidden = false;
  const dialog = personaNode("persona-entry-dialog");
  if (!dialog.open && dialog.showModal) dialog.showModal();
  personaRenderBusy();
  const name = personaNode("persona-name");
  if (name.focus) name.focus();
}

function closePersonaEntry() {
  personaView.editSeq += 1;
  personaResetEntry();
  personaRenderBusy();
}

function personaCloseEntryAfterSave() {
  personaView.editSeq += 1;
  const dialog = personaNode("persona-entry-dialog");
  dialog.hidden = true;
  if (dialog.open && dialog.close) {
    personaView.closingEntry = true;
    dialog.close();
    personaView.closingEntry = false;
  }
  personaResetEntry();
}

function openPersonaSection(section, mode) {
  if (personaView.saving || !personaView.snapshot) return;
  personaView.sectionEditing = section ? { id: section.id, mode } : { mode: "new" };
  personaError("persona-section-error", "");
  personaNode("persona-section-title").value = section ? section.title : "";
  personaNode("persona-section-content").value = section ? section.content : "";
  personaNode("persona-section-dialog").hidden = false;
  const dialog = personaNode("persona-section-dialog");
  if (!dialog.open && dialog.showModal) dialog.showModal();
  personaRenderBusy();
  const title = personaNode("persona-section-title");
  if (title.focus) title.focus();
}

function editPersonaSection(section) {
  openPersonaSection(section, "edit");
}

function renamePersonaSection(section) {
  openPersonaSection(section, "rename");
  personaStatus("persona-section-status", "只修改标题，正文和 id 保持不变。");
}

async function loadPersona(reason) {
  const viewSeq = personaView.viewSeq;
  personaStatus("persona-snapshot-status", reason === "refresh" ? "正在刷新…" : "正在载入…");
  personaError("persona-load-error", "");
  try {
    const value = await personaRequest("/api/persona");
    if (!personaCurrent(viewSeq)) return;
    personaApply(value);
    personaStatus("persona-snapshot-status", reason === "refresh" ? "已刷新当前人设。" : "已载入当前人设。", "ok");
  } catch (error) {
    if (!personaCurrent(viewSeq)) return;
    personaError("persona-load-error", "读取失败：" + error.message);
    personaStatus("persona-snapshot-status", "读取失败", "error");
  }
}

function openPersona() {
  if (personaView.open) return;
  personaView.open = true;
  personaView.viewSeq += 1;
  personaView.editSeq += 1;
  personaView.snapshot = null;
  personaView.saving = false;
  personaView.savingKind = "";
  personaResetEntry();
  personaResetSection();
  personaNode("persona-sections").textContent = "";
  personaNode("persona-entries").textContent = "";
  personaNode("persona-entry-count").textContent = "";
  personaNode("persona-preview").textContent = "";
  personaNode("persona-last").textContent = "";
  for (const node of [
    "persona-load-error", "persona-snapshot-status", "persona-section-status",
    "persona-list-status", "persona-summary-status",
  ]) personaStatus(node, "");
  personaRenderPreview();
  personaRenderLast();
  personaRenderBusy();
  const dialog = personaNode("persona-dialog");
  if (!dialog.open && dialog.showModal) dialog.showModal();
  loadPersona("open");
}

function closePersona() {
  if (!personaView.open) return;
  personaView.open = false;
  personaView.viewSeq += 1;
  personaView.editSeq += 1;
  personaView.snapshot = null;
  personaView.saving = false;
  personaView.savingKind = "";
  personaResetEntry();
  personaResetSection();
  personaNode("persona-sections").textContent = "";
  personaNode("persona-entries").textContent = "";
  personaNode("persona-entry-count").textContent = "";
  personaNode("persona-preview").textContent = "";
  personaNode("persona-last").textContent = "";
  personaStatus("persona-load-error", "");
  personaStatus("persona-snapshot-status", "");
  personaStatus("persona-section-status", "");
  personaStatus("persona-list-status", "");
  personaStatus("persona-summary-status", "");
  personaRenderBusy();
  const dialog = personaNode("persona-dialog");
  if (dialog.open && dialog.close) dialog.close();
}

async function personaSave(kind, mutate, done) {
  if (personaView.saving || !personaView.snapshot) return;
  const viewSeq = personaView.viewSeq;
  const previous = personaDocument();
  if (!mutate(previous)) return;
  personaView.saving = true;
  personaView.savingKind = kind;
  personaRenderBusy();
  try {
    const value = await personaRequest("/api/persona", "PUT", previous);
    if (!personaCurrent(viewSeq)) return;
    personaApply(value);
    if (done) done();
    personaRenderSections();
    personaRenderEntries();
  } catch (error) {
    if (personaCurrent(viewSeq)) personaStatus("persona-list-status", "保存失败：" + error.message, "error");
    throw error;
  } finally {
    if (personaCurrent(viewSeq)) {
      personaView.saving = false;
      personaView.savingKind = "";
      personaRenderBusy();
    }
  }
}

async function savePersonaSection(event) {
  if (event) event.preventDefault();
  if (!personaView.sectionEditing || personaView.saving || !personaView.snapshot) return;
  const current = personaView.sectionEditing;
  const title = personaNode("persona-section-title").value.trim();
  const content = personaNode("persona-section-content").value;
  if (!title) {
    personaError("persona-section-error", "请填写设定标题。");
    personaNode("persona-section-title").focus();
    return;
  }
  personaError("persona-section-error", "");
  try {
    await personaSave("section", (documentValue) => {
      if (current.mode === "new") {
        documentValue.sections.push({ title, content });
        return true;
      }
      const section = documentValue.sections.find((item) => String(item.id) === String(current.id));
      if (!section) {
        personaError("persona-section-error", "这个设定已不存在，请刷新后重试。");
        return false;
      }
      section.title = title;
      if (current.mode === "edit") section.content = content;
      return true;
    }, () => {
      closePersonaSection();
      personaStatus("persona-section-status", current.mode === "rename" ? "设定已改名，原 id 保留。" : "设定已保存。", "ok");
    });
  } catch (error) {
    personaError("persona-section-error", "保存失败：" + error.message);
  }
}

function closePersonaSection() {
  personaView.sectionEditing = null;
  const dialog = personaNode("persona-section-dialog");
  if (dialog.open && dialog.close) dialog.close();
  dialog.hidden = true;
  personaNode("persona-section-title").value = "";
  personaNode("persona-section-content").value = "";
  personaError("persona-section-error", "");
  personaRenderBusy();
}

async function movePersonaSection(index, direction) {
  const target = index + direction;
  if (target < 0 || target >= personaView.snapshot.persona.sections.length) return;
  await personaSave("section", (documentValue) => {
    const [section] = documentValue.sections.splice(index, 1);
    documentValue.sections.splice(target, 0, section);
    return true;
  }, () => personaStatus("persona-section-status", "设定顺序已保存。", "ok"));
}

async function deletePersonaSection(section) {
  if (!confirm("删除设定“" + (section.title || "未命名设定") + "”？")) return;
  try {
    await personaSave("section", (documentValue) => {
      documentValue.sections = documentValue.sections.filter((item) => String(item.id) !== String(section.id));
      return true;
    }, () => personaStatus("persona-section-status", "设定已删除。", "ok"));
  } catch (error) {
    personaStatus("persona-section-status", "删除失败：" + error.message, "error");
  }
}

async function savePersonaEntry(event) {
  if (event) event.preventDefault();
  if (!personaView.editing || personaView.saving || !personaView.snapshot) return;
  const name = personaNode("persona-name").value.trim();
  const description = personaNode("persona-description").value.trim();
  const body = personaNode("persona-body").value;
  const enabled = personaNode("persona-enabled").checked;
  if (!PERSONA_SLUG.test(name)) {
    personaError("persona-form-error", "名称需为英文 slug，例如 writing-style。");
    personaNode("persona-name").focus();
    return;
  }
  if (!description) {
    personaError("persona-form-error", "请填写摘要，摘要不能为空。");
    personaNode("persona-description").focus();
    return;
  }
  personaError("persona-form-error", "");
  const current = personaView.editing;
  try {
    await personaSave("entry", (documentValue) => {
      if (current.mode === "new") {
        documentValue.entries.push({ name, description, body, enabled });
        return true;
      }
      const entry = documentValue.entries.find((item) => String(item.id) === String(current.id));
      if (!entry) {
        personaError("persona-form-error", "这个条目已不存在，请刷新后重试。");
        return false;
      }
      Object.assign(entry, { name, description, body, enabled });
      return true;
    }, () => {
      personaCloseEntryAfterSave();
      personaStatus("persona-list-status", "条目已保存。", "ok");
    });
  } catch (error) {
    personaError("persona-form-error", "保存失败：" + error.message);
  }
}

async function togglePersonaEntry(entry, enabled) {
  try {
    await personaSave("entry", (documentValue) => {
      const item = documentValue.entries.find((candidate) => String(candidate.id) === String(entry.id));
      if (item) item.enabled = enabled;
      return Boolean(item);
    }, () => personaStatus("persona-list-status", enabled ? "条目已启用。" : "条目已停用。", "ok"));
  } catch (error) {
    personaRenderEntries();
    personaStatus("persona-list-status", "更新失败：" + error.message, "error");
  }
}

async function deletePersonaEntry(entry) {
  if (!confirm("删除条目“" + (entry.name || "未命名条目") + "”？")) return;
  try {
    await personaSave("entry", (documentValue) => {
      documentValue.entries = documentValue.entries.filter((item) => String(item.id) !== String(entry.id));
      return true;
    }, () => personaStatus("persona-list-status", "条目已删除。", "ok"));
  } catch (error) {
    personaStatus("persona-list-status", "删除失败：" + error.message, "error");
  }
}

async function generatePersonaSummary() {
  if (personaView.summaryGenerating || personaView.saving || !personaView.editing) return;
  const name = personaNode("persona-name").value.trim();
  const body = personaNode("persona-body").value.trim();
  if (!PERSONA_SLUG.test(name)) {
    personaStatus("persona-summary-status", "请先填写英文 slug 名称。", "error");
    personaNode("persona-name").focus();
    return;
  }
  if (!body) {
    personaStatus("persona-summary-status", "请先填写正文，才能生成摘要建议。", "error");
    personaNode("persona-body").focus();
    return;
  }
  const editSeq = personaView.editSeq;
  personaView.summaryGenerating = true;
  personaStatus("persona-summary-status", "正在生成摘要建议…");
  personaRenderBusy();
  try {
    const value = await personaRequest("/api/persona/summary", "POST", { name, body });
    if (!personaView.open || personaView.editSeq !== editSeq) return;
    const summary = personaText(value.summary).trim();
    if (!summary) {
      personaStatus("persona-summary-status", "生成失败：接口没有返回摘要。可以重试。", "error");
      return;
    }
    personaView.suggestion = summary;
    personaNode("persona-suggestion-text").textContent = summary;
    personaNode("persona-suggestion").hidden = false;
    personaStatus("persona-summary-status", "建议已生成，尚未保存。采用后仍需保存条目。", "ok");
  } catch (error) {
    if (!personaView.open || personaView.editSeq !== editSeq) return;
    personaStatus("persona-summary-status", "生成失败：" + error.message + "。输入已保留，可以重试。", "error");
  } finally {
    if (personaView.open && personaView.editSeq === editSeq) {
      personaView.summaryGenerating = false;
      personaRenderBusy();
    }
  }
}

function adoptPersonaSuggestion() {
  if (!personaView.suggestion) return;
  personaNode("persona-description").value = personaView.suggestion;
  personaHideSuggestion();
  personaStatus("persona-summary-status", "建议已写入摘要编辑区，保存条目后才会生效。", "ok");
  personaNode("persona-description").focus();
}

function discardPersonaSuggestion() {
  personaHideSuggestion();
  personaStatus("persona-summary-status", "已丢弃摘要建议。");
}

personaNode("persona-open").addEventListener("click", openPersona);
personaNode("persona-close").addEventListener("click", closePersona);
personaNode("persona-refresh").addEventListener("click", () => {
  if (!personaView.saving) loadPersona("refresh");
});
personaNode("persona-add-section").addEventListener("click", () => openPersonaSection(null, "new"));
personaNode("persona-section-cancel").addEventListener("click", () => {
  closePersonaSection();
});
personaNode("persona-section-form").addEventListener("submit", savePersonaSection);
personaNode("persona-add").addEventListener("click", () => openPersonaEntry(null));
personaNode("persona-entry-cancel").addEventListener("click", closePersonaEntry);
personaNode("persona-entry-form").addEventListener("submit", savePersonaEntry);
personaNode("persona-summary-generate").addEventListener("click", generatePersonaSummary);
personaNode("persona-adopt").addEventListener("click", adoptPersonaSuggestion);
personaNode("persona-discard").addEventListener("click", discardPersonaSuggestion);
personaNode("persona-dialog").addEventListener("close", () => {
  if (personaView.open) closePersona();
});
personaNode("persona-entry-dialog").addEventListener("close", () => {
  if (personaView.open && !personaView.closingEntry) closePersonaEntry();
});
personaNode("persona-section-dialog").addEventListener("close", () => {
  if (personaView.open && !personaView.closingSection) {
    closePersonaSection();
  }
});
personaNode("persona-entry-dialog-close").addEventListener("click", closePersonaEntry);
personaNode("persona-section-dialog-close").addEventListener("click", () => {
  closePersonaSection();
});
