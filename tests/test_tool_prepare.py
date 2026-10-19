"""Intent creation follows validation and precedes any tool side effect."""

from __future__ import annotations

from nexus.tools import build


def test_prepare_rejects_bad_arguments_without_running_handler(tmp_path):
    registry, context = build(root=tmp_path)
    prepared = registry.prepare("write_file", {"path": "x", "content": 4}, context)
    assert not hasattr(prepared, "spec")
    assert prepared.error_code == "invalid_arguments"
    assert not (tmp_path / "x").exists()


def test_execute_prepared_only_runs_validated_arguments(tmp_path):
    registry, context = build(root=tmp_path)
    prepared = registry.prepare("write_file", {"path": "x", "content": "ok"}, context)
    assert prepared.spec.name == "write_file"
    assert not (tmp_path / "x").exists()
    result = registry.execute_prepared(prepared, context)
    assert result.ok
    assert (tmp_path / "x").read_text() == "ok"


def test_read_hash_covers_whole_large_file(tmp_path):
    import hashlib
    from nexus.tools.files import MAX_READ_BYTES

    target = tmp_path / "large.txt"
    body = b"a" * (MAX_READ_BYTES + 20)
    target.write_bytes(body)
    registry, context = build(root=tmp_path)
    result = registry.execute("read_file", {"path": "large.txt"}, context)
    assert result.ok
    assert result.data["sha256"] == hashlib.sha256(body).hexdigest()
    assert result.data["sha256"] != hashlib.sha256(body[:MAX_READ_BYTES]).hexdigest()
