"""Skills: reusable instruction bundles discovered from disk.

Layout, copied from ``dsh-skill-filesystem``::

    skills/<name>/SKILL.md     # YAML frontmatter + body
    skills/<name>.md           # flat form, same frontmatter

Frontmatter keys: ``name``, ``description``, ``when``. Only the summary and the
``when`` line go into the system prompt; the body is read on demand through the
existing ``read_skill`` tool. Loading every body up front would spend context on
skills the turn never uses.

Two roots, project before user, so a project can pin its own conventions::

    <workspace>/.nexus/skills/
    ~/.nexus/skills/
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    when: str
    body: str
    path: str = ""

    def summary_line(self) -> str:
        """The single line that goes into the system prompt."""
        tail = f" Use when: {self.when}" if self.when else ""
        return f"- {self.name}: {self.description}{tail}"


@dataclass
class SkillSet:
    skills: dict[str, Skill] = field(default_factory=dict)

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def bodies(self) -> dict[str, str]:
        """Map of name -> body, the shape the ``read_skill`` tool consumes."""
        return {name: skill.body for name, skill in self.skills.items()}

    def manifest(self) -> str:
        """Compact list for the system prompt. No bodies."""
        if not self.skills:
            return ""
        lines = [skill.summary_line() for skill in sorted(
            self.skills.values(), key=lambda item: item.name
        )]
        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self.skills)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Read the leading ``---`` block.

    Deliberately a small hand-rolled parser rather than a YAML dependency: the
    accepted subset is ``key: value`` on one line, plus a ``|`` block scalar for
    multi-line values. Anything else is ignored, which is safe here because the
    fields are advisory text, not configuration.
    """
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text
    block = match.group(1)
    body = text[match.end():]
    values: dict[str, str] = {}
    current_key: str | None = None
    for raw in block.splitlines():
        if not raw.strip():
            continue
        if raw.startswith((" ", "\t")) and current_key:
            values[current_key] += "\n" + raw.strip()
            continue
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if value in ("|", ">"):
            values[key] = ""
            current_key = key
            continue
        values[key] = value.strip("\"'")
        current_key = key
    return values, body


def _read_skill_file(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body = parse_frontmatter(text)
    stem = path.parent.name if path.name.lower() == "skill.md" else path.stem
    name = (meta.get("name") or stem).strip().lower()
    if not NAME_PATTERN.match(name):
        return None
    description = (meta.get("description") or "").strip()
    if not description:
        # Without a description the skill cannot be chosen, so it is not loaded.
        return None
    return Skill(
        name=name,
        description=description,
        when=(meta.get("when") or "").strip(),
        body=body.strip(),
        path=str(path),
    )


def discover(roots: list[Path]) -> SkillSet:
    """Load skills from the given roots; earlier roots win on name clashes."""
    found: dict[str, Skill] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            candidates: list[Path] = []
            if entry.is_dir():
                skill_md = entry / "SKILL.md"
                if skill_md.is_file():
                    candidates.append(skill_md)
            elif entry.suffix.lower() == ".md":
                candidates.append(entry)
            for candidate in candidates:
                skill = _read_skill_file(candidate)
                if skill is None:
                    continue
                found.setdefault(skill.name, skill)
    return SkillSet(skills=found)


def default_roots(workspace: Path | None, home: Path) -> list[Path]:
    """Project skills before user skills, so a project can override."""
    roots: list[Path] = []
    if workspace is not None:
        roots.append(Path(workspace) / ".nexus" / "skills")
    roots.append(Path(home) / "skills")
    return roots
