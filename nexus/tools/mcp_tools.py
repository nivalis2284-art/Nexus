"""Bridge MCP declarations into the ordinary tool registry.

MCP tools are just tools: same dispatcher, same result shape, same clipping.
The only difference is that their schema comes from another process, so the
argument model is a passthrough and the server does the validating.

This module is deliberately thin. It exists so ``tools/__init__`` does not have
to know MCP's types, and so the bridge is one file if it ever needs replacing.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

from pydantic import BaseModel, ConfigDict

from ..mcp import MCPError
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
        visible = getattr(server, 'model_tools', None)
        if visible is not None and declaration.name not in visible:
            continue
        specs.append(
            ToolSpec(
                qualified,
                _describe(server.name, declaration),
                MCPArguments,
                _make_handler(registry, qualified),
                read_only=False,
                input_schema=declaration.input_schema,
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
        try:
            manager = getattr(context, 'plugin_manager', None)
            owner = getattr(server, 'plugin_id', None)
            with (manager.lease(owner, context, getattr(context, 'plugin_scope', {}))
                  if manager and owner else nullcontext()):
                result = server.call_tool(declaration.name, args)
        except MCPError as error:
            return {"content": f"MCP call failed: {error}", "mcp_error": True,
                    "server": server.name, "tool": declaration.name}
        if not result.ok:
            # An error result is data the model should read, not an exception.
            payload: dict[str, Any] = {
                "content": result.text,
                "mcp_error": True,
                "server": server.name,
                "tool": declaration.name,
            }
            if result.structured_content is not None:
                payload["data"] = result.structured_content
            return payload
        payload = {"server": server.name, "tool": declaration.name}
        if result.structured_content is not None:
            payload["data"] = result.structured_content
            payload["content"] = json.dumps(
                result.structured_content, ensure_ascii=False, indent=1
            )
        else:
            payload["content"] = result.text
        return payload

    handler.__name__ = f"mcp_{qualified}"
    return handler
