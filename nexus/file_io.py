"""Atomic replacement for local configuration and session files."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path


def replace_text(path: Path, text: str, *, private: bool = False) -> None:
    # Each writer owns its temporary file. Windows readers can briefly prevent
    # replacement of the destination; retry only that sharing/access failure.
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if private:
                os.chmod(temporary, 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(6):
            try:
                temporary.replace(path)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in (5, 32) or attempt == 5:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temporary.unlink(missing_ok=True)
