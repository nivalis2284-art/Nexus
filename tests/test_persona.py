import json
import pytest
from nexus.persona import PersonaStore, render_persona
from nexus.tools import build

def entry(name="voice", **changes):
    return dict(name=name, description="A candid voice.", body="  precise body\n", enabled=True, **changes)

def test_sections_file_backing_and_manual_update(tmp_path):
    store = PersonaStore(tmp_path)
    doc = store.load()
    assert [s["title"] for s in doc["sections"]] == ["核心设定", "回复原则"]
    doc["sections"][0]["content"] = "  exact core\n"
    doc["entries"] = [entry()]
    saved = store.save(doc)
    assert saved["entries"][0]["body"] == "  precise body\n"
    metadata = json.loads(store.path.read_text(encoding="utf-8"))
    assert metadata["entries"] == [{"name": "voice", "enabled": True}]
    path = store.skills_root / "voice" / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8").replace("precise body", "changed body"), encoding="utf-8")
    assert "changed body" in store.read_entry("voice")["content"]
    preview = render_persona(store.load())
    assert "  exact core\n" in preview
    assert "A candid voice." in preview and "changed body" not in preview

def test_manage_sections_rename_and_disable(tmp_path):
    store = PersonaStore(tmp_path)
    store.manage({"action": "save_section", "section": {"title": "Extra", "content": "FULL"}})
    identifier = store.load()["sections"][-1]["id"]
    store.manage({"action": "move_section", "id": identifier, "offset": -2})
    assert store.load()["sections"][0]["title"] == "Extra"
    store.manage({"action": "delete_section", "id": identifier})
    store.manage({"action": "save_entry", "entry": entry()})
    store.manage({"action": "save_entry", "id": "voice", "entry": {"name": "new-voice"}})
    assert not (store.skills_root / "voice").exists()
    assert (store.skills_root / "new-voice" / "SKILL.md").is_file()
    store.manage({"action": "save_entry", "id": "new-voice", "entry": {"enabled": False}})
    with pytest.raises(FileNotFoundError):
        store.read_entry("new-voice")
    assert "A candid voice." not in render_persona(store.load())
    store.manage({"action": "delete_entry", "id": "new-voice"})
    assert store.load()["entries"] == []

def test_legacy_nine_entries_migrate_once(tmp_path):
    old = {"core": "OLD CORE", "entries": [
        {"id": f"entry-{i}", "name": "旧名字", "summary": f"summary {i}", "body": f"BODY {i}", "enabled": i != 3}
        for i in range(9)]}
    store = PersonaStore(tmp_path)
    store.path.write_text(json.dumps(old), encoding="utf-8")
    migrated = store.load()
    assert migrated["sections"][0]["content"] == old["core"]
    assert len(migrated["entries"]) == 9
    for before, after in zip(old["entries"], migrated["entries"]):
        assert after["name"] == before["id"]
        assert after["description"] == before["summary"]
        assert after["body"] == before["body"]
        assert after["enabled"] == before["enabled"]
    assert json.loads(store.legacy_backup_path.read_text(encoding="utf-8")) == old
    path = store.skills_root / "entry-0" / "SKILL.md"
    path.write_text(path.read_text(encoding="utf-8").replace("BODY 0", "EDITED"), encoding="utf-8")
    assert store.load()["entries"][0]["body"] == "EDITED"

def test_manually_added_folder_and_snapshot(tmp_path):
    from nexus.skills import write_skill_file
    store = PersonaStore(tmp_path)
    write_skill_file(store.skills_root / "manual" / "SKILL.md", name="manual", description="Manual", body="text")
    assert store.load()["entries"][0]["enabled"]
    store.record_request("first", "chat", "model")
    store.record_request("second", "wake", "model")
    assert PersonaStore(tmp_path).snapshot()["last_request"]["content"] == "second"

def test_tool_read_category_and_edit_gate(tmp_path):
    store = PersonaStore(tmp_path)
    store.manage({"action": "save_entry", "entry": entry()})
    registry, context = build({"voice": "ordinary"}, root=tmp_path, persona_store=store, persona_writable=True)
    assert registry.execute("read_skill", {"name": "voice"}, context).content == "ordinary"
    assert "precise body" in registry.execute("read_skill", {"name": "voice", "category": "persona"}, context).content
    assert registry.execute("persona_manage", {"action": "save_section", "section": {"title": "Test", "content": "yes"}}, context).ok
    registry, _ = build(root=tmp_path, persona_store=store)
    assert registry.get("persona_manage") is None

def test_invalid_editor_save_preserves_document(tmp_path):
    store = PersonaStore(tmp_path)
    doc = store.load()
    store.save(doc)
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.save({"core": "obsolete"})
    assert store.path.read_bytes() == before

