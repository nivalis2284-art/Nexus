"""MCP connection construction for one execution owner.

The service may keep one registry alive for the whole Nexus process; a task
without such an owner starts and closes its own registry instead.
"""

from __future__ import annotations

from pathlib import Path

from .. import mcp


def start_mcp(entries: list[dict], workspace: Path) -> mcp.MCPRegistry | None:
    """Connect enabled stdio and HTTP servers for one workspace.

    A broken server is reported and skipped. Configuration diagnostics are
    handled by the MCP registry implementation. The caller owns the returned
    registry and must close it when that execution scope ends.
    """
    if not entries:
        return None
    registry = mcp.MCPRegistry()
    try:
        for raw in entries:
            config = mcp.ServerConfig.from_dict(raw)
            if config is None or not config.enabled:
                continue
            try:
                names = registry.add(config, cwd=workspace)
            except (mcp.MCPError, OSError) as error:
                print(f"mcp: '{config.name}' failed to start: {error}")
                continue
            print(f"mcp: '{config.name}' provided {len(names)} tool(s)")
        return registry
    except BaseException:
        registry.close()
        raise
