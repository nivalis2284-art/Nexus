"""Bridge MCP declarations into the ordinary tool registry.

MCP tools are just tools: same dispatcher, same result shape, same clipping.
The only difference is that their schema comes from another process, so the
argument model is a passthrough and the server does the validating.

This module is deliberately thin. It exists so ``tools/__init__`` does not have
to know MCP's types, and so the bridge is one file if it ever needs replacing.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from .registry import ToolSpec


class MCPArguments(BaseModel):
    """Passthrough arguments.

    A server's JSON Schema is not a pydantic model, and translating arbitrary
    JSON Schema correctly is a large problem. Since the server validates anyway,
    accepting any object and forwarding it is both simpler and more accurate than
    a lossy translation that would reject valid calls.
    """

    model_config = ConfigDict(extra="allow")


def specs_for(context: Any) -> list[ToolSpec]:
    registry = getattr(context, "mcp", None)
    if registry is None:
        return []

    specs: list[ToolSpec] = []
    for qualified, (server, declaration) in registry.tools.items():
        specs.append(
            ToolSpec(
                qualified,
                _describe(server.name, declaration),
                MCPArguments,
                _make_handler(registry, qualified),
                read_only=False,
            )
        )
    return specs


def _describe(server_name: str, declaration: Any) -> str:
    text = declaration.description or declaration.name
    return f"[MCP:{server_name}] {text}"


def _make_handler(registry: Any, qualified: str):
    def handler(context: Any, args: dict[str, Any]) -> dict[str, Any]:
        entry = registry.tools.get(qualified)
        if entry is None:
            raise ValueError(f"MCP tool '{qualified}' is no longer available")
        server, declaration = entry
        result = server.call_tool(declaration.name, args)
        if not result.ok:
            # An error result is data the model should read, not an exception.
            return {
                "content": result.text,
                "mcp_error": True,
                "server": server.name,
                "tool": declaration.name,
            }
        return {
            "content": result.text,
            "server": server.name,
            "tool": declaration.name,
        }

    handler.__name__ = f"mcp_{qualified}"
    return handler


def config_problems(raw: Any) -> list[str]:
    """Validate the ``mcp.servers`` config block without starting anything."""
    problems: list[str] = []
    if raw is None:
        return problems
    if not isinstance(raw, list):
        return ["mcp.servers must be a list"]
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            problems.append(f"mcp.servers[{index}] is not an object")
            continue
        name = str(item.get("name") or "").strip()
        command = str(item.get("command") or "").strip()
        if not name:
            problems.append(f"mcp.servers[{index}] has no name")
        elif not name.replace("-", "").replace("_", "").isalnum():
            problems.append(f"mcp.servers[{index}] name '{name}' has invalid characters")
        elif name in seen:
            problems.append(f"duplicate mcp server name '{name}'")
        else:
            seen.add(name)
        if not command:
            problems.append(f"mcp.servers[{index}] has no command")
    return problems


def summary(registry: Any) -> str:
    if registry is None or not registry.servers:
        return ""
    return json.dumps(
        {name: len([t for t, (s, _) in registry.tools.items() if s.name == name])
         for name in registry.servers},
        ensure_ascii=False,
    )
