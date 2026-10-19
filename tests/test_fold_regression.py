"""Regression: a message carrying both reasoning and prose must show its prose.

Observed in a real session: the final answer arrived with 11 characters of
reasoning attached, the grouping logic treated it as pure reasoning, and the
559-character body vanished from the transcript.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = (ROOT / "nexus" / "web" / "app.js").read_text(encoding="utf-8")


def test_grouping_checks_for_body_before_swallowing():
    block = APP[APP.index("function renderTranscript"):APP.index("function formatElapsed")]
    assert "hasBody" in block, "grouping must distinguish reasoning-only from reasoning+prose"
    assert 'item.text || ""' in block or "(item.text || '')" in block
    # The prose branch must not `continue`, or the body is lost.
    assert "if (!hasBody) continue;" in block


def test_answer_renders_the_text_node():
    block = APP[APP.index("function nodeFor"):APP.index("function thinkingNode")]
    assert 'el("div", "answer"' in block


def test_reasoning_still_goes_into_the_fold():
    block = APP[APP.index("function renderTranscript"):APP.index("function formatElapsed")]
    assert "batch.thinking.push(item.thinking)" in block


def test_a_message_with_reasoning_and_text_is_not_treated_as_tool_only():
    """Sanity: the condition still routes plain tool results into tools."""
    block = APP[APP.index("function renderTranscript"):APP.index("function formatElapsed")]
    assert 'item.role === "tool"' in block
    assert "batch.tools.push(item)" in block
