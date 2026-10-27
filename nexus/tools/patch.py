"""Apply a unified diff to one file.

Why this exists: ``edit_file`` needs an exact, unique block of text. A model that
already knows the intended change as a diff (because it generated one, or was
handed one by a reviewer) would otherwise have to translate it back into
``old_text``/``new_text`` by hand, which is exactly where off-by-one mistakes
happen. Accepting the diff directly removes that translation step.

The standard library parses and produces diffs but cannot apply them:
``difflib`` exposes ``unified_diff``, ``context_diff``, ``ndiff`` and
``restore``, and ``restore`` only understands ``Differ``/``ndiff`` deltas -- not
unified hunks. So the applier is here, and it is deliberately strict:

* every context and removal line must match the file, or nothing is written;
* a hunk that would run past the end of the file is an error, never a partial
  edit;
* the diff may name a file, but only the file being patched.

All-or-nothing matters more than convenience. A half-applied patch leaves a file
that matches no intention at all, and the caller cannot tell which half landed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ._paths import atomic_write_file, open_file, reject_link
from .files import resolve
from .registry import ToolSpec

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
# Lines git puts between sections; they carry no content for a single file.
META_PREFIXES = (
    "diff ",
    "index ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "dissimilarity index ",
    "new file mode ",
    "deleted file mode ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "Binary files ",
    "GIT binary patch",
)
NO_NEWLINE = "\\ No newline at end of file"


class PatchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    patch: str = Field(min_length=1, description="Unified diff text for this one file.")
    expected_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{64}$",
        description="If given, the operation fails unless the file still has this hash.",
    )


@dataclass
class _Hunk:
    """One ``@@`` block: header numbers plus its body lines."""

    index: int
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    body: list[tuple[str, str]] = field(default_factory=list)
    # Set when the diff says the file's last line had no trailing newline.
    old_no_newline: bool = False
    new_no_newline: bool = False

    @property
    def old_lines(self) -> int:
        """Lines this hunk consumes from the old file: context plus removals."""
        return sum(1 for op, _ in self.body if op in " -")

    @property
    def new_lines(self) -> int:
        """Lines it contributes to the new file: context plus additions."""
        return sum(1 for op, _ in self.body if op in " +")

    def expected_position(self) -> int:
        """0-based index where this hunk starts, before any offset.

        A pure insertion (``old_count == 0``) means "after line ``old_start``",
        so its insertion point is ``old_start`` rather than ``old_start - 1``.
        Reading it as 1-based would shift every append by one line.
        """
        return self.old_start if self.old_count == 0 else self.old_start - 1


def _file_header_path(line: str, marker: str) -> str:
    """Extract the path from a ``---``/``+++`` line, dropping timestamps."""
    rest = line[len(marker):].strip()
    if "\t" in rest:
        rest = rest.split("\t", 1)[0]
    # Timestamps are also sometimes separated by a plain space. A quoted path
    # may legally contain spaces, so it is left alone.
    if " " in rest and not rest.startswith('"'):
        rest = rest.split(" ", 1)[0]
    return rest.strip()


def _normalise(path: str) -> str:
    text = path.strip().strip('"').replace("\\", "/")
    for prefix in ("a/", "b/", "i/", "w/"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text


def _same_file(header: str, target: Path, raw: str) -> bool:
    """A diff header matches if it names the target by suffix.

    Git writes ``a/nexus/tools/patch.py`` while a caller passes the same path
    relative to the workspace (or just ``patch.py``); comparing suffixes accepts
    all of those without letting an unrelated file name through.
    """
    named = _normalise(header)
    if not named or named == "/dev/null":
        return False
    candidates = {_normalise(raw), target.name}
    return any(named == candidate or named.endswith("/" + candidate) for candidate in candidates)


def _parse(patch: str, target: Path, raw: str) -> list[_Hunk]:
    """Parse a single-file unified diff strictly.

    Hunks are consumed by the line counts in their header rather than by
    peeking at prefixes. A removal line may legitimately begin with ``-- ``,
    which makes ``--- foo`` indistinguishable from a file header; the counts are
    the only unambiguous source of truth.
    """
    lines = patch.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()

    hunks: list[_Hunk] = []
    headers: list[str] = []
    sections = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            sections += 1
            if sections > 1:
                raise ValueError(
                    "The diff contains more than one file section. apply_patch "
                    "handles one file per call."
                )
            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                raise ValueError(
                    f"Malformed diff: the '---' header on line {index + 1} is not "
                    "followed by a '+++' header."
                )
            headers.append(_file_header_path(line, "---"))
            headers.append(_file_header_path(lines[index + 1], "+++"))
            index += 2
            continue
        if line.startswith("@@"):
            match = HUNK_HEADER.match(line)
            if match is None:
                raise ValueError(f"Malformed hunk header on line {index + 1}: {line!r}")
            hunk = _Hunk(
                index=len(hunks) + 1,
                old_start=int(match.group(1)),
                old_count=int(match.group(2) or 1),
                new_start=int(match.group(3)),
                new_count=int(match.group(4) or 1),
                header=line,
            )
            index += 1
            seen_old = seen_new = 0
            while seen_old < hunk.old_count or seen_new < hunk.new_count:
                if index >= len(lines):
                    raise ValueError(
                        f"Hunk {hunk.index} is truncated: its header promises "
                        f"-{hunk.old_count}/+{hunk.new_count} lines but the diff ends "
                        f"after -{seen_old}/+{seen_new}."
                    )
                body_line = lines[index]
                if body_line.startswith("@@"):
                    raise ValueError(
                        f"Hunk {hunk.index} is truncated: its header promises "
                        f"-{hunk.old_count}/+{hunk.new_count} lines but the body stops "
                        f"after -{seen_old}/+{seen_new}, at line {index + 1}."
                    )
                op = body_line[0] if body_line else " "
                text = body_line[1:] if body_line else ""
                if op not in " -+":
                    raise ValueError(
                        f"Unexpected line {index + 1} inside hunk {hunk.index}: "
                        f"{body_line[:80]!r}. Hunk body lines must start with "
                        "' ', '-' or '+'."
                    )
                hunk.body.append((op, text))
                if op in " -":
                    seen_old += 1
                if op in " +":
                    seen_new += 1
                index += 1
                # "\ No newline at end of file" qualifies the preceding line.
                if index < len(lines) and lines[index].startswith(NO_NEWLINE):
                    if op in " -":
                        hunk.old_no_newline = True
                    if op in " +":
                        hunk.new_no_newline = True
                    index += 1
            hunks.append(hunk)
            continue
        if line.startswith(META_PREFIXES) or line.startswith(NO_NEWLINE):
            index += 1
            continue
        if not line.strip():
            index += 1
            continue
        raise ValueError(
            f"Unrecognised line {index + 1} outside any hunk: {line[:80]!r}. "
            "Expected '---', '+++', '@@' or an empty line. If the diff was copied "
            "from a transcript, check that no lines were lost."
        )

    if not hunks:
        raise ValueError("The diff contains no @@ hunks, so there is nothing to apply.")
    if any(not name or name == "/dev/null" for name in headers):
        raise ValueError(
            "The diff creates or deletes a file (its header is /dev/null). "
            "Use write_file to create a file; apply_patch only edits existing files."
        )
    for name in headers:
        if not _same_file(name, target, raw):
            raise ValueError(
                f"The diff is for {name!r} but path is {raw!r}. apply_patch "
                "refuses to apply a diff to a different file."
            )
    return hunks


def _split(text: str) -> tuple[list[str], list[str], str]:
    """Keep each line's terminator so a patch never normalizes untouched lines."""
    if not text:
        return [], [], "\n"
    parts = text.split("\n")
    lines, endings = [], []
    for part in parts[:-1]:
        ending = "\r\n" if part.endswith("\r") else "\n"
        lines.append(part[:-1] if ending == "\r\n" else part)
        endings.append(ending)
    if parts[-1]:
        lines.append(parts[-1])
        endings.append("")
    return lines, endings, next((ending for ending in endings if ending), "\n")


