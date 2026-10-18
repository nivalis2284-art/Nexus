"""Tests for applying a standard unified diff.

The interesting cases are all failure cases. A patch that applies is easy to
believe; a patch that half-applies is the one that costs a debugging session, so
most of these assert that the file on disk did not move.
"""

from __future__ import annotations

import difflib
import hashlib

from nexus.tools import build
from nexus.tools.patch import TOOLS


def make_registry(tmp_path):
    """A registry with apply_patch available.

    The catalog entry is owned by the integrator; until it lands, this module's
    own TOOLS list is the source of truth. Adding only when absent keeps this
    correct in both worlds.
    """
    registry, context = build({}, config={}, root=tmp_path)
    if registry.get("apply_patch") is None:
        registry.add(next(spec for spec in TOOLS if spec.name == "apply_patch"))
    return registry, context


def unified(old: str, new: str, name: str = "sample.txt", n: int = 3) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=name,
            tofile=name,
            n=n,
        )
    )


def write(target, text: str) -> None:
    """Write exactly these bytes, with no newline translation.

    ``Path.write_text`` turns ``\\n`` into ``\\r\\n`` on Windows, which would make
    the file content — and therefore its hash — platform-dependent. These tests
    assert on hashes and exact bytes, so both sides are explicit.
    """
    target.write_bytes(text.encode("utf-8"))


def contents(target) -> str:
    """The file's exact text, without universal-newline translation."""
    return target.read_bytes().decode("utf-8")


# ------------------------------------------------------------------ happy path


def test_round_trip_matches_difflib_expectation(tmp_path):
    old = "alpha\nbravo\ncharlie\n"
    new = "alpha\nBRAVO\ncharlie\n"
    target = tmp_path / "sample.txt"
    write(target, old)

    registry, context = make_registry(tmp_path)
    result = registry.execute(
        "apply_patch", {"path": "sample.txt", "patch": unified(old, new)}, context
    )

    assert result.ok, result.content
    assert contents(target) == new
    assert result.data["hunks"] == 1
    assert result.data["added"] == 1
    assert result.data["removed"] == 1
    assert result.data["sha256"] == hashlib.sha256(new.encode("utf-8")).hexdigest()


def test_multi_hunk_applies_with_shifting_offsets(tmp_path):
    """The second hunk's real position depends on how much the first added.

    Getting this wrong is the classic applier bug: every later hunk drifts by the
    running delta and the patch lands in the wrong place or fails spuriously.
    """
    old = "".join(f"line {n}\n" for n in range(1, 31))
    new = old.replace("line 3\n", "line 3\ninserted a\ninserted b\n").replace(
        "line 25\n", "LINE 25\n"
    )
    target = tmp_path / "sample.txt"
    write(target, old)

    diff = unified(old, new, n=2)
    assert diff.count("@@") == 4, "expected two hunks"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert result.data["hunks"] == 2
    assert result.data["added"] == 3
    assert result.data["removed"] == 1
    assert contents(target) == new


def test_insertion_after_last_line(tmp_path):
    """A `@@ -3,0 +4,1 @@` hunk inserts after line 3, not before it."""
    target = tmp_path / "sample.txt"
    write(target, "a\nb\nc\n")
    diff = "--- sample.txt\n+++ sample.txt\n@@ -3,0 +4,1 @@\n+d\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert contents(target) == "a\nb\nc\nd\n"


def test_no_newline_marker_is_honoured(tmp_path):
    """`\\ No newline at end of file` must be interpreted, not parsed as content."""
    target = tmp_path / "sample.txt"
    write(target, "one\ntwo")  # no trailing newline
    diff = (
        "--- sample.txt\n+++ sample.txt\n"
        "@@ -1,2 +1,2 @@\n one\n-two\n\\ No newline at end of file\n"
        "+TWO\n\\ No newline at end of file\n"
    )

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert contents(target) == "one\nTWO"


def test_git_style_headers_are_accepted(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "x\ny\n")
    diff = (
        "diff --git a/sample.txt b/sample.txt\n"
        "index 1111111..2222222 100644\n"
        "--- a/sample.txt\n+++ b/sample.txt\n"
        "@@ -1,2 +1,2 @@\n x\n-y\n+Y\n"
    )

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert contents(target) == "x\nY\n"


