"""The owner's persistent simple instructions."""

from __future__ import annotations

import json
from pathlib import Path

from .file_io import replace_text

INSTRUCTIONS_NAME = "owner-instructions.txt"
OWNER_ITEMS_NAME = "owner-instruction-items.json"


def _path(home) -> Path:
    return Path(home).expanduser() / INSTRUCTIONS_NAME


def _items_path(home) -> Path:
    return Path(home).expanduser() / OWNER_ITEMS_NAME


def _structured(home) -> list[str] | None:
    path = _items_path(home)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        return None
    return raw


def load_instructions(home) -> str:
    path = _path(home)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def save_instructions(home, text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("instructions must be text")
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = load_instructions(home)
    replace_text(path, text)
    items = _structured(home)
    if previous == text and items is not None:
        return text
    items_path = _items_path(home)
    if not text:
        items_path.unlink(missing_ok=True)
    else:
        replace_text(items_path, json.dumps([text], ensure_ascii=False, indent=2) + "\n")
    return text
