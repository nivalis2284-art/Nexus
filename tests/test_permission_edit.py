"""Tests for configurable permissions and the edit failure hints."""

from __future__ import annotations


from nexus.core.permission import ALLOW, ASK, DENY, default_policy


def test_default_policy_is_unchanged():
    """Without configuration, behaviour must be exactly what it was before."""
    policy = default_policy("ask")
    assert policy.decide("write_file", "a.txt") == ASK
    assert policy.decide("edit_file", "a.txt") == ASK
    assert policy.decide("bash", "ls") == ASK
    assert policy.decide("read_file", "a.txt") == ALLOW
    assert policy.decide("glob", "**/*.py") == ALLOW


def test_yolo_allows_everything():
    policy = default_policy("yolo")
    assert policy.decide("bash", "rm -rf /") == ALLOW


def test_configured_rules_replace_the_defaults():
    policy = default_policy(
        "ask",
        [
            {"tool": "bash", "pattern": "*", "action": ASK},
            {"tool": "bash", "pattern": "git *", "action": ALLOW},
            {"tool": "bash", "pattern": "rm *", "action": DENY},
        ],
    )
    assert policy.decide("bash", "git status") == ALLOW
    assert policy.decide("bash", "rm -rf build") == DENY
    assert policy.decide("bash", "ls") == ASK


def test_last_match_wins():
    """The documented ordering rule: later rules override earlier ones."""
    policy = default_policy(
        "ask",
        [
            {"tool": "bash", "pattern": "git *", "action": ALLOW},
            {"tool": "bash", "pattern": "*", "action": ASK},
        ],
    )
    # The catch-all comes last, so it wins even for git commands.
    assert policy.decide("bash", "git status") == ASK


def test_wildcard_tool_matches_any_tool():
    policy = default_policy("ask", [{"tool": "*", "pattern": "*.tmp", "action": DENY}])
    assert policy.decide("write_file", "x.tmp") == DENY
    assert policy.decide("write_file", "x.py") == ALLOW


def test_invalid_rule_entries_are_ignored():
    policy = default_policy(
        "ask",
        [
            {"tool": "bash", "action": "maybe"},  # bad action
            {"pattern": "*", "action": "allow"},  # missing tool
            "not-a-dict",
            {"tool": "bash", "pattern": "*", "action": ASK},
        ],
    )
    assert policy.decide("bash", "ls") == ASK


def test_empty_rules_fall_back_to_defaults():
    assert default_policy("ask", []) .decide("write_file", "a") == ASK
    assert default_policy("ask", None).decide("write_file", "a") == ASK


def test_remembered_answers_upgrade_ask_to_allow():
    policy = default_policy("ask")
    assert policy.decide("bash", "make") == ASK
    policy.remember("bash", "make")
    assert policy.decide("bash", "make") == ALLOW
    # A different command is still asked.
    assert policy.decide("bash", "make install") == ASK


# ---------------------------------------------------------------- edit hints


def test_edit_reports_near_miss_line(tmp_path):
    from nexus.tools import build as build_tools

    target = tmp_path / "sample.py"
    target.write_text("line one\nline two\n    indented value = 1\n", encoding="utf-8")

    registry, context = build_tools({}, config={}, root=tmp_path)
    result = registry.execute(
        "edit_file",
        {
            "path": "sample.py",
            # The file has four leading spaces; this asks for a tab, which is a
            # genuine mismatch, so the edit must fail rather than guess.
            "old_text": "\tindented value = 1",
            "new_text": "\tindented value = 2",
        },
        context,
    )
    assert not result.ok
    # The message should point at where the text really is.
    assert "line 3" in result.content
    assert "byte for byte" in result.content


def test_edit_duplicate_reports_candidate_lines(tmp_path):
    from nexus.tools import build as build_tools

    target = tmp_path / "dupes.txt"
    target.write_text("same\na\nsame\nb\n", encoding="utf-8")

    registry, context = build_tools({}, config={}, root=tmp_path)
    result = registry.execute(
        "edit_file",
        {"path": "dupes.txt", "old_text": "same", "new_text": "other"},
        context,
    )
    assert not result.ok
    assert "2 times" in result.content
    assert "line" in result.content or "first lines" in result.content


def test_edit_succeeds_on_unique_text(tmp_path):
    from nexus.tools import build as build_tools

    target = tmp_path / "ok.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    registry, context = build_tools({}, config={}, root=tmp_path)
    result = registry.execute(
        "edit_file", {"path": "ok.txt", "old_text": "beta", "new_text": "gamma"}, context
    )
    assert result.ok
    assert target.read_text(encoding="utf-8") == "alpha\ngamma\n"
