"""Write a checksum manifest for the experiment tree.

The point is not to keep a copy. It is to make a loss audible: if a file
vanishes, the manifest still names it and says what it was.

Usage:
    python manifest.py            # rewrite MANIFEST.json next to this script's parent
    python manifest.py --check    # verify current files against the manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "MANIFEST.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entries() -> list[dict]:
    result = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path == OUT or ".bak" in path.name:
            continue
        relative = path.relative_to(ROOT).as_posix()
        data = path.read_bytes()
        item = {"path": relative, "bytes": len(data), "sha256": digest(path)}
        if path.suffix == ".jsonl":
            item["lines"] = len([line for line in data.decode("utf-8").splitlines() if line.strip()])
        result.append(item)
    return result


def write() -> None:
    files = entries()
    # Regenerating must not silently erase the record of a file that is already gone.
    if OUT.exists():
        previous = json.loads(OUT.read_text(encoding="utf-8"))["files"]
        present = {item["path"] for item in files}
        for item in previous:
            if item["path"] not in present:
                print(f"GONE since last run: {item['path']} (still listed below)")
                files.append({**item, "missing": True})
    manifest = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "root": "docs/参考/archive/2026-10-25-ai-dialogue",
        "note": "Checksums of the experiment tree. A missing file here means it was lost outside git.",
        "files": files,
    }
    OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT.parents[1])} with {len(files)} files")


def check() -> int:
    if not OUT.exists():
        print("no manifest")
        return 2
    recorded = {item["path"]: item for item in json.loads(OUT.read_text(encoding="utf-8"))["files"]}
    current = {item["path"]: item for item in entries()}
    lost = 0
    for path, item in recorded.items():
        if path not in current:
            print(f"MISSING  {path}")
            lost += 1
        elif current[path]["sha256"] != item["sha256"]:
            # Growth is expected for a live transcript; it is reported, not failed.
            print(f"CHANGED  {path}")
    for path in current.keys() - recorded.keys():
        print(f"NEW      {path}")
    print(f"{lost} lost" if lost else "no loss")
    return 1 if lost else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        raise SystemExit(check())
    write()
