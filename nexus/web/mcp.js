"use strict";

// MCP servers are configured from the settings dialog. The list is read when
// settings opens and after mutations; no background polling is needed here.
(function () {
  const state = { servers: [], editing: null, loading: false, busy: false };

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }

  function errorText(error) {
    if (!error) return "";
    if (typeof error === "string") return error;
    try {
      return JSON.stringify(error);
    } catch (_) {
      return String(error);
    }
  }

  function normalizeTransport(transport) {
    const value = String(transport || "").trim().toLowerCase();
    return value === "http" || value === "streamable-http" || value === "streamable_http"
      ? "http"
      : "stdio";
  }

  function transportLabel(transport) {
    return normalizeTransport(transport) === "http" ? "HTTP" : "stdio";
  }

  function serverPayload(server) {
    return {
      name: server.name,
      transport: normalizeTransport(server.transport),
      command: server.command || "",
      args: Array.isArray(server.args) ? server.args : [],
      env: server.env && typeof server.env === "object" && !Array.isArray(server.env) ? server.env : {},
      enabled: server.enabled !== false,
      url: server.url || "",
    };
  }

  function applySnapshot(payload) {
    if (!payload || !Array.isArray(payload.servers)) return false;
    state.servers = payload.servers.filter((server) => server && typeof server === "object");
    renderList();
    return true;
  }

  function setListError(message) {
    const target = $("mcp-list-error");
    if (target) target.textContent = message || "";
  }

  function renderList() {
    const list = $("mcp-list");
    if (!list) return;
    list.innerHTML = "";

    const add = $("add-mcp");
    if (add) add.disabled = state.loading || state.busy;

    if (state.loading && !state.servers.length) {
      list.appendChild(node("p", "small", "正在读取 MCP 服务…"));
      return;
    }
    if (!state.servers.length) {
      list.appendChild(node("div", "mcp-empty", "尚未添加 MCP 服务。"));
      return;
    }

    for (const server of state.servers) {
      const row = node("div", "mcp-row");
      const main = node("div", "mcp-row-main");
      const title = node("div", "mcp-row-title");
      const enabled = server.enabled !== false;
      const dot = node("span", "mcp-dot " + (enabled && server.connected ? "on" : "off"));
      dot.title = enabled && server.connected ? "已连接" : "未连接";
      title.appendChild(dot);
      title.appendChild(node("span", "mcp-name", server.name || "未命名"));
      if (server.managed === true) title.appendChild(node("span", "mcp-badge", "系统服务"));
      main.appendChild(title);

      const connection = !enabled ? "已停用" : server.connected ? "已连接" : "未连接";
      const toolCount = server.tool_count === null || server.tool_count === undefined
        ? "—"
        : String(server.tool_count);
      const meta = node("div", "mcp-meta");
      meta.appendChild(node("span", "", transportLabel(server.transport)));
      meta.appendChild(node("span", "", connection));
      meta.appendChild(node("span", "", "工具 " + toolCount));
      main.appendChild(meta);

      const recent = errorText(server.error);
      main.appendChild(node("div", "mcp-recent" + (recent ? " has-error" : ""), "最近错误：" + (recent || "无")));
      row.appendChild(main);

      const actions = node("div", "mcp-row-actions");
      const action = (label, handler, danger) => {
        const button = node("button", "mcp-action" + (danger ? " danger" : ""), label);
        button.type = "button";
        button.disabled = state.loading || state.busy;
        button.addEventListener("click", handler);
        actions.appendChild(button);
      };

      if (server.managed !== true) {
        action(enabled ? "停用" : "启用", () => toggleServer(server));
        action("编辑", () => openEditor(server));
      }
      action("重连", () => reconnectServer(server));
      if (server.managed !== true) action("删除", () => deleteServer(server), true);
      row.appendChild(actions);
      list.appendChild(row);
    }
  }

  async function fetchServers() {
    const payload = await api("/api/mcp");
    if (!applySnapshot(payload)) throw new Error("MCP 服务列表格式不正确");
    return payload;
  }

  async function loadServers() {
    if (state.loading || state.busy) return;
    state.loading = true;
    setListError("");
    renderList();
    try {
      await fetchServers();
    } catch (error) {
      setListError(error.message || String(error));
      if (!state.servers.length) {
        const list = $("mcp-list");
        if (list) {
          list.innerHTML = "";
          list.appendChild(node("div", "mcp-empty", "读取 MCP 服务失败。"));
        }
      }
    } finally {
      state.loading = false;
      renderList();
    }
  }

  async function runMutation(action, errorTarget) {
    if (state.busy) return false;
    state.busy = true;
    if (errorTarget) errorTarget.textContent = "";
    renderList();
    try {
      const payload = await action();
      if (!applySnapshot(payload)) await fetchServers();
      setListError("");
      return true;
    } catch (error) {
      const message = error.message || String(error);
      if (errorTarget) errorTarget.textContent = message;
      else setListError(message);
      return false;
    } finally {
      state.busy = false;
      renderList();
    }
  }

  async function toggleServer(server) {
    if (server.managed === true) return;
    const payload = serverPayload(server);
    payload.enabled = server.enabled === false;
    await runMutation(() => post("/api/mcp/servers", payload), $("mcp-list-error"));
  }

  async function reconnectServer(server) {
    const path = "/api/mcp/servers/" + encodeURIComponent(server.name) + "/reconnect";
    await runMutation(() => post(path, {}), $("mcp-list-error"));
  }

  async function deleteServer(server) {
    if (server.managed === true) return;
    if (!confirm("删除 MCP 服务“" + server.name + "”？")) return;
    const path = "/api/mcp/servers/" + encodeURIComponent(server.name) + "/delete";
    const saved = await runMutation(() => post(path, {}), $("mcp-list-error"));
    if (saved && state.editing && state.editing.name === server.name) {
      $("mcp-dialog").close();
    }
  }

  function syncTransport() {
    const transport = normalizeTransport($("mcp-transport").value);
    $("mcp-stdio-fields").hidden = transport !== "stdio";
    $("mcp-http-fields").hidden = transport !== "http";
  }

  function syncTokenClear() {
    const clear = $("mcp-clear-token");
    const token = $("mcp-token");
    if (clear.checked) token.value = "";
    token.disabled = clear.checked;
  }

  function openEditor(server) {
    if (server && server.managed === true) return;
    state.editing = server || null;
    const editing = Boolean(server);
    $("mcp-dialog-title").textContent = editing ? "编辑 MCP 服务" : "添加 MCP 服务";
    $("mcp-name").value = server ? server.name || "" : "";
    $("mcp-transport").value = server ? normalizeTransport(server.transport) : "stdio";
    $("mcp-command").value = server ? server.command || "" : "";
    $("mcp-args").value = server && Array.isArray(server.args) ? server.args.join("\n") : "";
    $("mcp-env").value = server && server.env && typeof server.env === "object" && Object.keys(server.env).length
      ? JSON.stringify(server.env, null, 2)
      : "";
    $("mcp-url").value = server ? server.url || "" : "";
    $("mcp-token").value = "";
    $("mcp-token").disabled = false;
    $("mcp-clear-token").checked = false;
    $("mcp-clear-token-wrap").hidden = !(server && server.has_token);
    $("mcp-token").placeholder = server && server.has_token ? "已保存，留空则保留" : "可选";
    $("mcp-enabled").checked = !server || server.enabled !== false;
    $("mcp-delete").hidden = !editing;
    $("mcp-error").textContent = "";
    syncTransport();
    syncTokenClear();
    $("mcp-dialog").showModal();
  }

  function parseArgs(raw) {
    const text = raw.trim();
    if (!text) return [];
    if (text.startsWith("[")) {
      let value;
      try {
        value = JSON.parse(text);
      } catch (_) {
        throw new Error("参数 JSON 格式不正确");
      }
      if (!Array.isArray(value)) throw new Error("参数 JSON 必须是字符串数组");
      return value.map((item) => {
        if (item !== null && typeof item === "object") throw new Error("参数 JSON 必须是字符串数组");
        return String(item);
      });
    }
    return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  }

  function parseEnv(raw) {
    const text = raw.trim();
    if (!text) return {};
    let value;
    try {
      value = JSON.parse(text);
    } catch (_) {
      throw new Error("环境变量 JSON 格式不正确");
    }
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("环境变量必须是 JSON 对象");
    }
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      if (item !== null && typeof item === "object") throw new Error("环境变量的值必须是字符串");
      result[key] = String(item);
    }
    return result;
  }

  async function saveEditor() {
    const error = $("mcp-error");
    error.textContent = "";
    const name = $("mcp-name").value.trim();
    const transport = normalizeTransport($("mcp-transport").value);
    if (!name) {
      error.textContent = "请填写服务名称";
      return;
    }

    let payload;
    try {
      if (transport === "stdio") {
        const command = $("mcp-command").value.trim();
        if (!command) throw new Error("请填写启动命令");
        payload = {
          name,
          transport,
          command,
          args: parseArgs($("mcp-args").value),
          env: parseEnv($("mcp-env").value),
          enabled: $("mcp-enabled").checked,
          url: "",
        };
      } else {
        const url = $("mcp-url").value.trim();
        if (!url) throw new Error("请填写 HTTP URL");
        let parsed;
        try {
          parsed = new URL(url);
        } catch (_) {
          throw new Error("HTTP URL 格式不正确");
        }
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
          throw new Error("HTTP URL 必须使用 http:// 或 https://");
        }
        payload = {
          name,
          transport,
          command: "",
          args: [],
          env: {},
          enabled: $("mcp-enabled").checked,
          url,
        };
        if ($("mcp-clear-token").checked) {
          payload.token = "";
        } else {
          const token = $("mcp-token").value.trim();
          if (token) payload.token = token;
        }
      }
    } catch (problem) {
      error.textContent = problem.message || String(problem);
      return;
    }

    if (state.editing && state.editing.name !== name) payload.previous_name = state.editing.name;
    const saved = await runMutation(() => post("/api/mcp/servers", payload), error);
    if (saved) {
      state.editing = null;
      $("mcp-dialog").close();
    }
  }

  async function deleteEditing() {
    if (!state.editing) return;
    await deleteServer(state.editing);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("settings").addEventListener("click", () => {
      loadServers().catch((error) => setListError(error.message || String(error)));
    });
    $("add-mcp").addEventListener("click", () => openEditor(null));
    $("mcp-transport").addEventListener("change", syncTransport);
    $("mcp-clear-token").addEventListener("change", syncTokenClear);
    $("mcp-save").addEventListener("click", saveEditor);
    $("mcp-delete").addEventListener("click", deleteEditing);
    $("mcp-dialog").addEventListener("close", () => {
      state.editing = null;
    });
  }, { once: true });
})();
