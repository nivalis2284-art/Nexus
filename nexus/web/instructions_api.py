"""HTTP access to the owner's persistent instructions."""

from __future__ import annotations

from typing import Any

from ..instructions import load_instructions, save_instructions

PATH = "/api/instructions"


def route(method: str, path: str, body: Any, state: dict[str, Any]):
    """Handle the instructions endpoint; ``None`` means another route owns it."""
    if path != PATH:
        return None
    home = state["store"].home
    if method == "GET":
        try:
            return 200, {"text": load_instructions(home)}
        except OSError as error:
            return 500, {"error": str(error)}
    if method != "PUT":
        return 405, {"error": "method not allowed"}
    text = body.get("text") if isinstance(body, dict) else None
    try:
        return 200, {"text": save_instructions(home, text)}
    except ValueError as error:
        return 400, {"error": str(error)}
    except OSError as error:
        return 500, {"error": str(error)}
