"""Atomic settings save used by the HTTP settings and workspace routes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..instructions import INSTRUCTIONS_NAME, save_instructions
from ..task.models import TaskConflict


def save_settings(
    state: dict[str, Any],
    value: Any,
    busy,
    workspace_effect: Callable[[Path], None] | None = None,
):
    """Validate first, then save preferences, instructions and workspace.

    ``workspace_effect`` receives the resolved path after the disk writes and
    is responsible only for rebuilding live MCP and skill state.
    """
    if not isinstance(value, dict):
        raise TypeError("settings must be an object")
    store = state["store"]
    current = store.load()
    instructions = value.get("instructions")
    if "instructions" in value and not isinstance(instructions, str):
        raise ValueError("instructions must be text")
    permission = value.get("permission")
    if permission is not None and permission not in ("ask", "yolo"):
        raise ValueError("permission must be ask or yolo")
    max_steps = value.get("max_steps")
    if max_steps is not None and (
        type(max_steps) is not int or max_steps < 1
    ):
        raise ValueError("max_steps must be a positive integer")

    target: Path | None = None
    if "workspace" in value:
        raw = value["workspace"]
        if not isinstance(raw, str):
            raise ValueError("workspace path is required")
        if not raw.strip():
            raise ValueError("workspace path is required")
        candidate = Path(raw.strip()).expanduser()
        if not candidate.is_dir():
            raise ValueError(f"not a directory: {candidate}")
        target = candidate.resolve()

    previous_workspace = Path(current.workspace).expanduser().resolve()
    workspace_changed = target is not None and target != previous_workspace
    acquired = False
    if workspace_changed:
        acquired = bool(busy.acquire(blocking=False))
        if not acquired:
            raise TaskConflict("请等当前任务停止后再切换工作目录。")
    try:
        raw_config = store.read_config()
        instructions_path = Path(store.home) / INSTRUCTIONS_NAME
        had_instructions = instructions_path.is_file()
        old_instructions = (
            instructions_path.read_text(encoding="utf-8") if had_instructions else None
        )
        wrote_config = False
        wrote_instructions = False
        try:
            if any(key in value for key in ("permission", "max_steps", "workspace")):
                store.save_preferences(
                    permission=permission,
                    max_steps=max_steps,
                    workspace=str(target) if target is not None else None,
                )
                wrote_config = True
            if "instructions" in value:
                save_instructions(store.home, instructions)
                wrote_instructions = True
            if workspace_changed and workspace_effect is not None:
                workspace_effect(target)
        except BaseException as original:
            # Keep the two files consistent when the second write or the live
            # effect fails. ``replace_text`` already makes each file atomic.
            failures = []
            if wrote_config:
                try:
                    store.write_config(raw_config)
                except OSError as error:
                    failures.append(f"config restore failed: {error}")
            if wrote_instructions:
                try:
                    if had_instructions:
                        save_instructions(store.home, old_instructions or "")
                    else:
                        instructions_path.unlink(missing_ok=True)
                except OSError as error:
                    failures.append(f"instructions restore failed: {error}")
            if failures:
                raise RuntimeError(
                    "settings save failed and rollback left partial state ("
                    + "; ".join(failures) + "); original error: " + str(original)
                ) from None
            raise
    finally:
        if acquired:
            busy.release()
    return store.load()
