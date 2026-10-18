"""Tests for compaction, subagents, and skills."""

from __future__ import annotations

import pytest

from nexus.core import compaction as c
from nexus.core import subagent
from nexus import skills


# ---------------------------------------------------------------- pruning


def test_prune_shortens_oversized_tool_results():
    messages = [{"role": "tool", "tool_call_id": "1", "content": "x" * 20_000}]
    out, report = c.prune(messages)
    assert report.pruned_results == 1
    assert len(out[0]["content"]) < 20_000
    assert c.PRUNE_MARKER in out[0]["content"]


def test_prune_keeps_head_and_tail():
    body = "H" * 5000 + "M" * 5000 + "T" * 5000
    out, _ = c.prune([{"role": "tool", "tool_call_id": "1", "content": body}])
    text = out[0]["content"]
    assert text.startswith("H")
    assert text.endswith("T")
    assert "M" * 100 not in text


def test_prune_leaves_small_results_alone():
    messages = [{"role": "tool", "tool_call_id": "1", "content": "short"}]
    out, report = c.prune(messages)
    assert report.pruned_results == 0
    assert out[0]["content"] == "short"


def test_prune_does_not_mutate_input():
    messages = [{"role": "tool", "tool_call_id": "1", "content": "x" * 20_000}]
    c.prune(messages)
    assert len(messages[0]["content"]) == 20_000


def test_prune_never_splits_a_surrogate_pair():
    body = "a" * 100 + "\U0001F600" * 50
    out, _ = c.prune([{"role": "tool", "tool_call_id": "1", "content": body}])
    text = out[0]["content"]
    assert all(not (0xD800 <= ord(ch) <= 0xDFFF) for ch in text)


# ---------------------------------------------------------------- pairing


