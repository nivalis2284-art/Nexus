"""Tests for background jobs and optimistic-concurrency writes."""

from __future__ import annotations

import sys
import time


from nexus.tools import build


def test_background_returns_immediately():
    registry, context = build({}, config={})
    import pathlib

    context.root = pathlib.Path.cwd()
    script = "import time; time.sleep(2); print('late', flush=True)"
    command = f'"{sys.executable}" -c "{script}"'

    mark = time.monotonic()
    result = registry.execute("bash_background", {"command": command}, context)
    elapsed = time.monotonic() - mark

    assert result.ok
    assert elapsed < 1.5, f"start blocked for {elapsed:.1f}s"
    assert result.data["job_id"].startswith("job_")


def test_job_output_can_wait_for_completion():
    registry, context = build({}, config={})
    import pathlib

    context.root = pathlib.Path.cwd()
    script = "print('working', flush=True); import time; time.sleep(1); print('finished', flush=True)"
    command = f'"{sys.executable}" -c "{script}"'

    started = registry.execute("bash_background", {"command": command}, context)
    job_id = started.data["job_id"]
    cursor = started.data.get("cursor")

    done = registry.execute(
        "job_output", {"job_id": job_id, "wait_seconds": 6, "cursor": cursor}, context
    )
    assert done.ok
    assert done.data["running"] is False
    assert "finished" in done.content


def test_job_output_returns_only_new_text():
    registry, context = build({}, config={})
    import pathlib

    context.root = pathlib.Path.cwd()
    script = "import time; print('one', flush=True); time.sleep(1); print('two', flush=True)"
    command = f'"{sys.executable}" -c "{script}"'

    started = registry.execute("bash_background", {"command": command}, context)
    job_id = started.data["job_id"]
    first = registry.execute(
        "job_output", {"job_id": job_id, "wait_seconds": 5, "cursor": started.data.get("cursor")}, context
    )
    second = registry.execute(
        "job_output", {"job_id": job_id, "cursor": first.data["cursor"]}, context
    )
    # The second read must not repeat what the first already returned.
    assert "one" not in second.content


def test_job_kill_stops_a_long_process():
    registry, context = build({}, config={})
    import pathlib

    context.root = pathlib.Path.cwd()
    script = "import time; print('start', flush=True); time.sleep(30)"
    command = f'"{sys.executable}" -c "{script}"'

    started = registry.execute("bash_background", {"command": command}, context)
    job_id = started.data["job_id"]
    killed = registry.execute("job_kill", {"job_id": job_id}, context)
    assert killed.ok
    assert killed.data["stopped"] is True

    time.sleep(0.4)
    after = registry.execute("job_list", {}, context)
    assert job_id in after.content


def test_unknown_job_is_an_error():
    registry, context = build({}, config={})
    result = registry.execute("job_output", {"job_id": "job_999"}, context)
    assert not result.ok


def test_job_list_is_empty_initially():
    registry, context = build({}, config={})
    result = registry.execute("job_list", {}, context)
    assert result.ok
    assert result.data["jobs"] == []


# ---------------------------------------------------------------- sha256


def test_read_returns_a_hash(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello\n", encoding="utf-8")
    registry, context = build({}, config={}, root=tmp_path)
    result = registry.execute("read_file", {"path": "a.txt"}, context)
    assert result.ok
    assert len(result.data["sha256"]) == 64


def test_write_succeeds_with_matching_hash(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello\n", encoding="utf-8")
    registry, context = build({}, config={}, root=tmp_path)
    read = registry.execute("read_file", {"path": "a.txt"}, context)
    digest = read.data["sha256"]
    result = registry.execute(
        "write_file",
        {"path": "a.txt", "content": "changed\n", "expected_sha256": digest},
        context,
    )
    assert result.ok
    assert target.read_text(encoding="utf-8") == "changed\n"


def test_write_fails_when_file_changed_underneath(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("original\n", encoding="utf-8")
    registry, context = build({}, config={}, root=tmp_path)
    read = registry.execute("read_file", {"path": "a.txt"}, context)
    digest = read.data["sha256"]

    # Something else changes the file between read and write.
    target.write_text("someone else\n", encoding="utf-8")

    result = registry.execute(
        "write_file",
        {"path": "a.txt", "content": "mine\n", "expected_sha256": digest},
        context,
    )
    assert not result.ok
    assert "changed since it was read" in result.content
    # And the other change survived.
    assert target.read_text(encoding="utf-8") == "someone else\n"


def test_edit_honours_the_hash(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("alpha\n", encoding="utf-8")
    registry, context = build({}, config={}, root=tmp_path)
    digest = registry.execute("read_file", {"path": "a.txt"}, context).data["sha256"]
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    result = registry.execute(
        "edit_file",
        {"path": "a.txt", "old_text": "alpha", "new_text": "gamma",
         "expected_sha256": digest},
        context,
    )
    assert not result.ok
    assert "changed since it was read" in result.content


def test_write_without_hash_still_works(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("x\n", encoding="utf-8")
    registry, context = build({}, config={}, root=tmp_path)
    result = registry.execute(
        "write_file", {"path": "a.txt", "content": "y\n"}, context
    )
    assert result.ok


def test_hash_pattern_is_validated(tmp_path):
    registry, context = build({}, config={}, root=tmp_path)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = registry.execute(
        "write_file",
        {"path": "a.txt", "content": "y", "expected_sha256": "not-a-hash"},
        context,
    )
    assert not result.ok
