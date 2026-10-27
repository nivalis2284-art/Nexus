"""Display projection shared by web and public plugin chat snapshots."""

from typing import Any


def transcript(session) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in session.items:
        if item.kind in ("user", "assistant"):
            if item.kind == "assistant" and not item.text and not item.data.get("thinking"):
                continue
            out.append(
                {
                    "role": item.kind,
                    "message_id": item.data.get("message_id"),
                    "text": item.text,
                    "thinking": item.data.get("thinking") or "",
                    "usage": item.data.get("usage") or None,
                    "elapsed": item.data.get("elapsed"),
                    "at": item.at,
                    "images": item.data.get("images") or [],
                    "attachments": item.data.get("attachments") or [],
                    "automation_run_id": item.data.get("automation_run_id"),
                    "plugin_id": item.data.get("plugin_id"),
                    "plugin_event_id": item.data.get("plugin_event_id"),
                    "sources": item.data.get("sources") or [],
                }
            )
        elif item.kind == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": item.data.get("tool_call_id"),
                    "parent_message_id": item.data.get("parent_message_id"),
                    "name": item.data.get("name", ""),
                    "text": item.text,
                    "ok": bool(item.data.get("ok", True)),
                    "arguments": item.data.get("arguments"),
                    "error": item.data.get("error", ""),
                    "at": item.at,
                }
            )
        elif item.kind == "notice":
            out.append({"role": "notice", "text": item.text, "at": item.at})
    return out