def test_paired_transcript_is_accepted():
    messages = [
        {"role": "assistant", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a"},
    ]
    assert c.assert_paired(messages) is None


def test_unfinished_tool_call_blocks_compaction():
    messages = [{"role": "assistant", "tool_calls": [{"id": "a"}]}]
    with pytest.raises(c.CompactionBlocked):
        c.assert_paired(messages)


# ---------------------------------------------------------------- splitting


def test_split_keeps_a_verbatim_tail():
    messages = [
        {"role": "user", "content": "old " * 500},
        {"role": "assistant", "content": "reply " * 500},
        {"role": "user", "content": "new question"},
    ]
    head, tail = c.split_for_summary(messages, retain_tokens=50)
    assert tail, "the newest message must survive verbatim"
    assert tail[-1]["content"] == "new question"
    assert head


def test_split_never_orphans_a_tool_result():
    """If the tail starts with a tool result, its call must come with it."""
    messages = [
        {"role": "user", "content": "go " * 2000},
        {"role": "assistant", "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "result"},
    ]
    head, tail = c.split_for_summary(messages, retain_tokens=10)
    roles = [m.get("role") for m in tail]
    if "tool" in roles:
        assert "assistant" in roles, "tool result kept without its call"


# ---------------------------------------------------------------- summarise


def test_no_compaction_below_threshold():
    messages = [{"role": "user", "content": "short"}]
    out, report = c.summarise(
        messages, window_tokens=100_000, call_model=lambda prompt: "summary"
    )
    assert out is messages
    assert report.reason == "below threshold"


def test_summariser_failure_keeps_original():
    messages = [
        {"role": "user", "content": "x " * 30_000},
        {"role": "assistant", "content": "y " * 30_000},
    ]
    out, report = c.summarise(
        messages, window_tokens=1000, call_model=lambda prompt: None
    )
    assert out is messages
    assert "summariser" in report.reason


def test_compaction_must_actually_shrink():
    """A 'compaction' that grows the context is rejected."""
    messages = [
        {"role": "user", "content": "x " * 30_000},
        {"role": "assistant", "content": "y " * 30_000},
    ]
    huge = "z " * 200_000
    out, report = c.summarise(
        messages, window_tokens=1000, call_model=lambda prompt: huge
    )
    assert out is messages
    assert report.reason == "no gain"


def test_single_oversized_message_is_reported_not_crashed():
    """One message is the whole history: there is nothing to fold it into."""
    messages = [{"role": "user", "content": "x " * 50_000}]
    out, report = c.summarise(
        messages, window_tokens=1000, call_model=lambda prompt: "summary"
    )
    assert out is messages
    assert report.reason == "nothing to compact"


def test_successful_compaction_produces_a_tagged_checkpoint():
    messages = [
        {"role": "user", "content": "a " * 20_000},
        {"role": "assistant", "content": "b " * 20_000},
        {"role": "user", "content": "latest message"},
    ]
    out, report = c.summarise(
        messages,
        window_tokens=2000,
        call_model=lambda prompt: "## Summary\n- did things",
    )
    assert report.summarised
    assert out[0]["role"] == "user"
    assert c.SUMMARY_TAG_OPEN in out[0]["content"]
    assert out[-1]["content"] == "latest message"


def test_epoch_budget_is_enforced():
    messages = [{"role": "user", "content": "x " * 50_000}]
    out, report = c.summarise(
        messages, window_tokens=1000, call_model=lambda p: "s", epochs=16, max_epochs=16
    )
    assert out is messages
    assert "epoch" in report.reason


def test_compact_without_summariser_only_prunes():
    messages = [{"role": "tool", "tool_call_id": "1", "content": "x" * 20_000}]
    out, report = c.compact(messages, window_tokens=1000)
    assert not report.summarised
    assert c.PRUNE_MARKER in out[0]["content"]


# ---------------------------------------------------------------- subagent


def test_depth_limit_rejects_recursion():
    with pytest.raises(subagent.DepthExceeded):
        subagent.assert_depth(3, limit=2)
    subagent.assert_depth(1, limit=2)
    subagent.assert_depth(2, limit=2)


def test_depth_must_be_positive():
    with pytest.raises(subagent.SubagentError):
        subagent.assert_depth(0, limit=2)


def test_subagent_reduces_events_to_final_text():
    import asyncio

    class Event:
        def __init__(self, type_, text="", data=None):
            self.type = type_
            self.text = text
            self.data = data or {}

    async def factory(prompt, budget):
        assert "scoped task" in prompt
        yield Event("delta", "partial ")
        yield Event("delta", "report")
        yield Event("done", data={"steps": 3, "stopped": "complete"})

    result = asyncio.run(
        subagent.run_subagent(
            subagent.SubagentRequest(task="do it", max_steps=5),
            make_runner=factory,
        )
    )
    assert result.text == "partial report"
    assert result.steps == 3
    assert result.depth == 1


def test_subagent_reports_empty_output_clearly():
    import asyncio

    async def factory(prompt, budget):
        yield type("E", (), {"type": "done", "text": "", "data": {}})()

    result = asyncio.run(
        subagent.run_subagent(
            subagent.SubagentRequest(task="t"), make_runner=factory
        )
    )
    assert "nothing" in result.text


# ---------------------------------------------------------------- skills


def _write_skill(root, folder, name, description, when="", body="BODY"):
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    extra = f"when: {when}\n" if when else ""
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{extra}---\n{body}\n",
        encoding="utf-8",
    )


def test_skill_is_discovered_with_metadata(tmp_path):
    _write_skill(tmp_path, "review", "code-review", "Review a diff.", "asked to review")
    found = skills.discover([tmp_path])
    skill = found.get("code-review")
    assert skill is not None
    assert skill.description == "Review a diff."
    assert skill.when == "asked to review"
    assert "BODY" in skill.body


def test_manifest_omits_bodies(tmp_path):
    _write_skill(tmp_path, "review", "code-review", "Review a diff.", body="SECRET BODY")
    found = skills.discover([tmp_path])
    manifest = found.manifest()
    assert "code-review" in manifest
    assert "SECRET BODY" not in manifest


def test_flat_markdown_skill_is_supported(tmp_path):
    (tmp_path / "flat.md").write_text(
        "---\nname: flat\ndescription: A flat skill.\n---\nbody\n", encoding="utf-8"
    )
    assert skills.discover([tmp_path]).get("flat") is not None


def test_invalid_name_is_rejected(tmp_path):
    _write_skill(tmp_path, "bad", "Bad Name", "nope")
    assert skills.discover([tmp_path]).get("Bad Name") is None
    assert len(skills.discover([tmp_path])) == 0


def test_skill_without_description_is_rejected(tmp_path):
    directory = tmp_path / "nodesc"
    directory.mkdir()
    (directory / "SKILL.md").write_text("---\nname: nodesc\n---\nbody\n", encoding="utf-8")
    assert len(skills.discover([tmp_path])) == 0


def test_project_root_wins_over_user_root(tmp_path):
    project = tmp_path / "project"
    user = tmp_path / "user"
    _write_skill(project, "x", "shared", "from project", body="PROJECT")
    _write_skill(user, "x", "shared", "from user", body="USER")
    found = skills.discover([project, user])
    assert found.get("shared").description == "from project"
