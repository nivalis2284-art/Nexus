"""MCP boundary to the independently runnable scheduling service."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from ..mcp import MCPError, ServerConfig


class SchedulerService:
    def __init__(self, registry, home: Path, entry=None):
        self.registry, self.home = registry, home
        self.config = ServerConfig.from_dict(entry or {
            "name": "scheduler", "command": sys.executable,
            "args": ["-m", "nexus_scheduler", "--data", str(home / "scheduler")],
        })
        if self.config is None:
            raise ValueError("无效的调度服务配置")
        self.name = self.config.name
        self.error = ""

    def call(self, tool, **arguments):
        server = self.registry.servers.get(self.name)
        if server is None:
            raise MCPError("调度监听服务未连接")
        result = server.call_tool(tool, arguments)
        if not result.ok:
            raise MCPError(result.text)
        value = getattr(result, "structured_content", None)
        if value is None:
            value = json.loads(result.text)
        if not isinstance(value, dict):
            raise MCPError("调度服务返回的内容不是对象")
        return value

    def start(self, callback_url):
        self.callback_url = callback_url
        try:
            if self.name not in self.registry.servers:
                self.registry.add(self.config, cwd=self.home)
            legacy = self._legacy_rules()
            if legacy:
                self.call("scheduler_import", values=legacy)
            self.call("scheduler_connect", callback_url=callback_url)
            self.error = ""
        except (MCPError, ValueError, OSError) as exc:
            self.error = str(exc)

    def reconnect(self):
        self.registry.remove(self.name)
        self.start(self.callback_url)

    def snapshot(self):
        try:
            state = self.call("scheduler_state")
            self.error = ""
            return state
        except (MCPError, ValueError, OSError) as exc:
            self.error = str(exc)
            return {"settings": {}, "schedules": [], "watches": [], "error": self.error}

    def _legacy_rules(self):
        path = self.home / "automation.sqlite3"
        if not path.exists():
            return None
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"settings", "schedules", "watches"} <= tables:
                return None
            settings = {row["key"]: json.loads(row["value"]) for row in db.execute("SELECT * FROM settings")}
            schedules = []
            for row in db.execute("SELECT * FROM schedules"):
                item = dict(row)
                item["weekdays"] = json.loads(item["weekdays"])
                item["enabled"], item["notify"] = bool(item["enabled"]), bool(item["notify"])
                schedules.append(item)
            watches = []
            for row in db.execute("SELECT * FROM watches"):
                item = dict(row)
                item["enabled"] = bool(item["enabled"])
                watches.append(item)
            return {"settings": settings, "schedules": schedules, "watches": watches}
