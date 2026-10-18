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
from .registry import ToolSpec

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


def _near_misses(original: str, old: str, limit: int = 3) -> list[int]:
    """Line numbers where the first line of ``old`` appears, loosely.

    Only used to build an error message. A failed edit is almost always a
    whitespace or drift problem, and telling the model *where* the text actually
    is lets it correct itself instead of re-reading the entire file.
    """
    marker = old.strip().splitlines()[0].strip() if old.strip() else ""
    if not marker:
        return []
    hits: list[int] = []
    for index, line in enumerate(original.splitlines(), start=1):
        if marker in line.strip():
            hits.append(index)
            if len(hits) >= limit:
                break
    return hits


def edit(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve(context, args["path"])
    with open_file(path, "read") as handle:
        original = handle.read().decode("utf-8", errors="replace")
    old = args["old_text"]
    count = original.count(old)
    if count == 0:
        hint = ""
        lines = _near_misses(original, old)
        if lines:
            where = ", ".join(str(n) for n in lines)
            hint = f" The closest match is near line {where}; read that region and copy it exactly."
        raise ValueError(
            "old_text not found. It must match the file byte for byte, including "
            "indentation and blank lines." + hint
        )
    if count > 1 and not args["replace_all"]:
        lines = [
            index
            for index, line in enumerate(original.splitlines(), start=1)
            if old.splitlines() and line.strip().startswith(old.splitlines()[0].strip())
        ][:5]
        where = f" (first lines: {', '.join(str(n) for n in lines)})" if lines else ""
        raise ValueError(
            f"old_text appears {count} times{where}. Add surrounding context to make "
            "it unique, or set replace_all."
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


# The declarative list for this module. Adding a tool means appending here; the
# assembly code in tools/catalog.py never needs to change.
TOOLS = [
    ToolSpec(
        "read_file",
        "Read a UTF-8 file with line numbers. Use offset/limit for large files.",
        ReadArgs, read, read_only=True,
    ),
    ToolSpec(
        "write_file",
        "Create or overwrite a file. Prefer edit_file for existing files.",
        WriteArgs, write,
    ),
    ToolSpec(
        "edit_file",
        "Replace an exact, unique block of text in a file. old_text must match the "
        "file byte for byte; if it is not unique the edit is refused.",
        EditArgs, edit,
    ),
    ToolSpec(
        "glob",
        "Find files by glob pattern (e.g. **/*.py).",
        GlobArgs, glob_paths, read_only=True,
    ),
    ToolSpec(
        "grep",
        "Search file contents with a regular expression.",
        GrepArgs, grep, read_only=True,
    ),
]
