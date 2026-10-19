"""Per-workspace MCP connection ownership.

The web server and persistent-task runtime use the same startup rules, but
each execution owner gets its own live registry. Closing one task's workspace
connections cannot interrupt an ordinary conversation.
"""

from __future__ import annotations

from pathlib import Path

from .. import mcp
from ..core.config import ConfigStore
from ..tools import mcp_tools


def start_mcp(store: ConfigStore, workspace: Path) -> mcp.MCPRegistry | None:
    """Connect enabled MCP servers for one workspace.

    A broken server is reported and skipped. The caller owns the returned
    registry and must close it when that execution scope ends.
    """
    settings = store.load()
    entries = settings.mcp_servers
    if not entries:
        return None
    for problem in mcp_tools.config_problems(entries):
        print(f"mcp: {problem}")
    registry = mcp.MCPRegistry()
    try:
        for raw in entries:
            config = mcp.ServerConfig.from_dict(raw)
            if config is None or not config.enabled:
                continue
            try:
                names = registry.add(config, cwd=workspace)
            except mcp.MCPError as error:
                print(f"mcp: '{config.name}' failed to start: {error}")
                continue
            print(f"mcp: '{config.name}' provided {len(names)} tool(s)")
    except BaseException:
        registry.close()
        raise
    if not registry.servers:
        registry.close()
        return None
    return registry