def _final_newline(
    hunks: list[_Hunk], patched: list[str], current: bool, old_lines: int, removed_tail: bool
) -> bool:
    """Decide whether the patched file ends with a newline.

    A unified diff only speaks about the final newline through an explicit
    ``\\ No newline at end of file`` marker, which many hand-written diffs omit.
    So the rule falls back to who owns the new last line:

    1. An explicit marker on the new side is authoritative.
    2. If the old file's last line was removed, the new last line came from the
       patch, and patch lines are newline-terminated.
    3. An empty original file likewise leaves the patch in charge.
    4. Otherwise the file's own convention is preserved, so editing the middle
       of a file never adds or drops a trailing newline.
    """
    if any(hunk.new_no_newline for hunk in hunks):
        return False
    if not patched:
        return False
    if old_lines == 0 or removed_tail:
        return True
    return current


def _apply(
    hunks: list[_Hunk], lines: list[str], endings: list[str], newline: str,
) -> tuple[list[str], list[str], int, int, bool]:
    """Return the patched lines. Raises before any caller writes anything.

    The last flag reports whether the original file's final line was removed or
    replaced, which ``_final_newline`` needs to judge the trailing newline.
    """
    result = list(lines)
    endings = list(endings)
    offset = 0
    previous_end = 0
    added = removed = 0
    removed_tail = False
    for hunk in hunks:
        if hunk.old_count and hunk.old_start <= previous_end:
            raise ValueError(
                f"Hunk {hunk.index} starts at line {hunk.old_start}, which overlaps "
                f"the previous hunk (it ends at line {previous_end}). Hunks must not "
                "overlap."
            )
        if hunk.old_lines != hunk.old_count or hunk.new_lines != hunk.new_count:
            raise ValueError(
                f"Hunk {hunk.index} header says -{hunk.old_count}/+{hunk.new_count} "
                f"but its body accounts for -{hunk.old_lines}/+{hunk.new_lines} "
                f"(context counts on both sides). The header and body disagree, so "
                "the diff is malformed or was truncated."
            )

        start = hunk.expected_position() + offset
        if start < 0:
            raise ValueError(f"Hunk {hunk.index} resolves to a negative line number.")
        if start + hunk.old_count > len(result):
            what = (
                f"inserts after line {hunk.old_start}"
                if hunk.old_count == 0
                else f"needs {hunk.old_count} line(s) from line {hunk.old_start}"
            )
            raise ValueError(
                f"Hunk {hunk.index} ({hunk.header}) {what}, but the file has only "
                f"{len(result)} line(s). It would run past the end of the file, so "
                "nothing was written."
            )

        rebuilt = result[:start]
        rebuilt_endings = endings[:start]
        local_newline = next((e for e in endings[start:] if e), newline)
        cursor = start
        old_seen = 0
        for op, text in hunk.body:
            if op in " -":
                actual = result[cursor]
                if actual != text:
                    role = "context" if op == " " else "removal"
                    raise ValueError(
                        f"Hunk {hunk.index} does not match at file line {cursor + 1} "
                        f"({role} line expected):\n"
                        f"  expected: {text!r}\n"
                        f"  found:    {actual!r}\n"
                        "The file may have changed since the diff was made, or the "
                        "diff may have the wrong line numbers. Nothing was written; "
                        "re-read the file and regenerate the diff."
                    )
                # The original file's final line was removed or replaced, so the
                # patch now owns the tail and its newline convention.
                if op == "-" and hunk.old_start + old_seen >= len(lines):
                    removed_tail = True
                old_seen += 1
                if op == " ":
                    rebuilt.append(text)
                    rebuilt_endings.append(endings[cursor])
                else:
                    removed += 1
                local_newline = endings[cursor] or local_newline
                cursor += 1
            else:
                rebuilt.append(text)
                rebuilt_endings.append(local_newline)
                added += 1
        rebuilt.extend(result[cursor:])
        rebuilt_endings.extend(endings[cursor:])

        offset += hunk.new_count - hunk.old_count
        previous_end = hunk.old_start + hunk.old_count - 1
        result = rebuilt
        endings = rebuilt_endings
    return result, endings, added, removed, removed_tail


