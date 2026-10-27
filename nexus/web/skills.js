"use strict";

// Skills are edited from one dialog. The listing always lets a project skill
// shadow a same-name personal skill, while built-ins stay visible and read-only.

const skillsView = {
  open: false,
  seq: 0,
  skills: [],
  editing: null,
  saving: false,
  readonly: false,
};

const SKILL_SLUG = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function skillsNode(id) {
  return document.getElementById(id);
}

function skillsEl(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function skillsClear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function skillsText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function skillsStatus(message, tone) {
  const node = skillsNode("skills-status");
  node.className = "small skills-status" + (tone ? " skills-status-" + tone : "");
  node.textContent = message || "";
}

function skillsError(id, message) {
  const node = skillsNode(id);
  node.textContent = message || "";
  node.hidden = !message;
}

async function skillsRequest(path, method, body) {
  const options = { method: method || "GET" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || response.statusText || "请求失败");
  return payload;
}

function skillList(value) {
  if (!value || !Array.isArray(value.skills)) throw new Error("返回数据缺少 skills");
  return value.skills.filter((item) => item && typeof item === "object").map((item) => ({
    source: skillsText(item.source),
    path: skillsText(item.path),
    name: skillsText(item.name),
    description: skillsText(item.description),
    body: skillsText(item.body),
    effective: item.effective !== false,
    read_only: item.read_only === true,
  }));
}

function skillSourceLabel(source) {
  return { personal: "个人", project: "项目", builtin: "内置" }[source] || skillsText(source);
}

function skillsRender() {
  const list = skillsNode("skills-list");
  skillsClear(list);
  const skills = skillsView.skills;
  skillsNode("skills-count").textContent = skills.length ? skills.length + " 个技能" : "";
  if (!skills.length) {
    list.appendChild(skillsEl("p", "skills-empty", "还没有技能。"));
    return;
  }
  for (const skill of skills) {
    const card = skillsEl("article", "skills-entry" + (skill.effective ? "" : " is-disabled"));
    const head = skillsEl("div", "skills-entry-head");
    head.appendChild(skillsEl("h4", "skills-entry-name", skill.name || "未命名技能"));
    head.appendChild(skillsEl("span", "skill-source", skillSourceLabel(skill.source)));
    card.appendChild(head);
    card.appendChild(skillsEl("p", "skills-entry-summary", skill.description || "暂无摘要"));
    const meta = skillsEl("p", "skill-meta", skill.effective ? "当前生效" : "被更高优先级同名技能覆盖");
    card.appendChild(meta);
    const actions = skillsEl("div", "skills-entry-actions");
    const view = skillsEl("button", "skills-action", skill.read_only ? "查看" : "编辑");
    view.type = "button";
    view.addEventListener("click", () => openSkillEditor(skill, skill.read_only));
    actions.appendChild(view);
    if (!skill.read_only) {
      const remove = skillsEl("button", "skills-action skills-delete", "删除");
      remove.type = "button";
      remove.addEventListener("click", () => deleteSkill(skill));
      actions.appendChild(remove);
    }
    card.appendChild(actions);
    list.appendChild(card);
  }
}

function skillsRenderBusy() {
  skillsNode("skills-refresh").disabled = skillsView.saving;
  skillsNode("skills-new").disabled = skillsView.saving;
  const save = skillsNode("skill-save");
  save.hidden = skillsView.readonly;
  save.disabled = skillsView.saving;
  save.textContent = skillsView.saving ? "保存中…" : "保存技能";
  skillsNode("skill-cancel").disabled = skillsView.saving;
  for (const id of ["skill-name", "skill-description", "skill-body", "skill-source"]) {
    const node = skillsNode(id);
    if (id === "skill-source") {
      node.disabled = skillsView.saving || skillsView.readonly || Boolean(skillsView.editing);
    } else {
      node.readOnly = skillsView.readonly;
      node.disabled = skillsView.saving;
    }
  }
  for (const button of skillsNode("skills-list").querySelectorAll("button")) button.disabled = skillsView.saving;
}

function skillsApply(value) {
  skillsView.skills = skillList(value);
  skillsRender();
  skillsRenderBusy();
}

async function loadSkills() {
  const seq = ++skillsView.seq;
  skillsStatus("正在载入技能…");
  try {
    const value = await skillsRequest("/api/skills");
    if (!skillsView.open || skillsView.seq !== seq) return;
    skillsApply(value);
    skillsStatus("已载入技能。", "ok");
  } catch (error) {
    if (!skillsView.open || skillsView.seq !== seq) return;
    skillsStatus("读取失败：" + error.message, "error");
  }
}

function closeSkillEditor() {
  skillsView.seq += 1;
  skillsView.editing = null;
  skillsView.readonly = false;
  const dialog = skillsNode("skill-dialog");
  dialog.hidden = true;
  if (dialog.open && dialog.close) dialog.close();
  skillsNode("skill-name").value = "";
  skillsNode("skill-description").value = "";
  skillsNode("skill-body").value = "";
  skillsNode("skill-source").value = "personal";
  skillsError("skill-error", "");
  skillsRenderBusy();
}

function openSkillEditor(skill, readonly) {
  if (skillsView.saving) return;
  skillsView.seq += 1;
  skillsView.editing = skill ? { source: skill.source, path: skill.path } : null;
  skillsView.readonly = Boolean(readonly && skill);
  skillsNode("skill-dialog-title").textContent = skillsView.readonly
    ? "查看技能" : (skill ? "编辑技能" : "新建技能");
  skillsNode("skill-name").value = skill ? skill.name : "";
  skillsNode("skill-description").value = skill ? skill.description : "";
  skillsNode("skill-body").value = skill ? skill.body : "";
  skillsNode("skill-source").value = skill ? skill.source : "personal";
  skillsError("skill-error", "");
  skillsNode("skill-dialog").hidden = false;
  const dialog = skillsNode("skill-dialog");
  if (!dialog.open && dialog.showModal) dialog.showModal();
  skillsRenderBusy();
  const name = skillsNode("skill-name");
  if (name.focus) name.focus();
}

async function saveSkill(event) {
  if (event) event.preventDefault();
  if (skillsView.saving || skillsView.readonly) return;
  const name = skillsNode("skill-name").value.trim();
  const description = skillsNode("skill-description").value.trim();
  const body = skillsNode("skill-body").value;
  const source = skillsView.editing ? skillsView.editing.source : skillsNode("skill-source").value;
  if (!SKILL_SLUG.test(name)) {
    skillsError("skill-error", "名称需为英文 slug，例如 writing-style。");
    skillsNode("skill-name").focus();
    return;
  }
  if (!description) {
    skillsError("skill-error", "请填写摘要，摘要不能为空。");
    skillsNode("skill-description").focus();
    return;
  }
  if (!["personal", "project"].includes(source)) {
    skillsError("skill-error", "请选择个人或项目来源。");
    return;
  }
  const payload = { source, name, description, body };
  if (skillsView.editing) payload.path = skillsView.editing.path;
  const editSeq = skillsView.seq;
  skillsView.saving = true;
  skillsError("skill-error", "");
  skillsStatus("正在保存技能…");
  skillsRenderBusy();
  try {
    const value = await skillsRequest("/api/skills", "POST", payload);
    if (!skillsView.open || skillsView.seq !== editSeq) return;
    skillsApply(value);
    skillsView.saving = false;
    closeSkillEditor();
    skillsStatus("技能已保存。", "ok");
  } catch (error) {
    if (!skillsView.open || skillsView.seq !== editSeq) return;
    skillsError("skill-error", "保存失败：" + error.message);
    skillsStatus("保存失败", "error");
  } finally {
    if (skillsView.seq === editSeq) {
      skillsView.saving = false;
      skillsRenderBusy();
    }
  }
}

async function deleteSkill(skill) {
  if (skillsView.saving) return;
  if (!confirm("删除技能“" + (skill.name || "未命名技能") + "”？")) return;
  const seq = skillsView.seq;
  skillsView.saving = true;
  skillsStatus("正在删除技能…");
  skillsRenderBusy();
  try {
    const value = await skillsRequest("/api/skills/delete", "POST", {
      source: skill.source,
      path: skill.path,
    });
    if (!skillsView.open || skillsView.seq !== seq) return;
    skillsApply(value);
    skillsStatus("技能已删除。", "ok");
  } catch (error) {
    if (!skillsView.open || skillsView.seq !== seq) return;
    skillsStatus("删除失败：" + error.message, "error");
  } finally {
    if (skillsView.seq === seq) {
      skillsView.saving = false;
      skillsRenderBusy();
    }
  }
}

function openSkills() {
  if (skillsView.open) return;
  skillsView.open = true;
  skillsView.seq += 1;
  skillsView.skills = [];
  skillsView.saving = false;
  closeSkillEditor();
  skillsNode("skills-list").textContent = "";
  skillsNode("skills-count").textContent = "";
  skillsStatus("");
  const dialog = skillsNode("skills-dialog");
  if (!dialog.open && dialog.showModal) dialog.showModal();
  loadSkills();
}

function closeSkills() {
  if (!skillsView.open) return;
  skillsView.open = false;
  skillsView.seq += 1;
  skillsView.skills = [];
  closeSkillEditor();
  skillsNode("skills-list").textContent = "";
  skillsNode("skills-count").textContent = "";
  skillsStatus("");
  const dialog = skillsNode("skills-dialog");
  if (dialog.open && dialog.close) dialog.close();
}

skillsNode("skills-open").addEventListener("click", openSkills);
skillsNode("skills-close").addEventListener("click", closeSkills);
skillsNode("skills-refresh").addEventListener("click", () => {
  if (!skillsView.saving) loadSkills();
});
skillsNode("skills-new").addEventListener("click", () => openSkillEditor(null));
skillsNode("skill-cancel").addEventListener("click", closeSkillEditor);
skillsNode("skill-dialog-close").addEventListener("click", closeSkillEditor);
skillsNode("skill-form").addEventListener("submit", saveSkill);
skillsNode("skills-dialog").addEventListener("close", () => {
  if (skillsView.open) closeSkills();
});
