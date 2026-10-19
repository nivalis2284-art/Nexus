"""Tests for Windows command resolution, found by connecting to a real MCP server.

``npx`` and friends are ``.cmd`` shims on Windows. ``CreateProcess`` cannot run
them, and the resulting ``WinError 2`` looks exactly like "binary missing", which
sends you looking in the wrong place. The real official filesystem server is
what surfaced this.
"""

from __future__ import annotations

import os
import sys


from nexus.mcp import resolve_command


def test_non_windows_is_untouched(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    argv = ["npx", "-y", "pkg"]
    assert resolve_command(argv) == argv


def test_plain_executable_is_resolved(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    resolved = resolve_command([sys.executable, "-c", "pass"])
    assert resolved[0].lower().endswith(".exe")
    assert resolved[1:] == ["-c", "pass"]


def test_cmd_shim_is_never_run_directly(monkeypatch):
    """A .cmd shim must be routed through node or cmd.exe, never executed."""
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr("shutil.which", lambda name: r"C:\tools\npx.cmd")

    resolved = resolve_command(["npx", "-y", "pkg"])
    assert resolved[0].lower() != "npx"
    assert not resolved[0].lower().endswith(".cmd") or resolved[0].lower() == "cmd.exe"
    assert "-y" in resolved and "pkg" in resolved


def test_node_launcher_is_preferred_when_present(monkeypatch, tmp_path):
    """If node and the npx cli exist, run the JS directly."""
    shim_dir = tmp_path
    cli = shim_dir / "node_modules" / "npm" / "bin" / "npx-cli.js"
    cli.parent.mkdir(parents=True)
    cli.write_text("// stub", encoding="utf-8")
    shim = shim_dir / "npx.cmd"
    shim.write_text("@echo off", encoding="utf-8")

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: str(shim) if name == "npx" else r"C:\nodejs\node.exe",
    )

    resolved = resolve_command(["npx", "-y", "pkg"])
    assert resolved[0].lower().endswith("node.exe")
    assert resolved[1].endswith("npx-cli.js")


def test_falls_back_to_cmd_exe(monkeypatch, tmp_path):
    """Without node, cmd.exe runs the shim; arguments stay a list."""
    shim = tmp_path / "thing.cmd"
    shim.write_text("@echo off", encoding="utf-8")

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: str(shim) if name == "thing" else None,
    )

    resolved = resolve_command(["thing", "a", "b"])
    assert resolved[0].lower().endswith("cmd.exe")
    assert "/c" in resolved
    assert resolved[-2:] == ["a", "b"]


def test_empty_argv_is_safe(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert resolve_command([]) == []