def _check_expected(path: Path, expected: str | None) -> None:
    """Optimistic concurrency, shared in spirit with write_file/edit_file."""
    if not expected:
        return
    with open_file(path, "read") as handle:
        current = hashlib.sha256(handle.read()).hexdigest()
    if current != expected.lower():
        raise ValueError(
            f"{path} changed since it was read (expected {expected[:12]}…, "
            f"found {current[:12]}…). Read it again before applying the patch."
        )


def apply_patch(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    path = resolve(context, args["path"])
    reject_link(path.lstat())
    if not path.is_file():
        raise FileNotFoundError(str(path))
    _check_expected(path, args.get("expected_sha256"))

    hunks = _parse(args["patch"], path, args["path"])

    with open_file(path, "read") as handle:
        raw = handle.read()
    try:
        original = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"{path} is not valid UTF-8 ({error}). apply_patch only edits UTF-8 text."
        ) from error

    lines, endings, newline = _split(original)

    patched, patched_endings, added, removed, removed_tail = _apply(hunks, lines, endings, newline)
    ends_with_newline = _final_newline(
        hunks, patched, bool(endings and endings[-1]), len(lines), removed_tail
    )
    if patched:
        patched_endings[-1] = (patched_endings[-1] or newline) if ends_with_newline else ""
    text = "".join(
        line + (ending or (newline if index < len(patched) - 1 else ""))
        for index, (line, ending) in enumerate(zip(patched, patched_endings))
    )
    body = text.encode("utf-8")

    changed = body != raw
    if changed:
        # open_file opens a write handle without truncating, so a shorter result
        # would leave the tail of the old file behind. Truncate explicitly.
        with atomic_write_file(path) as handle:
            handle.write(body)

    return {
        "content": (
            f"Applied {len(hunks)} hunk{'s' if len(hunks) != 1 else ''} to "
            f"{path.relative_to(context.root).as_posix()} "
            f"(+{added}/-{removed} lines, {len(patched)} lines now)"
            if changed
            else f"{path.name} already matches the patch; nothing changed."
        ),
        "path": str(path),
        "sha256": hashlib.sha256(body).hexdigest(),
        "hunks": len(hunks),
        "added": added,
        "removed": removed,
        "lines": len(patched),
        "changed": changed,
    }


TOOLS = [
    ToolSpec(
        "apply_patch",
        "Apply a standard unified diff (@@ hunks) to one file. Every hunk must "
        "match exactly; if any hunk does not, nothing is written.",
        PatchArgs, apply_patch,
    ),
]
