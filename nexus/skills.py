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

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
_KEY_LINE = re.compile(r"^([^\s#][^:]*):")


class SkillEncodingError(ValueError):
    """A skill file cannot be decoded without losing its original text."""


def read_skill_text(path: Path | str) -> str:
    """Read a skill as UTF-8, or as GBK for existing Chinese files.

    Both decoders are strict. A file that is neither is left untouched and
    reported to the editor instead of being rewritten with replacement
    characters.
    """
    target = Path(path)
    data = target.read_bytes()
    for encoding in ("utf-8", "gbk"):
        try:
            text = data.decode(encoding)
            return text.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    raise SkillEncodingError(
        f"无法读取技能/人设文件 {target}：内容不是有效的 UTF-8 或 GBK 编码。"
        "请将文件重新保存为 UTF-8 或 GBK 后再编辑；原文件未被修改。"
    )


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
        values[key] = _scalar_value(value)
        current_key = key
    return values, body


def _scalar_value(value: str) -> str:
    """Decode the scalar forms our editor writes and older files use."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            decoded = json.loads(value)
        except ValueError:
            decoded = value[1:-1]
        if isinstance(decoded, str):
            return decoded
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _yaml_string(value: str) -> str:
    """A one-line YAML scalar that carries every string losslessly."""
    if not isinstance(value, str):
        raise TypeError("name and description must be text")
    return json.dumps(value, ensure_ascii=False)


def write_skill_file(path: Path | str, *, name: str, description: str, body: str) -> Path:
    """Write one skill document, preserving frontmatter keys we do not edit.

    The body is written exactly as supplied. Existing when and custom
    metadata blocks keep their original text; only top-level name and
    description entries are replaced.
    """
    target = Path(path)
    if not isinstance(body, str):
        raise TypeError("body must be text")
    try:
        existing = read_skill_text(target)
    except FileNotFoundError:
        existing = ""

    match = FRONTMATTER.match(existing)
    if match:
        metadata = _updated_frontmatter(match.group(1), name, description)
    else:
        metadata = f"name: {_yaml_string(name)}\ndescription: {_yaml_string(description)}"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"---\n{metadata}\n---\n{body}", encoding="utf-8")
    return target


def _updated_frontmatter(block: str, name: str, description: str) -> str:
    """Replace known top-level entries without disturbing the rest of the block."""
    chunks: list[list[str]] = []
    current: list[str] | None = None
    for line in block.splitlines(keepends=True):
        if _KEY_LINE.match(line):
            current = [line]
            chunks.append(current)
        elif current is not None:
            current.append(line)
        else:
            chunks.append([line])

    replacements = {"name": f"name: {_yaml_string(name)}\n",
                    "description": f"description: {_yaml_string(description)}\n"}
    result: list[str] = []
    inserted: set[str] = set()
    for chunk in chunks:
        key_match = _KEY_LINE.match(chunk[0])
        key = key_match.group(1).strip().lower() if key_match else ""
        if key in replacements:
            if key not in inserted:
                if key == "description" and "name" not in inserted:
                    result.append(replacements["name"])
                    inserted.add("name")
                result.append(replacements[key])
                inserted.add(key)
            continue
        result.extend(chunk)

    for key in ("name", "description"):
        if key not in inserted:
            result.append(replacements[key])
    return "".join(result).rstrip("\r\n")


def _read_skill_file(path: Path) -> Skill | None:
    try:
        text = read_skill_text(path)
    except (OSError, SkillEncodingError):
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
