"""Tests for the two-level fold: batch grouping and elapsed time."""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "nexus" / "web" / "app.js").read_text(encoding="utf-8")


def test_phase_groups_tools_and_thinking():
    """The renderer must put tool calls and thinking into one phase node."""
    assert "function phaseNode" in APP
    assert 'class="phase' in APP or "el(\"details\", \"phase" in APP
    # Both kinds are collected into the same batch object.
    assert "batch.tools.push" in APP
    assert "batch.thinking.push" in APP


def test_phase_label_has_the_two_states():
    assert "正在思考" in APP
    assert "思考完成" in APP
    assert "已思考" in APP


def test_steps_collapse_and_show_first_line():
    """A step's summary shows only the first non-empty line of its thinking."""
    assert "function stepThinking" in APP
    match = re.search(r"const first = String\(text\)\.split\(.*?find\(", APP, re.S)
    assert match, "stepThinking must derive a first line"
    assert "truncate(first.trim(), 120)" in APP


def test_step_is_closed_by_default():
    """Only the live phase is open; settled steps and phases stay folded."""
    # phaseNode opens only when live.
    assert "details.open = Boolean(batch.live)" in APP
    # stepThinking never sets open on a settled step.
    block = APP[APP.index("function stepThinking"):APP.index("function stepTool")]
    assert "details.open" not in block


def test_elapsed_is_adopted_from_the_closing_answer():
    assert 'typeof item.elapsed === "number"' in APP
    assert "batch.elapsed = item.elapsed" in APP


def test_format_elapsed_units():
    assert "function formatElapsed" in APP
    assert '" 秒"' in APP
    assert '" 分 "' in APP


def test_server_records_and_returns_elapsed():
    server = (ROOT / "nexus" / "web" / "server.py").read_text(encoding="utf-8")
    assert "started = time.monotonic()" in server
    assert 'message["elapsed"] = elapsed' in server
    assert 'data["elapsed"] = elapsed' in server
    assert '"elapsed": item.data.get("elapsed")' in server


def test_no_dead_single_tool_styles():
    css = (ROOT / "nexus" / "web" / "app.css").read_text(encoding="utf-8")
    assert ".tool .tname" not in css
    assert ".phase" in css and ".step" in css
    assert css.count("{") == css.count("}")
