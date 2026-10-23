"""Skill file writing and editor source precedence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus import skills
from nexus.skill_management import SkillManager


def _manager(tmp_path: Path) -> SkillManager:
    return SkillManager(tmp_path / "home", tmp_path / "workspace", tmp_path / "builtin")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _values(source: str, path: str, name: str, description: str, body: str) -> dict[str, str]:
    return {"source": source, "path": path, "name": name,
            "description": description, "body": body}


def test_write_preserves_unknown_frontmatter_and_body(tmp_path):
    path = tmp_path / "SKILL.md"
    _write(path, (
        "---\n"
        "name: old\n"
        "description: Old summary.\n"
        "when: Asked to check logs.\n"
        "custom:\n"
        "  owner: team\n"
        "  steps:\n"
        "    - one\n"
        "    - two\n"
        "---\n"
        "First line\n\n  indented\n"
    ))

    skills.write_skill_file(
        path, name="new", description='Quotes "and" \\ slashes', body="Exact body\n"
    )

    text = path.read_text(encoding="utf-8")
    assert text.endswith("---\nExact body\n")
    assert "when: Asked to check logs.\n" in text
    assert "custom:\n  owner: team\n  steps:\n    - one\n    - two\n" in text
    meta, body = skills.parse_frontmatter(text)
    assert meta["name"] == "new"
    assert meta["description"] == 'Quotes "and" \\ slashes'
    assert meta["when"] == "Asked to check logs."
    assert body == "Exact body\n"


def test_write_creates_a_folder_skill(tmp_path):
    path = tmp_path / "new" / "SKILL.md"
    skills.write_skill_file(
        path, name="new", description="A skill.", body="Use it.\n"
    )
    found = skills.discover([tmp_path]).get("new")
    assert found is not None
    assert found.description == "A skill."
    assert found.body == "Use it."


def test_editor_listing_has_source_precedence_and_paths(tmp_path):
    manager = _manager(tmp_path)
    project = tmp_path / "workspace" / ".nexus" / "skills"
    personal = tmp_path / "home" / "skills"
    builtin = tmp_path / "builtin"
    _write(project / "shared" / "SKILL.md",
           "---\nname: shared\ndescription: Project.\n---\nPROJECT\n")
    _write(personal / "shared" / "SKILL.md",
           "---\nname: shared\ndescription: Personal.\n---\nPERSONAL\n")
    _write(personal / "flat.md",
           "---\nname: flat\ndescription: Flat.\n---\nFLAT\n")
    _write(builtin / "builtin" / "SKILL.md",
           "---\nname: builtin\ndescription: Built in.\n---\nBUILTIN\n")

    rows = manager.list()["skills"]
    by_path = {(row["source"], row["path"]): row for row in rows}
    assert by_path[("project", "shared/SKILL.md")]["effective"] is True
    assert by_path[("personal", "shared/SKILL.md")]["effective"] is False
    assert by_path[("personal", "flat.md")]["effective"] is True
    assert by_path[("personal", "flat.md")]["read_only"] is False
    assert by_path[("builtin", "builtin/SKILL.md")]["effective"] is True
    assert by_path[("builtin", "builtin/SKILL.md")]["read_only"] is True
    assert by_path[("builtin", "builtin/SKILL.md")]["body"] == "BUILTIN\n"


def test_save_new_skill_defaults_to_personal_and_returns_listing(tmp_path):
    manager = _manager(tmp_path)
    result = manager.save({
        "name": "new-skill", "description": "Created here.", "body": "Do it.\n",
    })
    assert result["skills"][0]["path"] == "new-skill/SKILL.md"
    assert result["skills"][0]["source"] == "personal"
    assert (tmp_path / "home" / "skills" / "new-skill" / "SKILL.md").is_file()


def test_save_edits_flat_without_creating_a_folder(tmp_path):
    manager = _manager(tmp_path)
    personal = tmp_path / "home" / "skills"
    _write(personal / "flat.md",
           "---\nname: flat\ndescription: Flat.\nwhen: Keep this.\n---\nOLD\n")
    manager.save(_values("personal", "flat.md", "flat", "Changed.", "NEW\n"))

    assert not (personal / "flat").exists()
    text = (personal / "flat.md").read_text(encoding="utf-8")
    assert "when: Keep this.\n" in text
    assert "name: \"flat\"" in text
    assert text.endswith("NEW\n")


def test_save_renames_a_folder_when_the_name_changes(tmp_path):
    manager = _manager(tmp_path)
    personal = tmp_path / "home" / "skills"
    _write(personal / "old" / "SKILL.md",
           "---\nname: old\ndescription: Old.\nkeep: yes\n---\nOLD\n")
    result = manager.save(_values("personal", "old/SKILL.md", "renamed", "New.", "BODY\n"))

    assert not (personal / "old").exists()
    text = (personal / "renamed" / "SKILL.md").read_text(encoding="utf-8")
    assert "keep: yes\n" in text
    assert result["skills"][0]["path"] == "renamed/SKILL.md"


def test_save_rejects_builtin(tmp_path):
    manager = _manager(tmp_path)
    builtin = tmp_path / "builtin"
    _write(builtin / "fixed" / "SKILL.md",
           "---\nname: fixed\ndescription: Fixed.\n---\nBODY\n")
    with pytest.raises(ValueError, match="read-only"):
        manager.save(_values("builtin", "fixed/SKILL.md", "fixed", "Edit.", "BODY"))


def test_delete_removes_whole_folder_and_keeps_flat_siblings(tmp_path):
    manager = _manager(tmp_path)
    personal = tmp_path / "home" / "skills"
    _write(personal / "gone" / "SKILL.md",
           "---\nname: gone\ndescription: Gone.\n---\nBODY\n")
    _write(personal / "gone" / "notes.md", "extra")
    _write(personal / "flat.md",
           "---\nname: flat\ndescription: Flat.\n---\nBODY\n")

    result = manager.delete({"source": "personal", "path": "gone/SKILL.md"})
    assert not (personal / "gone").exists()
    assert (personal / "flat.md").is_file()
    assert [row["path"] for row in result["skills"]] == ["flat.md"]


def test_delete_rejects_builtin_and_escaped_paths(tmp_path):
    manager = _manager(tmp_path)
    builtin = tmp_path / "builtin"
    _write(builtin / "fixed" / "SKILL.md",
           "---\nname: fixed\ndescription: Fixed.\n---\nBODY\n")
    _write(tmp_path / "home" / "skills" / "ok" / "SKILL.md",
           "---\nname: ok\ndescription: Ok.\n---\nBODY\n")

    with pytest.raises(ValueError, match="read-only"):
        manager.delete({"source": "builtin", "path": "fixed/SKILL.md"})
    with pytest.raises(ValueError):
        manager.delete({"source": "personal", "path": "../ok/SKILL.md"})
    assert (builtin / "fixed" / "SKILL.md").is_file()
    assert (tmp_path / "home" / "skills" / "ok" / "SKILL.md").is_file()


def test_save_rejects_unknown_and_duplicate_paths(tmp_path):
    manager = _manager(tmp_path)
    personal = tmp_path / "home" / "skills"
    _write(personal / "one" / "SKILL.md",
           "---\nname: one\ndescription: One.\n---\nBODY\n")
    _write(personal / "two" / "SKILL.md",
           "---\nname: two\ndescription: Two.\n---\nBODY\n")

    with pytest.raises(ValueError, match="not found"):
        manager.save(_values("personal", "missing/SKILL.md", "one", "One.", "BODY"))
    with pytest.raises(ValueError, match="already exists"):
        manager.save(_values("personal", "one/SKILL.md", "two", "Two.", "BODY"))


def test_generated_scalars_are_valid_json_quoted_strings(tmp_path):
    manager = _manager(tmp_path)
    description = 'Line one: "quoted" \\ backslash\nsecond line'
    manager.save({"name": "quoted", "description": description, "body": "B"})
    text = (tmp_path / "home" / "skills" / "quoted" / "SKILL.md").read_text(encoding="utf-8")
    line = next(line for line in text.splitlines() if line.startswith("description:"))
    assert json.loads(line.partition(":")[2].strip()) == description
    assert manager.list()["skills"][0]["description"] == description


def test_gbk_skill_listing_and_save_converts_to_utf8(tmp_path):
    manager = _manager(tmp_path)
    path = tmp_path / "home" / "skills" / "gbk-skill" / "SKILL.md"
    path.parent.mkdir(parents=True)
    source = (
        "---\n"
        "name: gbk-skill\n"
        "description: 旧中文摘要\n"
        "when: 需要时读取\n"
        "custom:\n"
        "  owner: 团队\n"
        "---\n"
        "中文正文。\n"
    ).encode("gbk")
    path.write_bytes(source)

    row = manager.list()["skills"][0]
    assert row["description"] == "旧中文摘要"
    assert row["body"] == "中文正文。\n"
    assert path.read_bytes() == source

    manager.save(_values("personal", "gbk-skill/SKILL.md",
                         "gbk-skill", "新中文摘要", "保存正文。\n"))
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    metadata, body = skills.parse_frontmatter(text)
    assert body == "保存正文。\n"
    assert metadata["description"] == "新中文摘要"
    assert metadata["when"] == "需要时读取"
    assert "custom:\n  owner: 团队\n" in text
    assert source not in path.read_bytes()


def test_undecodable_skill_reports_error_without_overwriting(tmp_path):
    manager = _manager(tmp_path)
    path = tmp_path / "home" / "skills" / "broken" / "SKILL.md"
    path.parent.mkdir(parents=True)
    source = b"---\nname: broken\ndescription: Broken\n---\nvalid ascii\xff"
    path.write_bytes(source)

    with pytest.raises(skills.SkillEncodingError, match="UTF-8.*GBK"):
        manager.list()
    with pytest.raises(skills.SkillEncodingError, match="UTF-8.*GBK"):
        skills.write_skill_file(path, name="broken", description="Changed", body="body")
    assert path.read_bytes() == source
