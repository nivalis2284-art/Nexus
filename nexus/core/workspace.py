"""Workspace view: the facts an agent needs about where it is standing.

This is the "live repo context" of a coding harness. It is deliberately cheap
and fail-soft: a missing git binary or a non-repo directory yields an empty
section, never an error.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "NEXUS.md")
MAX_INSTRUCTION_BYTES = 32_000
INSTRUCTION_HEAD_BYTES = 24_000
INSTRUCTION_TAIL_BYTES = 6_000
GIT_TIMEOUT = 3.0


@dataclass(frozen=True)
class Workspace:
    root: Path
    branch: str = ""
    status: str = ""
    recent: str = ""
    instructions: str = ""
    is_repo: bool = False

    def summary(self) -> str:
        lines = [f"Working directory: {self.root}"]
        if self.is_repo:
            lines.append(f"Git branch: {self.branch or '(detached)'}")
            if self.status:
                lines.append("Git status:\n" + self.status)
            else:
                lines.append("Git status: clean")
            if self.recent:
                lines.append("Recent commits:\n" + self.recent)
        else:
            lines.append("Git: not a repository")
        return "\n".join(lines)


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _instruction_body(candidate: Path, size: int) -> str:
    if size <= MAX_INSTRUCTION_BYTES:
        return candidate.read_text(encoding="utf-8", errors="replace").strip()
    with candidate.open("rb") as handle:
        head = handle.read(INSTRUCTION_HEAD_BYTES).decode("utf-8", errors="replace")
        handle.seek(-INSTRUCTION_TAIL_BYTES, os.SEEK_END)
        tail = handle.read().decode("utf-8", errors="replace")
    # A byte split through a multi-byte character decodes to U+FFFD at the cut.
    head = head.rstrip("\ufffd").rstrip()
    tail = tail.lstrip("\ufffd").lstrip()
    omitted = size - INSTRUCTION_HEAD_BYTES - INSTRUCTION_TAIL_BYTES
    notice = (
        f"\n\n[Instruction file is {size:,} bytes; showing its first "
        f"{INSTRUCTION_HEAD_BYTES:,} and last {INSTRUCTION_TAIL_BYTES:,} bytes. "
        f"{omitted:,} middle bytes were omitted. Read the file directly if needed.]\n\n"
    )
    return head + notice + tail


def _instruction_directories(root: Path, paths: Iterable[str | Path]) -> list[Path]:
    """The root's ancestor chain, the root, then any touched descendant directory.

    The ancestor chain is kept because a global instruction file may live above
    the workspace. Touched paths only add directories *inside* the workspace, so
    an unrelated sibling is never read.
    """
    chain: list[Path] = []
    current = root
    while True:
        chain.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    directories = list(reversed(chain))
    descendants: set[Path] = set()
    for raw in paths:
        try:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = root / path
            path = path.resolve()
        except (OSError, TypeError, ValueError):
            continue
        if not path.is_relative_to(root):
            continue
        directory = path if path.is_dir() else path.parent
        while True:
            if directory == root:
                break
            descendants.add(directory)
            parent = directory.parent
            if parent == directory or not parent.is_relative_to(root):
                break
            directory = parent
    directories.extend(sorted(descendants, key=lambda item: (len(item.parts), item.as_posix())))
    return directories


def _instruction_scope(directory: Path, root: Path) -> str:
    if directory == root:
        return "."
    if directory.is_relative_to(root):
        return directory.relative_to(root).as_posix() + "/"
    return str(directory)


def load_instructions(
    root: Path,
    paths: Iterable[str | Path] = (),
) -> str:
    """Collect labeled instruction files for this workspace and touched paths.

    The chain from the filesystem root down to the workspace root is always read,
    so a global instruction file above the workspace still applies; touched paths
    add their own directories inside the workspace. Large files retain both ends
    with a visible notice instead of disappearing from the prompt.
    """
    root = Path(root).expanduser().resolve()
    sections: list[str] = []
    seen: set[Path] = set()
    for directory in _instruction_directories(root, paths):
        for name in INSTRUCTION_FILES:
            candidate = directory / name
            try:
                info = candidate.lstat()
            except OSError:
                continue
            # Refuse symlinks/junctions: an instruction file must be a real file.
            if (
                not candidate.is_file()
                or os.path.islink(candidate)
                or getattr(info, "st_file_attributes", 0) & 0x400
            ):
                continue
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                body = _instruction_body(candidate, info.st_size)
            except OSError:
                continue
            if not body:
                continue
            scope = _instruction_scope(directory, root)
            sections.append(f"### `{candidate}`\nScope: `{scope}`\n\n{body}")
    return "\n\n".join(sections)


def refresh(workspace: Workspace, paths: Iterable[str | Path] = ()) -> Workspace:
    """Reload instructions for the original root and paths touched so far.

    Call this before each model request. Existing instruction text stays intact
    until the next refresh, so a change cannot alter a turn already in progress.
    """
    return replace(
        workspace,
        instructions=load_instructions(workspace.root, paths),
    )


def inspect(root: Path) -> Workspace:
    root = Path(root).expanduser().resolve()
    is_repo = (root / ".git").exists()
    if not is_repo:
        return Workspace(root=root, instructions=load_instructions(root))
    return Workspace(
        root=root,
        branch=_git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        status=_git(root, "status", "--short"),
        recent=_git(root, "log", "--oneline", "-5"),
        instructions=load_instructions(root),
        is_repo=True,
    )
