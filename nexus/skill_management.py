"""List and edit skills using the same roots as chat discovery."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import skills

_SOURCES = ("project", "personal", "builtin")


class SkillManager:
    """Personal and project skills are editable; builtin skills are read-only."""

    def __init__(self, home: Path | str, workspace: Path | str | None,
                 builtin: Path | str) -> None:
        self.home = Path(home).expanduser().resolve()
        self.workspace = (
            Path(workspace).expanduser().resolve() if workspace is not None else None
        )
        self.builtin = Path(builtin).expanduser().resolve()

    def _roots(self) -> list[tuple[str, Path]]:
        roots: list[tuple[str, Path]] = []
        if self.workspace is not None:
            roots.append(("project", self.workspace / ".nexus" / "skills"))
        roots.append(("personal", self.home / "skills"))
        roots.append(("builtin", self.builtin))
        return roots

    def _root(self, source: str) -> Path:
        for name, root in self._roots():
            if name == source:
                return root
        raise ValueError("unknown skill source")

    def list(self) -> dict[str, list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        claimed: set[str] = set()
        for source, root in self._roots():
            for path in self._paths(root):
                try:
                    text = skills.read_skill_text(path)
                except OSError:
                    continue
                skill = skills._read_skill_file(path)
                if skill is None:
                    continue
                _metadata, body = skills.parse_frontmatter(text)
                effective = skill.name not in claimed
                rows.append({
                    "source": source,
                    "path": path.relative_to(root).as_posix(),
                    "name": skill.name,
                    "description": skill.description,
                    "body": body,
                    "effective": effective,
                    "read_only": source == "builtin",
                })
                claimed.add(skill.name)
        return {"skills": rows}

    @staticmethod
    def _paths(root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        paths: list[Path] = []
        for entry in sorted(root.iterdir()):
            if entry.is_dir():
                candidate = entry / "SKILL.md"
                if candidate.is_file():
                    paths.append(candidate)
            elif entry.suffix.lower() == ".md":
                paths.append(entry)
        return paths

    def save(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise TypeError("skill values must be an object")
        source = str(values.get("source") or "personal")
        path = str(values.get("path") or "")
        name = self._text(values, "name").strip()
        description = self._text(values, "description")
        body = self._text(values, "body")
        if not skills.NAME_PATTERN.match(name):
            raise ValueError("skill name must use lowercase letters, digits and hyphens")
        if not description.strip():
            raise ValueError("skill description is required")
        if source not in _SOURCES:
            raise ValueError("unknown skill source")
        if source == "builtin":
            raise ValueError("builtin skills are read-only")

        root = self._root(source)
        if path:
            current = next(
                (item for item in self.list()["skills"]
                 if item["source"] == source and item["path"] == path),
                None,
            )
            if current is None:
                raise ValueError("skill not found")
            target = self._inside(root, path)
            if target.parent == root:
                skills.write_skill_file(target, name=name, description=description, body=body)
            else:
                folder = target.parent
                if folder.name != name:
                    destination = self._inside(root, name)
                    if destination.exists():
                        raise ValueError("a skill folder with that name already exists")
                    folder.rename(destination)
                    folder = destination
                skills.write_skill_file(
                    folder / "SKILL.md", name=name, description=description, body=body
                )
        else:
            target = self._inside(root, f"{name}/SKILL.md")
            if target.parent.exists():
                raise ValueError("a skill with that name already exists")
            skills.write_skill_file(target, name=name, description=description, body=body)
        return self.list()

    def delete(self, values: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(values, dict):
            raise TypeError("skill values must be an object")
        source = str(values.get("source") or "")
        path = str(values.get("path") or "")
        if source not in _SOURCES:
            raise ValueError("unknown skill source")
        if source == "builtin":
            raise ValueError("builtin skills are read-only")
        if not path:
            raise ValueError("skill path is required")

        root = self._root(source)
        if not any(
            item["source"] == source and item["path"] == path
            for item in self.list()["skills"]
        ):
            raise ValueError("skill not found")
        target = self._inside(root, path)
        relative = target.relative_to(root)
        if (len(relative.parts) == 2 and relative.parts[1] == "SKILL.md"):
            folder = target.parent
            if not target.is_file():
                raise ValueError("skill not found")
            shutil.rmtree(folder)
        elif len(relative.parts) == 1 and target.suffix.lower() == ".md":
            if not target.is_file():
                raise ValueError("skill not found")
            target.unlink()
        else:
            raise ValueError("not a skill path")
        return self.list()

    @staticmethod
    def _text(values: dict[str, Any], key: str) -> str:
        value = values.get(key)
        if not isinstance(value, str):
            raise TypeError(f"{key} must be text")
        return value

    @staticmethod
    def _inside(root: Path, path: str) -> Path:
        root = root.resolve()
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            raise ValueError("skill path is outside its source")
        return candidate
