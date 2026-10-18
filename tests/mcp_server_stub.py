"""A minimal MCP server used by the tests.

Speaks the real protocol over stdio so the client is exercised end to end:
initialize, tools/list (with pagination), tools/call, and an error result.
Standard library only, same as the client.
"""

from __future__ import annotations

import json
import sys

PROTOCOL_VERSION = "2025-06-18"

TOOLS_PAGE_1 = [
    {
        "name": "echo",
        "description": "Echo the given text back.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
]
TOOLS_PAGE_2 = [
    {
        "name": "fail",
        "description": "Always returns an error result.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "env_probe",
        "description": "Report whether a secret-looking env var leaked.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        method = message.get("method")
        request_id = message.get("id")

        # Notifications carry no id and must not be answered.
        if request_id is None:
            continue

        if method == "initialize":
            respond({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test-server", "version": "1.0"},
                },
            })
        elif method == "tools/list":
            cursor = (message.get("params") or {}).get("cursor")
            if not cursor:
                respond({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": TOOLS_PAGE_1, "nextCursor": "page2"},
                })
            else:
                respond({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": TOOLS_PAGE_2},
                })
        elif method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if name == "echo":
                respond({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": str(arguments.get("text", ""))}]
                    },
                })
            elif name == "fail":
                respond({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "deliberate failure"}],
                    },
                })
            elif name == "env_probe":
                import os

                leaked = any(
                    "SECRET" in key.upper() or "TOKEN" in key.upper()
                    for key in os.environ
                )
                respond({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [{"type": "text", "text": "leaked" if leaked else "clean"}]
                    },
                })
            else:
                respond({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"unknown tool {name}"},
                })
        else:
            respond({
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"unknown method {method}"},
            })


if __name__ == "__main__":
    main()
