"""stdio MCP entry point: python -m nexus_scheduler --data <directory>."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .runtime import SchedulerRuntime


def build_server(runtime: SchedulerRuntime) -> MCPServer:
    server = MCPServer(
        "nexus-scheduler",
        instructions=(
            "Manage Nexus schedules and watched web pages. Reads return dictionaries. "
            "Rules are local to this service and only run while this MCP service is open."
        ),
    )

    @server.tool()
    def scheduler_state() -> dict[str, Any]:
        """Return settings, schedules, watches, and the last scheduler error."""
        return runtime.state()

    @server.tool()
    def scheduler_configure(values: dict[str, Any]) -> dict[str, Any]:
        """Update scheduler settings and return the complete current state."""
        runtime.store.configure(values)
        return runtime.state()

    @server.tool()
    def schedule_save(values: dict[str, Any]) -> dict[str, Any]:
        """Create or update a schedule and return the saved record."""
        return runtime.store.save_schedule(values)

    @server.tool()
    def schedule_delete(id: str) -> dict[str, Any]:
        """Delete a schedule by id."""
        return {"deleted": runtime.store.delete_schedule(id)}

    @server.tool()
    def schedule_run(id: str) -> dict[str, Any]:
        """Submit the current schedule to Nexus without changing its next time."""
        return runtime.schedule_run(id)

    @server.tool()
    def watch_save(values: dict[str, Any]) -> dict[str, Any]:
        """Create or update a watched URL and return the saved record."""
        return runtime.store.save_watch(values)

    @server.tool()
    def watch_delete(id: str) -> dict[str, Any]:
        """Delete a watched URL by id."""
        return {"deleted": runtime.store.delete_watch(id)}

    @server.tool()
    def watch_check(id: str) -> dict[str, Any]:
        """Check a watch now; unchanged pages do not create a wake."""
        return runtime.watch_check(id)

    @server.tool()
    def scheduler_connect(callback_url: str) -> dict[str, Any]:
        """Register the Nexus local HTTP callback for wake payloads."""
        return runtime.connect(callback_url)

    @server.tool()
    def scheduler_import(values: dict[str, Any]) -> dict[str, Any]:
        """Import existing settings, schedules, and watches once, preserving baselines."""
        return {"imported": runtime.store.import_state(values)}

    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nexus_scheduler")
    parser.add_argument("--data", required=True, help="service data directory")
    args = parser.parse_args(argv)
    data_dir = Path(args.data).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    runtime = SchedulerRuntime(data_dir)
    runtime.start()
    server = build_server(runtime)
    try:
        server.run("stdio")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
