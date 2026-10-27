"""Behavioral contracts for generated prompts and scoped instructions."""

from __future__ import annotations

from dataclasses import replace

from nexus.core import prompt
from nexus.core.workspace import Workspace, inspect, load_instructions, refresh
from nexus.tools import build


def generated(workspace: Workspace, tools: str = "- read_file: read text") -> str:
    return prompt.build_system_prompt(
        workspace,
        tools,
        platform="test-platform",
        date="2026-10-27",
        owner_instructions="OWNER-MARKER: prefer concise reports.",
        skills="- example: Example procedure.",
        interaction_context="CURRENT-CONTEXT",
        task_context="TASK-CONTEXT",
        permission_context="PERMISSION-CONTEXT",
    )


def flat(value: str) -> str:
    return " ".join(value.split())


def test_role_and_environment_describe_the_real_bash_contract():
    registry, _ = build({}, config={})
    schema = next(item for item in registry.schema() if item["name"] == "bash")
    command = schema["parameters"]["properties"]["command"]

    assert command["type"] == "string"
    assert "command-line STRING" in command["description"]
    assert "JSON" in command["description"]

    system = generated(
        Workspace(root=__import__("pathlib").Path.cwd()),
        registry.declarations(),
    )
    assert "bash receives one command-line string (not an argv list)" in system
    assert "does not interpret shell operators" in system
    assert "Retry when the failure is transient" in system
    assert "without a reason to expect a different result" in system

    without_shell = prompt.build_system_prompt(
        Workspace(root=__import__("pathlib").Path.cwd()),
        "- read_file: read text",
        platform="test-platform",
        date="2026-10-27",
    )
    assert "Shell:" not in without_shell


def test_generated_prompt_names_sources_nature_and_priority(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        "PROJECT-BOUNDARY: do not publish this project.", encoding="utf-8"
    )
    system = generated(inspect(tmp_path))

    assert "Source: `owner-instructions.txt` in the Nexus data directory" in system
    assert "Source: instruction files on the path from the filesystem root down to" in system
    assert "plus directories already touched in this run" in system
    assert f"### `{tmp_path / 'AGENTS.md'}`" in system
    assert "Scope: `.`" in system
    assert "PROJECT-BOUNDARY" in system
    plain = flat(system)
    assert "The owner is the highest authority" in plain
    assert "An explicit current owner request overrides" in plain
    assert "ordinary habits" in plain
    assert "the owner's own project instructions" in plain
    assert "Nature: workspace-specific requirements and habits" in plain
    assert "That boundary never overrides an explicit owner request" in plain
    assert "Only when a conflict cannot be resolved" in plain
    assert "do not promote it into a hard prohibition" in plain
    assert "Source: the task controller's saved state" in system
    assert "Source: supplied by the main agent for this request" in system
    assert "PERMISSION-CONTEXT" in system


def test_file_touch_adds_only_its_ancestry_before_request_refresh(tmp_path):
    root = tmp_path / "workspace"
    (root / "pkg" / "deep").mkdir(parents=True)
    (root / "other").mkdir()
    (root / "AGENTS.md").write_text("ROOT-RULE", encoding="utf-8")
    (root / "pkg" / "AGENTS.md").write_text("NESTED-RULE", encoding="utf-8")
    (root / "other" / "AGENTS.md").write_text("UNTOUCHED-RULE", encoding="utf-8")
    target = root / "pkg" / "deep" / "code.py"
    target.write_text("print('ok')\n", encoding="utf-8")

    registry, context = build({}, config={}, root=root)
    result = registry.execute("read_file", {"path": "pkg/deep/code.py"}, context)
    assert result.ok, result.content
    assert context.instruction_paths == {str(target.resolve())}

    initial = inspect(root)
    assert "ROOT-RULE" in initial.instructions
    assert "NESTED-RULE" not in initial.instructions

    updated = refresh(initial, context.instruction_paths)
    assert updated.root == initial.root
    assert "ROOT-RULE" in updated.instructions
    assert f"### `{root / 'pkg' / 'AGENTS.md'}`" in updated.instructions
    assert "Scope: `pkg/`" in updated.instructions
    assert "NESTED-RULE" in updated.instructions
    assert "UNTOUCHED-RULE" not in updated.instructions

    system = generated(updated)
    assert "NESTED-RULE" in system
    assert "UNTOUCHED-RULE" not in system


def test_refresh_uses_changed_file_on_the_next_request(tmp_path):
    (tmp_path / "AGENTS.md").write_text("OLD-RULE", encoding="utf-8")
    first = inspect(tmp_path)
    assert "OLD-RULE" in generated(first)

    (tmp_path / "AGENTS.md").write_text("NEW-RULE", encoding="utf-8")
    second = refresh(first)
    assert "OLD-RULE" in first.instructions
    assert "NEW-RULE" in second.instructions
    assert "OLD-RULE" not in second.instructions


def test_ancestor_chain_is_kept_but_touched_paths_stay_inside_the_workspace(tmp_path):
    (tmp_path / "AGENTS.md").write_text("PARENT-RULE", encoding="utf-8")
    root = tmp_path / "workspace"
    (root / "pkg").mkdir(parents=True)
    (root / "AGENTS.md").write_text("ROOT-ONLY", encoding="utf-8")
    outside = tmp_path / "outside" / "AGENTS.md"
    outside.parent.mkdir()
    outside.write_text("OUTSIDE-RULE", encoding="utf-8")

    loaded = load_instructions(root, [root / "pkg" / "file.py", outside])
    assert "ROOT-ONLY" in loaded
    assert loaded.count("ROOT-ONLY") == 1
    assert "PARENT-RULE" in loaded
    assert f"### `{tmp_path / 'AGENTS.md'}`" in loaded
    assert "Scope: `" + str(tmp_path) + "`" in loaded
    assert "OUTSIDE-RULE" not in loaded


def test_oversized_instruction_is_visible_and_marked_as_truncated(tmp_path):
    body = "HEAD-MARKER\n" + "h" * 40_000 + "\nMIDDLE-MARKER\n" + "t" * 20_000 + "\nTAIL-MARKER"
    (tmp_path / "AGENTS.md").write_text(body, encoding="utf-8")
    workspace = refresh(Workspace(root=tmp_path), [tmp_path / "AGENTS.md"])
    system = generated(replace(workspace, is_repo=False))

    assert "HEAD-MARKER" in system
    assert "TAIL-MARKER" in system
    assert "MIDDLE-MARKER" not in system
    assert "Instruction file is" in system
    assert "middle bytes were omitted" in system