def test_crlf_file_keeps_its_line_endings(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1,3 +1,3 @@\n alpha\n-beta\n+BETA\n gamma\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert target.read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


def test_deleting_every_line_empties_the_file(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "only\n")
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1 +0,0 @@\n-only\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    # A shorter result must not leave the tail of the old content behind.
    assert contents(target) == ""


# ------------------------------------------------------------ trailing newline


def test_editing_the_middle_keeps_a_missing_final_newline(tmp_path):
    """A middle-of-file patch must not add a newline the file never had."""
    target = tmp_path / "sample.txt"
    write(target, "x\ny")  # no trailing newline
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1,2 +1,2 @@\n-x\n+X\n y\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert contents(target) == "X\ny"


def test_appending_a_line_terminates_the_new_last_line(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "a\nb\nc\n")
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1,3 +1,4 @@\n a\n b\n c\n+d\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert contents(target) == "a\nb\nc\nd\n"


def test_inserting_into_an_empty_file(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "")
    diff = "--- sample.txt\n+++ sample.txt\n@@ -0,0 +1,2 @@\n+first\n+second\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert contents(target) == "first\nsecond\n"


def test_mixed_line_endings_are_refused(tmp_path):
    """Silently normalising would rewrite every untouched line, not apply a diff."""
    target = tmp_path / "sample.txt"
    target.write_bytes(b"a\nb\r\nc\n")
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1,3 +1,3 @@\n a\n-b\n+B\n c\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "mixes CRLF" in result.content
    assert target.read_bytes() == b"a\nb\r\nc\n"


def test_unified_diff_generated_from_files_round_trips(tmp_path):
    """The realistic path: read two files with difflib, apply, get the second."""
    before = tmp_path / "before.txt"
    after = tmp_path / "after.txt"
    old = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    new = "def f():\n    return 10\n\n\ndef h():\n    return 2\n"
    write(before, old)
    write(after, new)

    diff = unified(old, new, name="before.txt")

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "before.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert contents(before) == contents(after)


# --------------------------------------------------------------- refusal paths


def test_context_mismatch_fails_and_leaves_the_file_alone(tmp_path):
    original = "one\ntwo\nthree\n"
    target = tmp_path / "sample.txt"
    write(target, original)
    # The context says "TWO"; the file says "two".
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1,3 +1,3 @@\n one\n-TWO\n+TWO2\n three\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "Hunk 1" in result.content
    assert "line 2" in result.content
    assert "two" in result.content
    assert contents(target) == original


def test_mismatch_in_first_hunk_writes_nothing_even_if_later_hunks_fit(tmp_path):
    """All-or-nothing: the second hunk matching must not save the first."""
    original = "a\nb\nc\nd\ne\nf\n"
    target = tmp_path / "sample.txt"
    write(target, original)
    diff = (
        "--- sample.txt\n+++ sample.txt\n"
        "@@ -1,2 +1,2 @@\n a\n-WRONG\n+B\n"
        "@@ -5,2 +5,2 @@\n e\n-f\n+F\n"
    )

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "Hunk 1" in result.content
    assert contents(target) == original


def test_path_outside_workspace_is_denied(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    write(outside, "secret\n")
    diff = "--- ../outside.txt\n+++ ../outside.txt\n@@ -1 +1 @@\n-secret\n+stolen\n"

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry, context = make_registry(workspace)
    result = registry.execute("apply_patch", {"path": "../outside.txt", "patch": diff}, context)

    assert not result.ok
    assert "outside the workspace" in result.content
    assert contents(outside) == "secret\n"


def test_expected_sha256_mismatch_is_refused(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "v1\n")
    stale = hashlib.sha256(b"v1\n").hexdigest()
    # Something else writes first.
    write(target, "v2\n")
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1 +1 @@\n-v2\n+v3\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute(
        "apply_patch",
        {"path": "sample.txt", "patch": diff, "expected_sha256": stale},
        context,
    )

    assert not result.ok
    assert "changed since it was read" in result.content
    assert contents(target) == "v2\n"


def test_expected_sha256_match_is_accepted(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "v1\n")
    digest = hashlib.sha256(b"v1\n").hexdigest()
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1 +1 @@\n-v1\n+v2\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute(
        "apply_patch",
        {"path": "sample.txt", "patch": diff, "expected_sha256": digest},
        context,
    )

    assert result.ok, result.content
    assert contents(target) == "v2\n"


def test_hunk_past_end_of_file_fails_cleanly(tmp_path):
    """Deleting more lines than exist must not write a partial result."""
    original = "one\ntwo\nthree\n"
    target = tmp_path / "sample.txt"
    write(target, original)
    diff = "--- sample.txt\n+++ sample.txt\n@@ -2,5 +2,5 @@\n two\n three\n four\n five\n six\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "only 3 line" in result.content
    assert contents(target) == original


def test_diff_for_a_different_file_is_refused(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "x\n")
    diff = "--- other.txt\n+++ other.txt\n@@ -1 +1 @@\n-x\n+y\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "other.txt" in result.content
    assert contents(target) == "x\n"


def test_multiple_file_sections_are_refused(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "x\n")
    diff = (
        "--- sample.txt\n+++ sample.txt\n@@ -1 +1 @@\n-x\n+y\n"
        "--- second.txt\n+++ second.txt\n@@ -1 +1 @@\n-a\n+b\n"
    )

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "one file per call" in result.content
    assert contents(target) == "x\n"


def test_adjacent_hunks_are_allowed(tmp_path):
    """Two hunks touching end-to-start are legal; only real overlap is not."""
    target = tmp_path / "sample.txt"
    write(target, "a\nb\nc\nd\n")
    diff = (
        "--- sample.txt\n+++ sample.txt\n"
        "@@ -1 +1 @@\n-a\n+A\n"
        "@@ -3 +3 @@\n-c\n+C\n"
    )

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert result.ok, result.content
    assert contents(target) == "A\nb\nC\nd\n"


def test_overlapping_hunks_are_refused(tmp_path):
    original = b"a\nb\nc\nd\n"
    target = tmp_path / "sample.txt"
    target.write_bytes(original)
    diff = (
        "--- sample.txt\n+++ sample.txt\n"
        "@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
        "@@ -2,2 +2,2 @@\n b\n-c\n+C\n"
    )

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "overlap" in result.content
    assert target.read_bytes() == original


def test_out_of_order_hunks_are_refused(tmp_path):
    original = b"a\nb\nc\nd\ne\n"
    target = tmp_path / "sample.txt"
    target.write_bytes(original)
    diff = (
        "--- sample.txt\n+++ sample.txt\n"
        "@@ -4,2 +4,2 @@\n d\n-e\n+E\n"
        "@@ -1,2 +1,2 @@\n a\n-b\n+B\n"
    )

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "overlap" in result.content
    assert target.read_bytes() == original


def test_truncated_hunk_is_reported_not_guessed(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "a\nb\nc\n")
    # The header promises 3 lines each side; the body stops after one.
    diff = "--- sample.txt\n+++ sample.txt\n@@ -1,3 +1,3 @@\n a\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "sample.txt", "patch": diff}, context)

    assert not result.ok
    assert "truncated" in result.content
    assert contents(target) == "a\nb\nc\n"


def test_garbage_is_rejected(tmp_path):
    target = tmp_path / "sample.txt"
    write(target, "x\n")

    registry, context = make_registry(tmp_path)
    result = registry.execute(
        "apply_patch", {"path": "sample.txt", "patch": "just some prose\n"}, context
    )

    assert not result.ok
    assert contents(target) == "x\n"


def test_missing_file_is_not_created(tmp_path):
    diff = "--- new.txt\n+++ new.txt\n@@ -1 +1 @@\n-a\n+b\n"

    registry, context = make_registry(tmp_path)
    result = registry.execute("apply_patch", {"path": "new.txt", "patch": diff}, context)

    assert not result.ok
    assert not (tmp_path / "new.txt").exists()
