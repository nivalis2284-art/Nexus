"""Regression: shortening a file must not leave the old tail behind.

Found while integrating the patch tool. ``open_file(path, "write")`` opened the
existing file and then wrapped the descriptor with ``"wb"`` — but on an already
open descriptor that only sets the mode, it does not truncate. Writing a shorter
body left the previous bytes after the new content:

    "aaaa\\nbbbb\\n"  --write "b\\n"-->  "b\\naa\\nbbbb\\n"

That is silent file corruption, and it affected ``write_file`` and ``edit_file``,
not just the new tool.
"""

from __future__ import annotations

import pytest

from nexus.tools import build
from nexus.tools._paths import open_file


def test_open_file_write_truncates(tmp_path):
    target = tmp_path / "t.txt"
    target.write_bytes(b"aaaa\nbbbb\n")
    with open_file(target, "write") as handle:
        handle.write(b"b\n")
    assert target.read_bytes() == b"b\n"


def test_write_file_shrinking_a_file_leaves_no_tail(tmp_path):
    target = tmp_path / "t.txt"
    target.write_bytes(b"aaaa\nbbbb\n")
    registry, context = build({}, config={}, root=tmp_path)
    result = registry.execute("write_file", {"path": "t.txt", "content": "b\n"}, context)
    assert result.ok
    assert target.read_bytes() == b"b\n"


def test_edit_file_shrinking_a_file_leaves_no_tail(tmp_path):
    target = tmp_path / "t.txt"
    target.write_bytes(b"first line\nsecond line\nthird line\n")
    registry, context = build({}, config={}, root=tmp_path)
    result = registry.execute(
        "edit_file",
        {
            "path": "t.txt",
            "old_text": "first line\nsecond line\nthird line\n",
            "new_text": "short\n",
        },
        context,
    )
    assert result.ok
    assert target.read_bytes() == b"short\n"


def test_apply_patch_shrinking_leaves_no_tail(tmp_path):
    """The tool that surfaced the bug must also be covered."""
    import difflib

    target = tmp_path / "t.txt"
    before = "one\ntwo\nthree\nfour\n"
    after = "one\nFOUR\n"
    target.write_bytes(before.encode())

    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="t.txt",
            tofile="t.txt",
        )
    )
    registry, context = build({}, config={}, root=tmp_path)
    result = registry.execute("apply_patch", {"path": "t.txt", "patch": diff}, context)
    if not result.ok:
        # The patch tool is allowed to refuse an awkward diff; what must never
        # happen is a partial write.
        assert target.read_bytes() == before.encode(), "failed patch corrupted the file"
        return
    assert target.read_bytes() == after.encode()


def test_writing_longer_content_still_works(tmp_path):
    target = tmp_path / "t.txt"
    target.write_bytes(b"a\n")
    registry, context = build({}, config={}, root=tmp_path)
    registry.execute("write_file", {"path": "t.txt", "content": "a\nbb\ncc\n"}, context)
    assert target.read_bytes() == b"a\nbb\ncc\n"


def test_repeated_writes_do_not_accumulate(tmp_path):
    target = tmp_path / "t.txt"
    # write_file overwrites an existing file; creating one is the caller's job,
    # which is the behaviour this test has to work with.
    target.write_bytes(b"seed\n")
    registry, context = build({}, config={}, root=tmp_path)
    for body in ("aaaa\n", "bb\n", "c\n"):
        result = registry.execute("write_file", {"path": "t.txt", "content": body}, context)
        assert result.ok
        assert target.read_bytes() == body.encode()


def test_empty_write_truly_empties_the_file(tmp_path):
    """Writing nothing is the extreme case of a shorter write."""
    target = tmp_path / "t.txt"
    target.write_bytes(b"content that must go\n")
    registry, context = build({}, config={}, root=tmp_path)
    registry.execute("write_file", {"path": "t.txt", "content": ""}, context)
    assert target.read_bytes() == b""
