"""File tools: read, write, edit, glob, grep.

Editing uses exact string replacement with a uniqueness requirement. That is not
a shortcut: it matches how a model reasons ("this text becomes that text") and it
makes an ambiguous edit fail loudly instead of writing to the wrong place.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ._paths import FileAccessDenied, open_file, reject_link

MAX_READ_LINES = 2000
MAX_READ_BYTES = 400_000
MAX_GREP_RESULTS = 200
MAX_GLOB_RESULTS = 500
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build", ".next",
}


class ReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=MAX_READ_LINES, ge=1, le=MAX_READ_LINES)


class WriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    content: str


class EditArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str = ""
    replace_all: bool = False


class GlobArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pattern: str = Field(min_length=1)
    path: str = "."


class GrepArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pattern: str = Field(min_length=1)
    path: str = "."
    glob: str = ""
    ignore_case: bool = False
    max_results: int = Field(default=MAX_GREP_RESULTS, ge=1, le=MAX_GREP_RESULTS)


def resolve(context: Any, raw: str) -> Path:
    """Resolve a caller path inside the workspace; refuse escapes."""
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = context.root / path
    try:
        resolved = path.resolve()
    except OSError as error:
        raise FileAccessDenied(f"Cannot resolve path: {error}") from error
    if not resolved.is_relative_to(context.root):
        raise PermissionError(
            f"Path is outside the workspace: {raw} (workspace is {context.root})"
        )
    return resolved


def _read_text(path: Path, *, offset: int, limit: int) -> dict[str, Any]:
    with open_file(path, "read") as handle:
        data = handle.read(MAX_READ_BYTES + 1)
    oversized = len(data) > MAX_READ_BYTES
    text = data[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)
    start = min(offset - 1, total)
    selected = lines[start:start + limit]
    body = "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(selected))
    if oversized:
        body += f"\n... [file larger than {MAX_READ_BYTES} bytes; read a line range] ..."
    elif start + limit < total:
        body += f"\n... [{total - start - len(selected)} more lines] ..."
    return {
        "content": body or "(empty file)",
        "path": str(path),
        "lines": total,
        "offset": start + 1,
        "returned": len(selected),
    }


def read(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve(context, args["path"])
    info = path.lstat()
    reject_link(info)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return _read_text(path, offset=args["offset"], limit=args["limit"])


def write(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve(context, args["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    with open_file(path, "write") as handle:
        handle.write(args["content"].encode("utf-8"))
    lines = args["content"].count("\n") + (1 if args["content"] else 0)
    return {
        "content": f"{'Overwrote' if existed else 'Created'} {path} ({lines} lines)",
        "path": str(path),
        "bytes": len(args["content"].encode("utf-8")),
        "created": not existed,
    }


def edit(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve(context, args["path"])
    with open_file(path, "read") as handle:
        original = handle.read().decode("utf-8", errors="replace")
    old = args["old_text"]
    count = original.count(old)
    if count == 0:
        raise ValueError(
            "old_text not found. Read the file first and copy the exact text, "
            "including indentation."
        )
    if count > 1 and not args["replace_all"]:
        raise ValueError(
            f"old_text appears {count} times. Add surrounding context to make it "
            "unique, or set replace_all."
        )
    updated = original.replace(old, args["new_text"]) if args["replace_all"] else (
        original.replace(old, args["new_text"], 1)
    )
    with open_file(path, "write") as handle:
        handle.write(updated.encode("utf-8"))
    replaced = count if args["replace_all"] else 1
    return {
        "content": f"Edited {path} ({replaced} replacement{'s' if replaced != 1 else ''})",
        "path": str(path),
        "replaced": replaced,
    }


def _walk(root: Path):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            yield Path(current) / name


def glob_paths(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    base = resolve(context, args["path"])
    if not base.is_dir():
        raise ValueError(f"Not a directory: {base}")
    pattern = args["pattern"].replace("\\", "/")
    matches: list[str] = []
    for path in _walk(base):
        relative = path.relative_to(context.root).as_posix()
        candidate = relative if "/" in pattern else path.name
        if fnmatch.fnmatch(candidate, pattern) or fnmatch.fnmatch(relative, pattern):
            matches.append(relative)
            if len(matches) >= MAX_GLOB_RESULTS:
                break
    matches.sort()
    body = "\n".join(matches) if matches else "(no matches)"
    return {"content": body, "count": len(matches), "truncated": len(matches) >= MAX_GLOB_RESULTS}


def grep(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    base = resolve(context, args["path"])
    flags = re.IGNORECASE if args["ignore_case"] else 0
    try:
        pattern = re.compile(args["pattern"], flags)
    except re.error as error:
        raise ValueError(f"Invalid regular expression: {error}") from error
    hits: list[str] = []
    scanned = 0
    for path in _walk(base if base.is_dir() else base.parent):
        if args["glob"] and not fnmatch.fnmatch(path.name, args["glob"]):
            continue
        try:
            info = path.lstat()
            reject_link(info)
            if info.st_size > MAX_READ_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError):
            continue
        scanned += 1
        for number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                relative = path.relative_to(context.root).as_posix()
                hits.append(f"{relative}:{number}: {line.strip()[:200]}")
                if len(hits) >= args["max_results"]:
                    break
        if len(hits) >= args["max_results"]:
            break
    body = "\n".join(hits) if hits else "(no matches)"
    return {
        "content": body,
        "matches": len(hits),
        "files_scanned": scanned,
        "truncated": len(hits) >= args["max_results"],
    }
