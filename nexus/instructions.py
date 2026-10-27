"""The owner's persistent simple instructions."""

from __future__ import annotations

from pathlib import Path

from .file_io import replace_text

INSTRUCTIONS_NAME = "owner-instructions.txt"


def _path(home) -> Path:
    return Path(home).expanduser() / INSTRUCTIONS_NAME


def load_instructions(home) -> str:
    path = _path(home)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def save_instructions(home, text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("instructions must be text")
    path = _path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_text(path, text)
    return text
