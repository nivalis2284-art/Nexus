"""One-time local snapshot when replacing the original autonomy prototype."""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path


def archive_original(home: Path, tasks) -> None:
    target = home / "archive" / "autonomy-original"
    marker = target / "snapshot.json"
    if marker.exists():
        return
    with sqlite3.connect(home / "tasks.sqlite3") as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='autonomy_meta'").fetchone()
        if not exists:
            return
        target.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target / "tasks.sqlite3") as backup:
            db.backup(backup)
        actions = [json.loads(row[0]) for row in db.execute(
            "SELECT data FROM autonomy_records WHERE kind='action'")]
        observer = home / "autonomy-observer"
        if observer.exists() and not (target / "autonomy-observer").exists():
            shutil.copytree(observer, target / "autonomy-observer")
        sessions = home / "sessions"
        if sessions.exists() and not (target / "sessions").exists():
            shutil.copytree(sessions, target / "sessions")
        paused = []
        for action in actions:
            task_id = action.get("task_id")
            if not task_id:
                continue
            try:
                task = tasks.get(task_id)
            except ValueError:
                continue
            tasks.clear_schedule(task_id)
            if task.state not in ("completed", "cancelled", "paused"):
                tasks.command(task_id, "paused", request_id="archive-original-" + task_id)
                paused.append(task_id)
    marker.write_text(json.dumps({"source_commit": "2d99159", "paused_tasks": paused,
        "description": "Original autonomy data, preserved before switching to automation."},
        ensure_ascii=False, indent=2), encoding="utf-8")
