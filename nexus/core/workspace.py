"""Workspace view: the facts an agent needs about where it is standing.

This is the "live repo context" of a coding harness. It is deliberately cheap
and fail-soft: a missing git binary or a non-repo directory yields an empty
section, never an error.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "NEXUS.md")
MAX_INSTRUCTION_BYTES = 32_000
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


def load_instructions(root: Path) -> str:
    """Collect instruction files from the root up to the filesystem root.

    Nearest file wins in emphasis, so ancestors come first. Missing or unreadable
    files contribute nothing.
    """
    parts: list[str] = []
    chain: list[Path] = []
    current = root.resolve()
    while True:
        chain.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    for directory in reversed(chain):
        for name in INSTRUCTION_FILES:
            candidate = directory / name
            try:
                info = candidate.lstat()
            except OSError:
                continue
            if not candidate.is_file() or info.st_size > MAX_INSTRUCTION_BYTES:
                continue
            stat_result = os.lstat(candidate)
            # Refuse symlinks/junctions: an instruction file must be a real file.
            if getattr(stat_result, "st_file_attributes", 0) & 0x400:
                continue
            try:
                parts.append(candidate.read_text(encoding="utf-8", errors="replace").strip())
            except OSError:
                continue
    sections = [part for part in parts if part]
    if not sections:
        return ""
    return "\n\n---\n\n".join(sections)


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
