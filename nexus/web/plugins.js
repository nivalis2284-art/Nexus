"use strict";

// Plugin center: management state and the seven-day model request browser.
// Plugin business pages are isolated in an iframe; this file never evaluates
// request bodies or other plugin-provided text as HTML.

const pluginCenterView = {
  open: false,
  seq: 0,
  tab: "plugins",
  snapshot: null,
  plugins: [],
  selectedId: null,
  loading: false,
  action: null,
  importBusy: false,
  importFile: null,
  importConflict: null,
  settingsPluginId: null,
  settingsDirty: false,
  settingsSaving: false,
  settingsInputs: new Map(),
  framePluginId: null,
  framePage: null,
  drainingTimer: null,
  requestsLoaded: false,
  requestsLoading: false,
  requests: [],
  requestsError: "",
  requestOwner: "",
  requestKind: "",
  requestNext: null,
  requestSeq: 0,
  requestDetailSeq: 0,
  selectedRequestId: null,
};

function pluginCenterNode(id) {
  return document.getElementById(id);
}

function pluginCenterEl(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function pluginCenterClear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function pluginCenterText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function pluginCenterJsonText(value) {
  if (value === undefined) return "";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function pluginCenterFormatTime(value) {
  if (!value) return "时间未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return pluginCenterText(value);
  return date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}

function pluginCenterStatus(plugin) {
  const raw = pluginCenterText(plugin && plugin.status).trim();
  const lower = raw.toLowerCase();
  if (lower === "draining") return { label: "正在收尾", tone: "draining" };
  if (lower === "closing") return { label: "正在释放", tone: "draining" };
  if (lower === "loading") return { label: "加载中", tone: "draining" };
  if (lower === "ready") return { label: "已加载", tone: "on" };
  if (lower === "unloaded") return { label: "未启用", tone: "off" };
  if (lower === "error" || lower === "failed") {
    return { label: raw || "错误", tone: "error" };
  }
  if (plugin && plugin.enabled) return { label: raw || "已加载", tone: "on" };
  return { label: raw || "未启用", tone: "off" };
}

function pluginCenterSetStatus(message, tone) {
  const node = pluginCenterNode("plugins-status");
  node.className = "plugins-global-status" + (tone ? " is-" + tone : "");
  node.textContent = message || "";
  node.hidden = !message;
}

function pluginCenterSetError(id, message) {
  const node = pluginCenterNode(id);
  if (!node) return;
  node.textContent = message || "";
  node.hidden = !message;
}

async function pluginCenterRequest(path, options) {
  const response = await fetch(path, options || {});
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) {
    const error = new Error(payload.error || response.statusText || "请求失败");
    error.status = response.status;
    error.payload = payload;
    throw error;
  }
  return payload;
}

function pluginCenterJsonRequest(path, method, body) {
  const options = { method: method || "GET" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  return pluginCenterRequest(path, options);
}

function pluginCenterNormalizeSnapshot(value) {
  if (!value || !Array.isArray(value.plugins)) {
    throw new Error("返回数据缺少插件列表");
  }
  return {
    root: pluginCenterText(value.root),
    plugins: value.plugins.filter((plugin) => plugin && plugin.id !== undefined),
  };
}

function pluginCenterSelected() {
  if (pluginCenterView.selectedId === null) return null;
  return pluginCenterView.plugins.find(
    (plugin) => String(plugin.id) === String(pluginCenterView.selectedId),
  ) || null;
}

function pluginCenterPageUrl(pluginId, path) {
  const segments = pluginCenterText(path).split("/").filter(Boolean)
    .map((segment) => encodeURIComponent(segment));
  return "/plugins/" + encodeURIComponent(pluginCenterText(pluginId)) + "/" +
    segments.join("/");
}

function pluginCenterSettingsProperties(plugin) {
  const schema = plugin && plugin.settings_schema;
  if (!schema || typeof schema !== "object" || !schema.properties ||
      typeof schema.properties !== "object" || Array.isArray(schema.properties)) {
    return [];
  }
  return Object.keys(schema.properties)
    .filter((key) => schema.properties[key] && typeof schema.properties[key] === "object")
    .map((key) => ({ key, schema: schema.properties[key] }));
}

function pluginCenterFieldValue(plugin, key, schema) {
  const settings = plugin && plugin.settings && typeof plugin.settings === "object"
    ? plugin.settings : {};
  if (Object.prototype.hasOwnProperty.call(settings, key)) return settings[key];
  if (Object.prototype.hasOwnProperty.call(schema, "default")) return schema.default;
  if (schema.type === "boolean") return false;
  return "";
}

function pluginCenterMarkSettingsDirty() {
  pluginCenterView.settingsDirty = true;
  const node = pluginCenterNode("plugin-settings-status");
  node.className = "small plugin-settings-status is-warn";
  node.textContent = "有未保存改动，保存后立即生效。";
  node.hidden = false;
}

function pluginCenterBuildSettingField(plugin, key, schema) {
  const field = pluginCenterEl("div", "plugin-settings-field");
  const label = pluginCenterEl("label");
  label.appendChild(pluginCenterEl("span", "plugin-settings-title",
    pluginCenterText(schema.title) || key));

  const value = pluginCenterFieldValue(plugin, key, schema);
  const type = pluginCenterText(schema.type).toLowerCase();
  let control = null;

  if (type === "boolean") {
    control = document.createElement("input");
    control.type = "checkbox";
    control.checked = value === true;
    const wrap = pluginCenterEl("span", "plugin-settings-check");
    wrap.appendChild(control);
    wrap.appendChild(pluginCenterEl("span", "", "启用"));
    label.appendChild(wrap);
    control.addEventListener("change", pluginCenterMarkSettingsDirty);
  } else if (Array.isArray(schema.enum) && schema.enum.length) {
    control = document.createElement("select");
    for (const choice of schema.enum) {
      const option = document.createElement("option");
      option.value = pluginCenterText(choice);
      option.textContent = pluginCenterText(choice);
      if (pluginCenterText(choice) === pluginCenterText(value)) option.selected = true;
      control.appendChild(option);
    }
    control.value = pluginCenterText(value);
    label.appendChild(control);
    control.addEventListener("change", pluginCenterMarkSettingsDirty);
  } else if (type === "string") {
    control = document.createElement("input");
    control.type = "text";
    control.value = pluginCenterText(value);
    label.appendChild(control);
    control.addEventListener("input", pluginCenterMarkSettingsDirty);
  } else if (type === "number" || type === "integer") {
    control = document.createElement("input");
    control.type = "number";
    control.step = type === "integer" ? "1" : "any";
    control.value = pluginCenterText(value);
    if (schema.minimum !== undefined) control.min = pluginCenterText(schema.minimum);
    if (schema.maximum !== undefined) control.max = pluginCenterText(schema.maximum);
    label.appendChild(control);
    control.addEventListener("input", pluginCenterMarkSettingsDirty);
  } else {
    field.appendChild(label);
    field.appendChild(pluginCenterEl("p", "plugin-settings-unsupported",
      "此设置类型暂不支持，当前值会原样保留：" + (type || "未声明类型")));
    return field;
  }

  field.appendChild(label);
  if (schema.description) {
    field.appendChild(pluginCenterEl("p", "plugin-settings-help",
      pluginCenterText(schema.description)));
  }
  pluginCenterView.settingsInputs.set(key, { node: control, schema, type });
  return field;
}

function pluginCenterRenderSettings(plugin) {
  const fields = pluginCenterNode("plugin-settings-fields");
  const save = pluginCenterNode("plugin-settings-save");
  const status = pluginCenterNode("plugin-settings-status");
  const properties = pluginCenterSettingsProperties(plugin);
  if (pluginCenterView.settingsPluginId === plugin.id &&
      pluginCenterView.settingsDirty) {
    return;
  }

  pluginCenterView.settingsPluginId = plugin.id;
  pluginCenterView.settingsDirty = false;
  pluginCenterView.settingsInputs.clear();
  pluginCenterClear(fields);
  status.className = "small plugin-settings-status";
  status.textContent = "";
  status.hidden = true;

  if (!properties.length) {
    fields.appendChild(pluginCenterEl("p", "plugin-settings-empty",
      "此插件没有声明可生成的设置项。"));
    save.hidden = true;
    save.disabled = true;
    return;
  }

  save.hidden = false;
  save.disabled = pluginCenterView.settingsSaving;
  for (const property of properties) {
    fields.appendChild(pluginCenterBuildSettingField(plugin, property.key, property.schema));
  }
}

function pluginCenterReadSettings(plugin) {
  const settings = plugin.settings && typeof plugin.settings === "object"
    ? Object.assign({}, plugin.settings) : {};
  for (const [key, entry] of pluginCenterView.settingsInputs) {
    const { node, schema, type } = entry;
    if (type === "boolean") {
      settings[key] = node.checked === true;
    } else if (Array.isArray(schema.enum) && schema.enum.length) {
      const selected = schema.enum.find(
        (choice) => pluginCenterText(choice) === pluginCenterText(node.value),
      );
      settings[key] = selected === undefined ? node.value : selected;
    } else if (type === "number" || type === "integer") {
      const raw = pluginCenterText(node.value).trim();
      if (!raw) throw new Error((schema.title || key) + " 需要填写数值");
      const number = Number(raw);
      if (!Number.isFinite(number) || (type === "integer" && !Number.isInteger(number))) {
        throw new Error((schema.title || key) + " 需要填写有效" +
          (type === "integer" ? "整数" : "数值"));
      }
      if (schema.minimum !== undefined && number < Number(schema.minimum)) {
        throw new Error((schema.title || key) + " 不能小于 " + schema.minimum);
      }
      if (schema.maximum !== undefined && number > Number(schema.maximum)) {
        throw new Error((schema.title || key) + " 不能大于 " + schema.maximum);
      }
      settings[key] = number;
    } else if (type === "string") {
      settings[key] = pluginCenterText(node.value);
    }
  }
  return settings;
}

async function pluginCenterSaveSettings(event) {
  if (event && event.preventDefault) event.preventDefault();
  const plugin = pluginCenterSelected();
  if (!plugin || pluginCenterView.settingsSaving) return;
  let settings;
  try {
    settings = pluginCenterReadSettings(plugin);
  } catch (error) {
    const status = pluginCenterNode("plugin-settings-status");
    status.className = "small plugin-settings-status is-error";
    status.textContent = error.message;
    status.hidden = false;
    return;
  }

  pluginCenterView.settingsSaving = true;
  const seq = pluginCenterView.seq;
  const status = pluginCenterNode("plugin-settings-status");
  status.className = "small plugin-settings-status";
  status.textContent = "正在保存设置…";
  status.hidden = false;
  pluginCenterNode("plugin-settings-save").disabled = true;
  try {
    const value = await pluginCenterJsonRequest(
      "/api/plugins/" + encodeURIComponent(plugin.id) + "/settings",
      "PUT",
      settings,
    );
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterView.settingsDirty = false;
    pluginCenterView.settingsPluginId = null;
    pluginCenterApplySnapshot(value);
    pluginCenterSetStatus("设置已保存并立即生效。", "ok");
  } catch (error) {
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    status.className = "small plugin-settings-status is-error";
    status.textContent = "保存失败：" + error.message;
    status.hidden = false;
  } finally {
    pluginCenterView.settingsSaving = false;
    const save = pluginCenterNode("plugin-settings-save");
    save.disabled = false;
  }
}

function pluginCenterRenderActions(plugin) {
  const box = pluginCenterNode("plugin-detail-actions");
  pluginCenterClear(box);
  if (!plugin) return;
  const busy = Boolean(pluginCenterView.action || pluginCenterView.importBusy);
  const add = (text, operation, className) => {
    const button = pluginCenterEl("button", className || "plugin-action", text);
    button.type = "button";
    button.disabled = busy;
    button.dataset.operation = operation;
    button.addEventListener("click", () => {
      if (operation === "load") pluginCenterLoadPlugin(plugin);
      else pluginCenterRunCommand(plugin, operation);
    });
    box.appendChild(button);
  };

  const status = pluginCenterText(plugin.status).toLowerCase();
  const draining = status === "draining" || status === "closing";
  const loading = status === "loading";
  if (draining) {
    box.appendChild(pluginCenterEl("span", "plugin-action-note",
      "插件正在收尾；页面会自动刷新状态，此时不能重复下发操作。"));
  } else if (loading) {
    add("停用", "unload");
  } else if (plugin.enabled) {
    add("停用", "unload");
    add("重载", "restart");
    add("停止在途", "stop");
  } else {
    add("加载", "load", "plugin-action plugin-action-primary");
  }
  const uninstall = pluginCenterEl("button", "plugin-action plugin-action-danger", "卸载");
  uninstall.type = "button";
  uninstall.disabled = busy || draining;
  uninstall.hidden = loading;
  uninstall.dataset.operation = "uninstall";
  uninstall.addEventListener("click", () => pluginCenterRunCommand(plugin, "uninstall"));
  box.appendChild(uninstall);

  if (pluginCenterView.action && String(pluginCenterView.action.id) === String(plugin.id)) {
    const label = {
      load: "正在加载…", unload: "正在停用…", restart: "正在重载…",
      stop: "正在停止在途操作…", uninstall: "正在卸载…",
    }[pluginCenterView.action.operation] || "正在处理…";
    box.appendChild(pluginCenterEl("span", "plugin-action-note", label));
  }
}

function pluginCenterRenderPages(plugin) {
  const box = pluginCenterNode("plugin-detail-pages");
  pluginCenterClear(box);
  const pages = Array.isArray(plugin.pages) ? plugin.pages : [];
  if (!pages.length) {
    box.appendChild(pluginCenterEl("p", "plugin-section-empty", "此插件没有提供功能页。"));
    return;
  }
  for (const page of pages) {
    if (!page || !page.path) continue;
    const row = pluginCenterEl("div", "plugin-page-row");
    const text = pluginCenterEl("div", "plugin-page-text");
    text.appendChild(pluginCenterEl("div", "plugin-page-title",
      pluginCenterText(page.title) || pluginCenterText(page.id) || "功能页"));
    text.appendChild(pluginCenterEl("div", "plugin-page-path", pluginCenterText(page.path)));
    row.appendChild(text);
    const open = pluginCenterEl("button", "plugin-secondary", "打开页面");
    open.type = "button";
    open.addEventListener("click", () => pluginCenterOpenPage(plugin, page));
    row.appendChild(open);
    box.appendChild(row);
  }
  if (!box.children.length) {
    box.appendChild(pluginCenterEl("p", "plugin-section-empty", "此插件没有提供功能页。"));
  }
}

function pluginCenterRenderCapabilities(plugin) {
  const box = pluginCenterNode("plugin-detail-capabilities");
  pluginCenterClear(box);
  const capabilities = Array.isArray(plugin.capabilities) ? plugin.capabilities : [];
  if (!capabilities.length) {
    box.appendChild(pluginCenterEl("span", "plugin-section-empty", "未声明能力"));
    return;
  }
  for (const capability of capabilities) {
    const text = capability && typeof capability === "object"
      ? (pluginCenterText(capability.title) || pluginCenterText(capability.name) ||
        pluginCenterText(capability.id) || pluginCenterJsonText(capability))
      : pluginCenterText(capability);
    const label = pluginCenterEl("span", "plugins-tag", text);
    if (capability && typeof capability === "object" && capability.description) {
      label.title = pluginCenterText(capability.description);
    }
    box.appendChild(label);
  }
}

function pluginCenterRenderDetail() {
  const empty = pluginCenterNode("plugins-empty");
  const detail = pluginCenterNode("plugin-detail");
  const frame = pluginCenterNode("plugin-frame-panel");
  const plugin = pluginCenterSelected();
  const progressNode = pluginCenterNode("plugin-detail-progress");
  if (!plugin) {
    progressNode.hidden = true;
    detail.hidden = true;
    frame.hidden = true;
    empty.hidden = false;
    empty.textContent = pluginCenterView.loading
      ? "正在读取插件…"
      : (pluginCenterView.plugins.length ? "选择一个插件查看详情。"
        : "还没有登记插件。可以上传 ZIP，或重新发现插件。");
    return;
  }

  if (pluginCenterView.framePluginId &&
      String(pluginCenterView.framePluginId) === String(plugin.id)) {
    empty.hidden = true;
    detail.hidden = true;
    frame.hidden = false;
  } else {
    empty.hidden = true;
    frame.hidden = true;
    detail.hidden = false;
  }

  pluginCenterNode("plugin-detail-title").textContent = pluginCenterText(plugin.name) || plugin.id;
  pluginCenterNode("plugin-detail-version").textContent =
    "v" + (pluginCenterText(plugin.version) || "未声明版本") +
    " · " + pluginCenterText(plugin.id);
  const pluginStatus = pluginCenterStatus(plugin);
  const state = pluginCenterNode("plugin-detail-state");
  state.className = "plugins-state is-" + pluginStatus.tone;
  state.textContent = pluginStatus.label;
  pluginCenterNode("plugin-detail-description").textContent =
    pluginCenterText(plugin.description) || "此插件没有填写简介。";

  const errorText = pluginCenterText(plugin.error);
  pluginCenterSetError("plugin-detail-error", errorText ? "插件错误：" + errorText : "");
  const meta = pluginCenterNode("plugin-detail-meta");
  pluginCenterClear(meta);
  const enabled = pluginCenterEl("span", "plugin-meta-item",
    plugin.enabled ? "已启用" : "未启用");
  meta.appendChild(enabled);
  meta.appendChild(pluginCenterEl("span", "plugin-meta-item",
    "在途调用 " + Number(plugin.active_calls || 0)));
  if (plugin.status) {
    meta.appendChild(pluginCenterEl("span", "plugin-meta-item",
      "状态 " + pluginCenterText(plugin.status)));
  }

  const progress = pluginCenterText(plugin.progress).trim();
  progressNode.textContent = progress;
  progressNode.hidden = !progress;

  pluginCenterRenderActions(plugin);
  pluginCenterRenderCapabilities(plugin);
  pluginCenterRenderPages(plugin);
  pluginCenterRenderSettings(plugin);
}

function pluginCenterRenderList() {
  const list = pluginCenterNode("plugins-list");
  pluginCenterClear(list);
  pluginCenterNode("plugins-count").textContent = pluginCenterView.plugins.length
    ? pluginCenterView.plugins.length + " 个" : "";
  if (!pluginCenterView.plugins.length) {
    list.appendChild(pluginCenterEl("p", "plugins-list-empty",
      pluginCenterView.loading ? "正在读取…" : "暂无插件。"));
    return;
  }
  for (const plugin of pluginCenterView.plugins) {
    const button = pluginCenterEl("button", "plugin-list-item");
    button.type = "button";
    button.dataset.pluginId = pluginCenterText(plugin.id);
    if (String(pluginCenterView.selectedId) === String(plugin.id)) {
      button.classList.add("is-selected");
    }
    const head = pluginCenterEl("div", "plugin-list-title-row");
    head.appendChild(pluginCenterEl("span", "plugin-list-name",
      pluginCenterText(plugin.name) || pluginCenterText(plugin.id)));
    const state = pluginCenterStatus(plugin);
    head.appendChild(pluginCenterEl("span", "plugins-state is-" + state.tone, state.label));
    button.appendChild(head);
    const sub = pluginCenterEl("div", "plugin-list-sub");
    sub.appendChild(pluginCenterEl("span", "", "v" + (pluginCenterText(plugin.version) || "?")));
    sub.appendChild(pluginCenterEl("span", "", pluginCenterText(plugin.id)));
    button.appendChild(sub);
    if (Number(plugin.active_calls || 0) > 0) {
      button.appendChild(pluginCenterEl("div", "plugin-list-calls",
        "在途 " + Number(plugin.active_calls)));
    }
    button.addEventListener("click", () => pluginCenterSelectPlugin(plugin.id));
    list.appendChild(button);
  }
}

function pluginCenterRenderBusy() {
  const busy = Boolean(pluginCenterView.action || pluginCenterView.importBusy);
  for (const id of ["plugins-refresh", "plugins-scan", "plugins-upload"]) {
    const node = pluginCenterNode(id);
    if (node) node.disabled = busy || pluginCenterView.loading;
  }
  const file = pluginCenterNode("plugins-file");
  if (file) file.disabled = busy;
}

function pluginCenterApplySnapshot(value) {
  const snapshot = pluginCenterNormalizeSnapshot(value);
  pluginCenterView.snapshot = snapshot;
  pluginCenterView.plugins = snapshot.plugins;
  pluginCenterNode("plugins-root").textContent = snapshot.root || "插件目录未提供";
  pluginCenterNode("plugins-root").title = snapshot.root || "";

  const previousSelection = pluginCenterView.selectedId;
  const selected = pluginCenterSelected();
  if (!selected) {
    pluginCenterView.selectedId = snapshot.plugins.length ? snapshot.plugins[0].id : null;
    pluginCenterView.settingsPluginId = null;
    pluginCenterView.settingsDirty = false;
    pluginCenterView.framePluginId = null;
    pluginCenterView.framePage = null;
  }
  if (pluginCenterView.selectedId !== previousSelection) {
    pluginCenterView.settingsPluginId = null;
    pluginCenterView.settingsDirty = false;
    pluginCenterView.framePluginId = null;
    pluginCenterView.framePage = null;
  }
  pluginCenterRenderList();
  pluginCenterRenderDetail();
  pluginCenterRenderBusy();
  pluginCenterScheduleDrainPoll();
}

async function pluginCenterRefresh(message) {
  if (pluginCenterView.loading) return;
  pluginCenterView.loading = true;
  const seq = pluginCenterView.seq;
  pluginCenterRenderList();
  pluginCenterRenderDetail();
  pluginCenterRenderBusy();
  if (message) pluginCenterSetStatus(message);
  try {
    const value = await pluginCenterRequest("/api/plugins", { method: "GET" });
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterApplySnapshot(value);
  } catch (error) {
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterSetStatus("读取插件失败：" + error.message, "error");
    pluginCenterNode("plugins-empty").textContent = "无法读取插件列表。";
  } finally {
    if (seq === pluginCenterView.seq) pluginCenterView.loading = false;
    if (pluginCenterView.open && seq === pluginCenterView.seq) {
      pluginCenterRenderList();
      pluginCenterRenderDetail();
      pluginCenterRenderBusy();
    }
  }
}

async function pluginCenterLoadPlugin(plugin) {
  if (!plugin || pluginCenterView.action || pluginCenterView.importBusy) return;
  pluginCenterView.action = { id: plugin.id, operation: "load" };
  const seq = pluginCenterView.seq;
  pluginCenterSetStatus("正在加载插件“" + (plugin.name || plugin.id) + "”…");
  pluginCenterRenderActions(plugin);
  pluginCenterRenderBusy();
  try {
    const value = await pluginCenterJsonRequest(
      "/api/plugins/" + encodeURIComponent(plugin.id) + "/commands",
      "POST",
      { operation: "load" },
    );
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterApplySnapshot(value);
    if (!pluginCenterNeedsPolling()) {
      pluginCenterSetStatus("加载请求已完成。", "ok");
    }
  } catch (error) {
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterSetStatus("加载失败：" + error.message, "error");
  } finally {
    if (seq === pluginCenterView.seq) pluginCenterView.action = null;
    if (pluginCenterView.open && seq === pluginCenterView.seq) {
      pluginCenterRenderList();
      pluginCenterRenderDetail();
      pluginCenterRenderBusy();
    }
  }
}

async function pluginCenterScan() {
  if (pluginCenterView.action || pluginCenterView.importBusy || pluginCenterView.loading) return;
  pluginCenterView.loading = true;
  const seq = pluginCenterView.seq;
  pluginCenterRenderBusy();
  pluginCenterSetStatus("正在发现插件…");
  try {
    const value = await pluginCenterJsonRequest("/api/plugins/scan", "POST", {});
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterApplySnapshot(value);
    pluginCenterSetStatus("发现完成，共 " + pluginCenterView.plugins.length + " 个插件。", "ok");
  } catch (error) {
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterSetStatus("发现插件失败：" + error.message, "error");
  } finally {
    if (seq === pluginCenterView.seq) pluginCenterView.loading = false;
    if (pluginCenterView.open && seq === pluginCenterView.seq) pluginCenterRenderBusy();
  }
}

function pluginCenterClearImportConflict() {
  pluginCenterView.importConflict = null;
  const notice = pluginCenterNode("plugins-import-notice");
  pluginCenterClear(notice);
  notice.hidden = true;
}

function pluginCenterShowImportConflict(file, payload) {
  const notice = pluginCenterNode("plugins-import-notice");
  pluginCenterClear(notice);
  pluginCenterView.importConflict = { file, payload };
  notice.hidden = false;
  notice.className = "plugins-import-notice is-conflict";
  notice.appendChild(pluginCenterEl("div", "plugins-import-title", "同名插件已经安装"));
  const plugin = payload && payload.plugin ? payload.plugin : {};
  notice.appendChild(pluginCenterEl("p", "plugins-import-copy",
    "插件 " + (pluginCenterText(plugin.name) || pluginCenterText(plugin.id) || "未知插件") +
    "（" + pluginCenterText(plugin.id) + "）已安装 v" +
    (pluginCenterText(payload && payload.installed_version) || "未知版本") +
    "，上传包为 v" + (pluginCenterText(plugin.version) || "未知版本") +
    "。替换会更新插件文件，资料默认保留。"));
  const actions = pluginCenterEl("div", "plugins-import-actions");
  const cancel = pluginCenterEl("button", "plugin-secondary", "取消");
  cancel.type = "button";
  cancel.addEventListener("click", () => {
    pluginCenterView.importFile = null;
    pluginCenterClearImportConflict();
    pluginCenterSetStatus("已取消替换版本。");
  });
  const replace = pluginCenterEl("button", "plugin-action plugin-action-primary", "替换版本");
  replace.type = "button";
  replace.id = "plugins-replace";
  replace.addEventListener("click", () => pluginCenterImport(file, true));
  actions.appendChild(cancel);
  actions.appendChild(replace);
  notice.appendChild(actions);
}

async function pluginCenterImport(file, replace) {
  if (!file || pluginCenterView.importBusy || pluginCenterView.action) return;
  if (typeof file.arrayBuffer !== "function") {
    pluginCenterSetStatus("无法读取所选文件。", "error");
    return;
  }
  pluginCenterView.importBusy = true;
  const seq = pluginCenterView.seq;
  pluginCenterRenderBusy();
  pluginCenterSetStatus(replace ? "正在替换插件版本…" : "正在导入插件…");
  try {
    const bytes = await file.arrayBuffer();
    const value = await pluginCenterRequest(
      "/api/plugins/import?replace=" + (replace ? "1" : "0"),
      {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: bytes,
      },
    );
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    const importedId = value && value.plugin && value.plugin.id !== undefined
      ? value.plugin.id
      : (Array.isArray(value.plugins) && value.plugins.length
        ? value.plugins[value.plugins.length - 1].id : null);
    pluginCenterView.importFile = null;
    pluginCenterClearImportConflict();
    pluginCenterNode("plugins-file").value = "";
    if (importedId !== null && importedId !== undefined) {
      pluginCenterView.selectedId = importedId;
      pluginCenterView.settingsPluginId = null;
      pluginCenterView.settingsDirty = false;
    }
    if (value && Array.isArray(value.plugins)) {
      pluginCenterApplySnapshot(value);
    } else {
      await pluginCenterRefresh();
      if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
      pluginCenterRenderList();
      pluginCenterRenderDetail();
    }
    pluginCenterSetStatus(replace
      ? "插件版本已替换。新版本默认不启用。"
      : "插件已导入。新导入的插件默认不启用。", "ok");
  } catch (error) {
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterNode("plugins-file").value = "";
    if (error.status === 409 && error.payload) {
      pluginCenterShowImportConflict(file, error.payload);
      pluginCenterSetStatus("检测到同名插件，请确认是否替换版本。", "warn");
    } else if (replace && error.status === 400) {
      pluginCenterSetStatus("替换版本失败：" + error.message +
        " 请先在插件详情里停用插件，等状态变为已停用后再重试替换。", "error");
    } else {
      pluginCenterSetStatus("导入失败：" + error.message, "error");
    }
  } finally {
    if (seq === pluginCenterView.seq) pluginCenterView.importBusy = false;
    if (pluginCenterView.open && seq === pluginCenterView.seq) {
      pluginCenterRenderBusy();
      const selected = pluginCenterSelected();
      if (selected) pluginCenterRenderActions(selected);
    }
  }
}

function pluginCenterHandleFile(event) {
  const input = event && event.target ? event.target : pluginCenterNode("plugins-file");
  const file = input && input.files && input.files[0];
  if (!file) return;
  pluginCenterClearImportConflict();
  pluginCenterView.importFile = file;
  pluginCenterImport(file, false);
}

function pluginCenterRunCommand(plugin, operation) {
  if (!plugin || pluginCenterView.action || pluginCenterView.importBusy) return;
  if (operation === "uninstall" &&
      !confirm("卸载插件“" + (plugin.name || plugin.id) +
        "”？插件文件会被移除，历史与资料默认保留。")) {
    return;
  }
  pluginCenterView.action = { id: plugin.id, operation };
  const seq = pluginCenterView.seq;
  const labels = {
    load: "加载", unload: "停用", restart: "重载", stop: "停止在途操作",
    uninstall: "卸载",
  };
  pluginCenterSetStatus(labels[operation] + "插件“" + (plugin.name || plugin.id) + "”…");
  pluginCenterRenderActions(plugin);
  pluginCenterRenderBusy();
  pluginCenterJsonRequest(
    "/api/plugins/" + encodeURIComponent(plugin.id) + "/commands",
    "POST",
    { operation },
  ).then((value) => {
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterApplySnapshot(value);
    if (!pluginCenterNeedsPolling()) {
      pluginCenterSetStatus(labels[operation] + "请求已完成。", "ok");
    }
  }).catch((error) => {
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterSetStatus(labels[operation] + "失败：" + error.message, "error");
  }).finally(() => {
    if (seq === pluginCenterView.seq) pluginCenterView.action = null;
    if (!pluginCenterView.open || seq !== pluginCenterView.seq) return;
    pluginCenterRenderList();
    pluginCenterRenderDetail();
    pluginCenterRenderBusy();
  });
}

function pluginCenterStopDrainPoll() {
  if (pluginCenterView.drainingTimer) clearTimeout(pluginCenterView.drainingTimer);
  pluginCenterView.drainingTimer = null;
}

function pluginCenterNeedsPolling() {
  return pluginCenterView.plugins.some(
    (plugin) => {
      const status = pluginCenterText(plugin.status).toLowerCase();
      return status === "draining" || status === "loading" || status === "closing";
    },
  );
}

function pluginCenterScheduleDrainPoll() {
  if (!pluginCenterView.open || !pluginCenterNeedsPolling()) {
    pluginCenterStopDrainPoll();
    return;
  }
  if (pluginCenterView.drainingTimer) return;
  pluginCenterSetStatus("插件正在准备或收尾，等待状态更新…");
  pluginCenterArmDrainPoll();
}

function pluginCenterArmDrainPoll() {
  if (!pluginCenterView.open || pluginCenterView.drainingTimer) return;
  pluginCenterView.drainingTimer = setTimeout(async () => {
    pluginCenterView.drainingTimer = null;
    if (!pluginCenterView.open || !pluginCenterNeedsPolling()) return;
    try {
      const value = await pluginCenterRequest("/api/plugins", { method: "GET" });
      if (!pluginCenterView.open) return;
      pluginCenterApplySnapshot(value);
      if (pluginCenterNeedsPolling()) {
        pluginCenterSetStatus("插件正在准备或收尾，等待状态更新…");
      } else {
        pluginCenterSetStatus("插件收尾完成。", "ok");
      }
    } catch (error) {
      if (!pluginCenterView.open) return;
      pluginCenterSetStatus("收尾状态读取失败：" + error.message, "error");
    }
  }, 1000);
}

function pluginCenterSelectPlugin(id) {
  if (String(pluginCenterView.selectedId) === String(id)) return;
  pluginCenterView.selectedId = id;
  pluginCenterView.settingsPluginId = null;
  pluginCenterView.settingsDirty = false;
  pluginCenterView.framePluginId = null;
  pluginCenterView.framePage = null;
  pluginCenterRenderList();
  pluginCenterRenderDetail();
}

function pluginCenterOpenPage(plugin, page) {
  const frame = pluginCenterNode("plugin-frame");
  pluginCenterView.framePluginId = plugin.id;
  pluginCenterView.framePage = page;
  pluginCenterNode("plugin-frame-title").textContent =
    (pluginCenterText(plugin.name) || plugin.id) + " · " +
    (pluginCenterText(page.title) || "功能页");
  pluginCenterNode("plugin-frame-path").textContent = pluginCenterText(page.path);
  frame.title = pluginCenterNode("plugin-frame-title").textContent;
  frame.src = pluginCenterPageUrl(plugin.id, page.path);
  pluginCenterNode("plugin-detail").hidden = true;
  pluginCenterNode("plugins-empty").hidden = true;
  pluginCenterNode("plugin-frame-panel").hidden = false;
  pluginCenterSetStatus("已打开插件页面；关闭页面不会停用插件后台。");
}

function pluginCenterClosePage() {
  const frame = pluginCenterNode("plugin-frame");
  pluginCenterView.framePluginId = null;
  pluginCenterView.framePage = null;
  if (frame.removeAttribute) frame.removeAttribute("src");
  else frame.src = "";
  pluginCenterNode("plugin-frame-panel").hidden = true;
  pluginCenterRenderDetail();
  pluginCenterSetStatus("已关闭插件页面；插件后台仍在运行。");
}

// Minimal navigation bridge for a plugin page that relates to a current task.
// Only the visible frame may request these two fixed host actions.
function pluginCenterOpenRelated(action, taskId) {
  pluginCenterClose();
  try {
    const pending = action === "task" ? openTask(taskId) : openLing();
    if (pending && typeof pending.catch === "function") {
      pending.catch((error) => {
        if (typeof setStatus === "function") setStatus("打开失败：" + error.message, true);
      });
    }
  } catch (error) {
    if (typeof setStatus === "function") setStatus("打开失败：" + error.message, true);
  }
}

function pluginCenterHandleFrameMessage(event) {
  if (!pluginCenterView.open || !pluginCenterView.framePluginId) return;
  const frame = pluginCenterNode("plugin-frame");
  if (!frame || event.source !== frame.contentWindow) return;
  if (event.origin !== location.origin) return;
  const message = event.data;
  if (!message || typeof message !== "object" || Array.isArray(message)) return;
  if (message.type === "nexus.open_task") {
    if (typeof message.task_id !== "string" || !message.task_id) return;
    pluginCenterOpenRelated("task", message.task_id);
    return;
  }
  if (message.type === "nexus.open_ling") {
    pluginCenterOpenRelated("ling");
  }
}

function pluginCenterRenderKindOptions() {
  const list = pluginCenterNode("plugins-requests-kind-options");
  if (!list) return;
  pluginCenterClear(list);
  const seen = new Set();
  for (const request of pluginCenterView.requests) {
    const kind = pluginCenterText(request.kind);
    if (!kind || seen.has(kind)) continue;
    seen.add(kind);
    const option = document.createElement("option");
    option.value = kind;
    list.appendChild(option);
  }
}

function pluginCenterRenderRequestList() {
  const list = pluginCenterNode("plugins-requests-list");
  pluginCenterClear(list);
  pluginCenterNode("plugins-requests-count").textContent = pluginCenterView.requests.length
    ? pluginCenterView.requests.length + " 条" : "";
  if (!pluginCenterView.requests.length) {
    list.appendChild(pluginCenterEl("p", "plugins-list-empty",
      pluginCenterView.requestsLoading ? "正在读取请求…" : "没有符合条件的请求。"));
  } else {
    for (const request of pluginCenterView.requests) {
      const row = pluginCenterEl("button", "plugin-request-row");
      row.type = "button";
      row.dataset.requestId = pluginCenterText(request.id);
      if (String(pluginCenterView.selectedRequestId) === String(request.id)) {
        row.classList.add("is-selected");
      }
      row.appendChild(pluginCenterEl("span", "plugin-request-time",
        pluginCenterFormatTime(request.created_at)));
      const meta = pluginCenterEl("span", "plugin-request-meta");
      meta.appendChild(pluginCenterEl("span", "", pluginCenterText(request.kind) || "未知类型"));
      meta.appendChild(pluginCenterEl("span", "", pluginCenterText(request.owner_id) || "无活动标识"));
      row.appendChild(meta);
      row.appendChild(pluginCenterEl("span", "plugin-request-model",
        pluginCenterText(request.model) || "模型未记录"));
      const status = pluginCenterText(request.status).toLowerCase();
      row.appendChild(pluginCenterEl("span", "plugin-request-status" +
        (status === "error" || status === "failed" ? " is-error" : ""),
      pluginCenterText(request.status) || "状态未记录"));
      row.addEventListener("click", () => {
        pluginCenterView.selectedRequestId = request.id;
        pluginCenterRenderRequestList();
        pluginCenterLoadRequestDetail(request.id);
      });
      list.appendChild(row);
    }
  }
  pluginCenterRenderKindOptions();
  const more = pluginCenterNode("plugins-requests-more");
  more.hidden = pluginCenterView.requestNext === null ||
    pluginCenterView.requestNext === undefined;
  more.disabled = pluginCenterView.requestsLoading;
}

async function pluginCenterLoadRequests(append) {
  if (pluginCenterView.requestsLoading) return;
  if (!append) {
    pluginCenterView.requests = [];
    pluginCenterView.requestNext = null;
    pluginCenterView.selectedRequestId = null;
    pluginCenterView.requestDetailSeq += 1;
    pluginCenterSetError("plugins-requests-error", "");
    pluginCenterRequestDetailEmpty("选择一条请求查看来源、参与原因与最终 JSON。");
  }
  const seq = ++pluginCenterView.requestSeq;
  pluginCenterView.requestsLoading = true;
  pluginCenterRenderRequestList();
  const params = new URLSearchParams();
  params.set("limit", "50");
  if (pluginCenterView.requestOwner) params.set("owner_id", pluginCenterView.requestOwner);
  if (pluginCenterView.requestKind) params.set("kind", pluginCenterView.requestKind);
  if (append && pluginCenterView.requestNext !== null &&
      pluginCenterView.requestNext !== undefined) {
    params.set("before", pluginCenterText(pluginCenterView.requestNext));
  }
  try {
    const value = await pluginCenterRequest("/api/model-requests?" + params.toString(),
      { method: "GET" });
    if (!pluginCenterView.open || seq !== pluginCenterView.requestSeq) return;
    const incoming = Array.isArray(value.requests) ? value.requests : [];
    const seen = new Set(pluginCenterView.requests.map((item) => String(item.id)));
    for (const request of incoming) {
      if (request && request.id !== undefined && !seen.has(String(request.id))) {
        pluginCenterView.requests.push(request);
        seen.add(String(request.id));
      }
    }
    pluginCenterView.requestsLoaded = true;
    pluginCenterView.requestNext = value.next === undefined ? null : value.next;
    pluginCenterSetError("plugins-requests-error", "");
  } catch (error) {
    if (!pluginCenterView.open || seq !== pluginCenterView.requestSeq) return;
    pluginCenterView.requestsError = error.message;
    pluginCenterSetError("plugins-requests-error", "读取请求记录失败：" + error.message);
  } finally {
    if (seq === pluginCenterView.requestSeq) {
      pluginCenterView.requestsLoading = false;
      if (pluginCenterView.open) pluginCenterRenderRequestList();
    }
  }
}

function pluginCenterRequestDetailEmpty(message) {
  const detail = pluginCenterNode("plugins-request-detail");
  pluginCenterClear(detail);
  detail.appendChild(pluginCenterEl("p", "plugins-empty", message));
}

function pluginCenterParticipationText(entry) {
  if (entry === null || entry === undefined) return "（空记录）";
  if (typeof entry !== "object") return pluginCenterText(entry);
  const name = pluginCenterText(entry.name) || pluginCenterText(entry.title) ||
    pluginCenterText(entry.id) || pluginCenterText(entry.plugin_id) || "未命名插件";
  const parts = [];
  const statusLabels = {
    used: "已参与", not_applicable: "无需参与", failed: "失败", skipped: "未参与",
  };
  if (entry.status !== undefined) parts.push(pluginCenterText(entry.status));
  if (entry.used === false || entry.participated === false || entry.included === false) {
    parts.push("未参与");
  } else if (entry.used === true || entry.participated === true || entry.included === true) {
    parts.push("已参与");
  }
  const reason = entry.reason || entry.participation_reason || entry.why || entry.message;
  if (reason) parts.push("原因：" + pluginCenterText(reason));
  if (entry.error) parts.push("错误：" + pluginCenterText(entry.error));
  if (entry.required === true) parts.push("本次必需");
  const status = pluginCenterText(entry.status).toLowerCase();
  if (status && statusLabels[status]) parts[0] = statusLabels[status];
  return name + (parts.length ? " · " + parts.join(" · ") : "");
}

function pluginCenterRenderRequestDetail(value) {
  const detail = pluginCenterNode("plugins-request-detail");
  pluginCenterClear(detail);
  const head = pluginCenterEl("div", "plugin-request-detail-head");
  head.appendChild(pluginCenterEl("h3", "", "请求 #" + pluginCenterText(value.id)));
  const status = pluginCenterText(value.status).toLowerCase();
  head.appendChild(pluginCenterEl("span", "plugin-request-status" +
    (status === "error" || status === "failed" ? " is-error" : ""),
  pluginCenterText(value.status) || "状态未记录"));
  detail.appendChild(head);

  const meta = pluginCenterEl("div", "plugin-request-detail-meta");
  const line = (label, text) => {
    const row = pluginCenterEl("div", "plugin-request-detail-line");
    row.appendChild(pluginCenterEl("span", "plugin-request-label", label));
    row.appendChild(pluginCenterEl("span", "", text || "未记录"));
    meta.appendChild(row);
  };
  line("时间", pluginCenterFormatTime(value.created_at));
  line("活动", pluginCenterText(value.owner_id));
  line("类型", pluginCenterText(value.kind));
  line("模型", pluginCenterText(value.model));
  if (value.protocol) line("协议", pluginCenterText(value.protocol));
  detail.appendChild(meta);
  if (value.error) {
    const error = pluginCenterEl("div", "plugin-error", "请求错误：" + value.error);
    detail.appendChild(error);
  }

  const participation = pluginCenterEl("section", "plugin-request-section");
  participation.appendChild(pluginCenterEl("h4", "", "来源与参与原因"));
  const plugins = Array.isArray(value.plugins) ? value.plugins
    : (value.plugins ? [value.plugins] : []);
  if (!plugins.length) {
    participation.appendChild(pluginCenterEl("p", "plugin-request-note",
      "此请求没有记录插件参与。"));
  } else {
    for (const entry of plugins) {
      participation.appendChild(pluginCenterEl("div", "plugin-participation-row",
        pluginCenterParticipationText(entry)));
    }
  }
  detail.appendChild(participation);

  if (value.materials !== undefined && value.materials !== null) {
    const materials = pluginCenterEl("section", "plugin-request-section");
    materials.appendChild(pluginCenterEl("h4", "", "材料"));
    materials.appendChild(pluginCenterEl("pre", "plugin-json",
      pluginCenterJsonText(value.materials)));
    detail.appendChild(materials);
  }

  const body = pluginCenterEl("section", "plugin-request-section");
  body.appendChild(pluginCenterEl("h4", "", "最终 JSON"));
  body.appendChild(pluginCenterEl("pre", "plugin-json", pluginCenterJsonText(value.body)));
  detail.appendChild(body);
}

async function pluginCenterLoadRequestDetail(id) {
  const seq = ++pluginCenterView.requestDetailSeq;
  pluginCenterRequestDetailEmpty("正在读取请求详情…");
  try {
    const value = await pluginCenterRequest(
      "/api/model-requests/" + encodeURIComponent(id),
      { method: "GET" },
    );
    if (!pluginCenterView.open || seq !== pluginCenterView.requestDetailSeq ||
        String(pluginCenterView.selectedRequestId) !== String(id)) return;
    pluginCenterRenderRequestDetail(value);
  } catch (error) {
    if (!pluginCenterView.open || seq !== pluginCenterView.requestDetailSeq) return;
    pluginCenterRequestDetailEmpty("读取请求详情失败：" + error.message);
  }
}

function pluginCenterHandleRequestsSubmit(event) {
  if (event && event.preventDefault) event.preventDefault();
  pluginCenterView.requestOwner = pluginCenterText(pluginCenterNode("plugins-requests-owner").value).trim();
  pluginCenterView.requestKind = pluginCenterText(pluginCenterNode("plugins-requests-kind").value).trim();
  pluginCenterLoadRequests(false);
}

function pluginCenterSetTab(tab) {
  const target = tab === "requests" ? "requests" : "plugins";
  pluginCenterView.tab = target;
  const pluginsPane = pluginCenterNode("plugins-pane-plugins");
  const requestsPane = pluginCenterNode("plugins-pane-requests");
  const pluginsTab = pluginCenterNode("plugins-tab-plugins");
  const requestsTab = pluginCenterNode("plugins-tab-requests");
  pluginsPane.hidden = target !== "plugins";
  requestsPane.hidden = target !== "requests";
  pluginsTab.classList.toggle("is-active", target === "plugins");
  requestsTab.classList.toggle("is-active", target === "requests");
  pluginsTab.setAttribute("aria-selected", String(target === "plugins"));
  requestsTab.setAttribute("aria-selected", String(target === "requests"));
  if (target === "requests" && !pluginCenterView.requestsLoaded &&
      !pluginCenterView.requestsLoading) {
    pluginCenterLoadRequests(false);
  }
}

function pluginCenterOpen() {
  if (pluginCenterView.open) return;
  pluginCenterView.open = true;
  pluginCenterView.seq += 1;
  pluginCenterSetTab("plugins");
  pluginCenterSetStatus("");
  pluginCenterClearImportConflict();
  pluginCenterSetError("plugins-requests-error", "");
  const dialog = pluginCenterNode("plugins-dialog");
  if (!dialog.open && dialog.showModal) dialog.showModal();
  pluginCenterRenderList();
  pluginCenterRenderDetail();
  pluginCenterRenderBusy();
  pluginCenterRefresh();
}

function pluginCenterClose() {
  if (!pluginCenterView.open) return;
  pluginCenterView.open = false;
  pluginCenterView.seq += 1;
  pluginCenterStopDrainPoll();
  pluginCenterClearFrame();
  pluginCenterView.loading = false;
  pluginCenterView.requestsLoading = false;
  pluginCenterView.action = null;
  pluginCenterView.importBusy = false;
  pluginCenterView.settingsSaving = false;
  const dialog = pluginCenterNode("plugins-dialog");
  if (dialog.open && dialog.close) dialog.close();
}

function pluginCenterClearFrame() {
  const frame = pluginCenterNode("plugin-frame");
  pluginCenterView.framePluginId = null;
  pluginCenterView.framePage = null;
  if (frame.removeAttribute) frame.removeAttribute("src");
  else frame.src = "";
  pluginCenterNode("plugin-frame-panel").hidden = true;
}

pluginCenterNode("plugins-open").addEventListener("click", pluginCenterOpen);
pluginCenterNode("plugins-close").addEventListener("click", pluginCenterClose);
pluginCenterNode("plugins-dialog").addEventListener("close", () => {
  if (!pluginCenterView.open) return;
  pluginCenterView.open = false;
  pluginCenterView.seq += 1;
  pluginCenterStopDrainPoll();
  pluginCenterClearFrame();
  pluginCenterView.loading = false;
  pluginCenterView.requestsLoading = false;
  pluginCenterView.action = null;
  pluginCenterView.importBusy = false;
  pluginCenterView.settingsSaving = false;
});
pluginCenterNode("plugins-refresh").addEventListener("click", () => {
  if (!pluginCenterView.action && !pluginCenterView.importBusy) pluginCenterRefresh();
});
pluginCenterNode("plugins-scan").addEventListener("click", pluginCenterScan);
pluginCenterNode("plugins-upload").addEventListener("click", () => {
  if (!pluginCenterView.action && !pluginCenterView.importBusy) {
    pluginCenterNode("plugins-file").click();
  }
});
pluginCenterNode("plugins-file").addEventListener("change", pluginCenterHandleFile);
pluginCenterNode("plugins-tab-plugins").addEventListener("click", () => pluginCenterSetTab("plugins"));
pluginCenterNode("plugins-tab-requests").addEventListener("click", () => pluginCenterSetTab("requests"));
pluginCenterNode("plugin-settings-form").addEventListener("submit", pluginCenterSaveSettings);
pluginCenterNode("plugin-frame-close").addEventListener("click", pluginCenterClosePage);
pluginCenterNode("plugins-requests-filters").addEventListener("submit", pluginCenterHandleRequestsSubmit);
pluginCenterNode("plugins-requests-more").addEventListener("click", () => {
  pluginCenterLoadRequests(true);
});
window.addEventListener("message", pluginCenterHandleFrameMessage);
