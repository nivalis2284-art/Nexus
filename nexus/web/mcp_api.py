"""Service configuration and live connection controls for the settings page."""
from __future__ import annotations

from ..mcp import MCPError, ServerConfig


def snapshot(state):
    registry = state["mcp"]
    statuses = {entry["name"]: entry for entry in registry.status()}
    entries = state["store"].load().mcp_servers
    plugins = state.get("plugins")
    if plugins is not None:
        for owner in plugins.entries.values():
            for name, full_name in owner.services.items():
                entries = [*entries, {
                    "name": full_name, "transport": "stdio", "enabled": True,
                    "managed": True, "plugin_id": owner.id, "service": name,
                }]
    result = []
    for raw in entries:
        item = dict(raw)
        item["has_token"] = bool(item.pop("token", ""))
        item.setdefault("transport", "http" if item.get("url") else "stdio")
        status = statuses.get(item["name"], {"connected": False, "tool_count": 0, "error": ""})
        # The SDK names its wire transport "streamable-http"; the settings
        # page edits the configured "http" transport, so keep that value.
        item.update({key: value for key, value in status.items()
                     if key not in ("name", "transport")})
        result.append(item)
    return {"servers": result}


def save(state, values):
    config = state["store"]
    raw = config.read_config()
    entries = list((raw.get("mcp") or {}).get("servers") or [])
    name = str(values.get("name", "")).strip()
    previous = str(values.get("previous_name") or name)
    if not name:
        raise ValueError("服务名称不能为空")
    plugins = state.get('plugins')
    if plugins and any(candidate in item.services.values()
                       for item in plugins.entries.values() for candidate in (name, previous)):
        raise ValueError("此服务属于插件，请在插件管理中操作")
    old = next((e for e in entries if e.get("name") == previous), {})
    if name != previous and not old:
        raise ValueError("原服务不存在；请刷新列表后重试")
    if name != previous and any(e.get("name") == name for e in entries):
        raise ValueError("服务名称已存在")
    item = {**old, **{k: v for k, v in values.items() if k in (
        "name", "transport", "command", "args", "env", "enabled", "url")}}
    item["name"] = name
    entry = ServerConfig.from_dict(item)
    if entry is None:
        raise ValueError("请填写本地服务命令或远程服务地址")
    key = "mcp:" + name
    old_key = "mcp:" + previous
    secrets = config.read_credentials()
    token = str(values["token"]) if "token" in values else secrets.get(old_key, "")
    config.write_credential(key, token)
    if previous != name:
        config.write_credential(old_key, "")
    item.pop("token", None)
    raw["mcp"] = {**(raw.get("mcp") or {}), "servers": [
        e for e in entries if e.get("name") not in (previous, name)] + [item]}
    config.write_config(raw)
    registry = state["mcp"]
    registry.remove(previous)
    if item.get("enabled", True):
        entry = ServerConfig.from_dict({**item, "token": token})
        try:
            if entry is not None:
                registry.add(entry, cwd=config.load().workspace)
        except (MCPError, OSError):
            # Keep the saved configuration and SDK connection diagnostics editable.
            pass
    return snapshot(state)


def command(state, name, operation):
    registry, config = state["mcp"], state["store"]
    raw = config.read_config()
    entries = (raw.get("mcp") or {}).get("servers") or []
    item = next((e for e in config.load().mcp_servers if e.get("name") == name), None)
    if item is None:
        raise ValueError("服务不存在")
    if operation == "delete":
        registry.remove(name)
        raw["mcp"]["servers"] = [e for e in entries if e.get("name") != name]
        config.write_config(raw)
        config.write_credential("mcp:" + name, "")
    elif operation == "reconnect":
        if not item.get("enabled", True):
            raise ValueError("服务已停用；请先启用后再重连")
        entry = ServerConfig.from_dict(item)
        if entry is None:
            raise ValueError("服务配置无效")
        registry.reconnect(entry, cwd=config.load().workspace)
    else:
        raise ValueError("未知服务操作")
    return snapshot(state)
