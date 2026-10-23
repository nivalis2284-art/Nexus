"""Full persona sections and file-backed persona skills."""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from . import skills

_LOCK = threading.RLock()


class InvalidPersona(ValueError):
    pass


def _text(value, label):
    if not isinstance(value, str):
        raise InvalidPersona(f"{label} must be text")
    return value


def _name(value):
    value = _text(value, "name")
    if not skills.NAME_PATTERN.fullmatch(value):
        raise InvalidPersona("name must use lowercase letters, digits and hyphens")
    return value


def _sections(values):
    if not isinstance(values, list):
        raise InvalidPersona("sections must be a list")
    result = []
    for item in values:
        title = _text(item.get("title"), "title")
        if not title.strip():
            raise InvalidPersona("section title is required")
        result.append({"id": item.get("id") or uuid.uuid4().hex[:12],
                       "title": title, "content": _text(item.get("content", ""), "content")})
    if len({s["id"] for s in result}) != len(result):
        raise InvalidPersona("duplicate section id")
    return result


def _defaults(core=""):
    return [{"id": "core", "title": "核心设定", "content": core},
            {"id": "reply-principles", "title": "回复原则", "content": ""}]


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class PersonaStore:
    def __init__(self, home):
        self.home = Path(home).resolve()
        self.path = self.home / "persona.json"
        self.skills_root = self.home / "persona-skills"
        self.request_path = self.home / "persona-last-request.json"
        self.legacy_backup_path = self.home / "persona.json.legacy-backup"

    def _metadata(self):
        if not self.path.exists():
            return {"sections": _defaults(), "entries": []}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if "core" in raw:
            if not self.legacy_backup_path.exists():
                shutil.copy2(self.path, self.legacy_backup_path)
            entries = []
            for item in raw.get("entries", []):
                name = _name(item["id"])
                skills.write_skill_file(self.skills_root / name / "SKILL.md", name=name,
                                        description=item["summary"], body=item["body"])
                entries.append({"name": name, "enabled": item.get("enabled", True)})
            raw = {"sections": _defaults(raw["core"]), "entries": entries}
            _write(self.path, raw)
        return raw

    def load(self):
        with _LOCK:
            metadata = self._metadata()
            ordered = {item["name"]: item.get("enabled", True) for item in metadata.get("entries", [])}
            found = {}
            if self.skills_root.exists():
                for folder in sorted(self.skills_root.iterdir()):
                    path = folder / "SKILL.md"
                    if folder.is_dir() and path.is_file():
                        skill = skills._read_skill_file(path)
                        if skill:
                            _, body = skills.parse_frontmatter(skills.read_skill_text(path))
                            found[skill.name] = {"id": folder.name, "name": skill.name,
                                "description": skill.description, "body": body,
                                "enabled": ordered.get(folder.name, True)}
            entries = [found.pop(name) for name in ordered if name in found]
            entries.extend(found.values())
            return {"sections": _sections(metadata["sections"]), "entries": entries}

    def save(self, document):
        with _LOCK:
            if not isinstance(document, dict) or "sections" not in document:
                raise InvalidPersona("sections are required; the old core format is no longer accepted")
            current = self.load()
            sections = _sections(document["sections"])
            entries = document.get("entries", [])
            if not isinstance(entries, list):
                raise InvalidPersona("entries must be a list")
            prepared = []
            for item in entries:
                name = _name(item["name"])
                origin = _name(item.get("id") or name)
                description = _text(item.get("description"), "description")
                if not description.strip():
                    raise InvalidPersona("description is required")
                body = _text(item.get("body", ""), "body")
                enabled = item.get("enabled", True)
                if not isinstance(enabled, bool):
                    raise InvalidPersona("enabled must be a boolean")
                prepared.append((origin, name, description, body, enabled))
            if len({e[1] for e in prepared}) != len(prepared):
                raise InvalidPersona("duplicate entry name")
            for origin, name, _, _, _ in prepared:
                if origin != name and (self.skills_root / name).exists():
                    raise InvalidPersona("a persona folder with that name already exists")
            for origin, name, description, body, _ in prepared:
                old = self.skills_root / origin
                folder = self.skills_root / name
                if origin != name and old.exists():
                    old.rename(folder)
                skills.write_skill_file(folder / "SKILL.md", name=name, description=description, body=body)
            retained = {e[1] for e in prepared} | {e[0] for e in prepared}
            for old in current["entries"]:
                if old["id"] not in retained:
                    folder = (self.skills_root / _name(old["id"])).resolve()
                    if folder.is_relative_to(self.skills_root.resolve()) and folder.is_dir():
                        shutil.rmtree(folder)
            _write(self.path, {"sections": sections, "entries": [
                {"name": e[1], "enabled": e[4]} for e in prepared]})
            return self.load()

    def manage(self, args):
        with _LOCK:
            document = self.load()
            action = args["action"]
            if action == "read":
                return document
            if action in ("save_section", "save_entry"):
                field = "section" if action == "save_section" else "entry"
                values = args.get(field)
                if not isinstance(values, dict):
                    raise InvalidPersona(f"{field} is required")
                values = dict(values)
                identifier = args.get("id") or values.get("id")
                collection = document["sections" if field == "section" else "entries"]
                if identifier:
                    item = next((v for v in collection if v["id"] == identifier), None)
                    if item is None:
                        raise InvalidPersona("item not found")
                    item.update(values)
                else:
                    collection.append(values)
            elif action in ("delete_section", "delete_entry", "move_section"):
                collection = document["entries" if action == "delete_entry" else "sections"]
                index = next((i for i, v in enumerate(collection) if v["id"] == args.get("id")), None)
                if index is None:
                    raise InvalidPersona("item not found")
                item = collection.pop(index)
                if action == "move_section":
                    offset = args.get("offset")
                    if not isinstance(offset, int):
                        raise InvalidPersona("offset is required")
                    collection.insert(max(0, min(len(collection), index + offset)), item)
            else:
                raise InvalidPersona("unknown persona action")
            return self.save(document)

    def read_entry(self, name):
        for entry in self.load()["entries"]:
            if entry["name"] == name and entry["enabled"]:
                return {"content": entry["body"], "skill": name, "category": "persona"}
        raise FileNotFoundError(f"Unknown persona entry '{name}'")

    def record_request(self, content, source, model):
        value = {"content": content, "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
                 "source": source, "model": model}
        with _LOCK:
            _write(self.request_path, value)
        return value

    def snapshot(self):
        with _LOCK:
            document = self.load()
            last = json.loads(self.request_path.read_text(encoding="utf-8")) if self.request_path.exists() else None
            return {"persona": document, "preview": render_persona(document), "last_request": last}


def render_persona(document):
    lines = ["# Persona", "These sections and entry descriptions are your active persona. "
             "They shape your behavior without requiring a skill read."]
    for section in document["sections"]:
        lines.extend(["", "## " + section["title"], section["content"]])
    entries = [e for e in document["entries"] if e["enabled"]]
    if entries:
        lines.extend(["", "## Persona entries", 'Read details only when needed with read_skill(name="<name>", category="persona").'])
        lines.extend(f"- {e['name']}: {e['description']}" for e in entries)
    return "\n".join(lines) if document["sections"] or entries else ""
