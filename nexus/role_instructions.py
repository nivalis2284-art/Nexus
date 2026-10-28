"""Persistent, owner-editable role prompts.

Defaults are imported from the modules that actually use them.  Keeping the
defaults there avoids a second copy that silently drifts; only an owner edit is
written to ``roles.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .file_io import replace_text

ROLES_NAME = "roles.json"
ROLE_IDS = ("main", "work", "subagent")


def _path(home) -> Path:
    return Path(home).expanduser() / ROLES_NAME


def _saved(home) -> dict[str, str]:
    path = _path(home)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"roles.json is not valid JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ValueError("roles.json must be an object")
    return {key: value for key, value in raw.items()
            if key in ROLE_IDS and isinstance(value, str)}


def defaults() -> dict[str, str]:
    """The prompts currently assembled by each real runtime.

    Imported lazily: ``ling`` imports runtime modules that import this module.
    """
    from .core.prompt import ROLE, ROLE_RULES
    from .ling import LING_INSTRUCTIONS

    return {
        "main": LING_INSTRUCTIONS + "\n\n" + ROLE_RULES,
        "work": ROLE,
        "subagent": ROLE,
    }


def load_role(home, role_id: str) -> str:
    if role_id not in ROLE_IDS:
        raise ValueError(f"unknown role: {role_id}")
    return _saved(home).get(role_id) or defaults()[role_id]


def save_role(home, role_id: str, text: str) -> str:
    if role_id not in ROLE_IDS:
        raise ValueError(f"unknown role: {role_id}")
    if not isinstance(text, str):
        raise ValueError("role text must be text")
    if not text.strip():
        raise ValueError("role text must not be empty")
    values = _saved(home)
    values[role_id] = text
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_text(path, json.dumps(values, ensure_ascii=False, indent=2) + "\n")
    return text
