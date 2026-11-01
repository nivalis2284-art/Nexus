import os

import pytest

from nexus.core.permission import ALLOW, ASK, DENY, default_policy, target
from nexus.tools import build


@pytest.mark.parametrize("tool", ["grep", "glob"])
def test_file_search_stays_inside_workspace_with_directory_alias(tmp_path, tool):
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "nested").mkdir()
    (root / "nested" / "inside.txt").write_text("search marker", encoding="utf-8")
    (outside / "outside.txt").write_text("search marker", encoding="utf-8")
    alias = root / "linked"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(alias))
    else:
        alias.symlink_to(outside, target_is_directory=True)
    try:
        registry, context = build(root=root)
        result = registry.execute(tool, {
            "path": ".", "pattern": "search marker" if tool == "grep" else "**/*.txt",
        }, context)
        assert result.ok
        assert "nested/inside.txt" in result.content
        assert "outside.txt" not in result.content
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


def test_grep_uses_the_same_hardlink_boundary_as_read_file(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private marker", encoding="utf-8")
    (root / "linked.txt").hardlink_to(outside)
    registry, context = build(root=root)
    assert not registry.execute("read_file", {"path": "linked.txt"}, context).ok
    result = registry.execute("grep", {"pattern": "private marker"}, context)
    assert result.ok
    assert result.data["matches"] == 0


def test_write_and_command_variants_share_default_confirmation():
    policy = default_policy("ask")
    for name in ["write_file", "edit_file", "apply_patch", "bash", "bash_background", "powershell", "powershell_background"]:
        assert policy.decide(name, "value") == ASK
        assert default_policy("yolo").decide(name, "value") == ALLOW
    assert target("apply_patch", {"path": "a.txt"}) == "a.txt"
    assert target("bash_background", {"command": "git status"}) == "git status"
    configured = default_policy("ask", [{"tool": "bash_background", "pattern": "git *", "action": DENY}])
    assert configured.decide("bash_background", "git status") == DENY
    assert configured.decide("powershell", "git status") == ASK
    assert target("powershell", {"command": "Get-ChildItem"}) == "Get-ChildItem"


def test_nonzero_command_reports_failure_without_losing_details(tmp_path):
    import sys
    registry, context = build(root=tmp_path)
    result = registry.execute("bash", {"command": f'"{sys.executable}" -c "raise SystemExit(7)"'}, context)
    assert not result.ok
    assert result.data["exit_code"] == 7
    assert "exit: 7" in result.content


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior")
def test_windows_shell_commands_use_explicit_powershell(tmp_path):
    (tmp_path / "example.txt").write_text("example", encoding="utf-8")
    registry, context = build(root=tmp_path)
    missing = registry.execute("bash", {"command": "nexus-missing-executable-for-test"}, context)
    assert not missing.ok
    assert "powershell.exe -NoProfile -Command" in missing.content
    result = registry.execute("bash", {
        "command": 'powershell.exe -NoProfile -Command "Get-ChildItem -Name | Select-Object -First 1"',
    }, context)
    assert result.ok
    assert "example.txt" in result.content
    assert "powershell_background" in next(tool["description"] for tool in registry.schema()
                                            if tool["name"] == "bash_background")


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior")
def test_powershell_executes_scripts_and_propagates_failures(tmp_path):
    folder = tmp_path / "中文目录"
    folder.mkdir()
    (folder / "文件.txt").write_text("hello", encoding="utf-8")
    registry, context = build(root=folder)
    result = registry.execute("powershell", {
        "command": "Write-Output '中文';\nGet-ChildItem -Name | Select-Object -First 1",
    }, context)
    assert result.ok
    assert "中文" in result.content and "文件.txt" in result.content
    for command in ('cmd.exe /c exit 7', 'Write-Error "failure"'):
        result = registry.execute("powershell", {"command": command}, context)
        assert not result.ok
        assert result.data["exit_code"] != 0


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell behavior")
def test_powershell_background_output_and_stop(tmp_path):
    import time
    registry, context = build(root=tmp_path)
    started = registry.execute("powershell_background", {
        "command": "Write-Output '后台就绪'; Start-Sleep -Seconds 30",
    }, context)
    job_id = started.data["job_id"]
    try:
        listed = registry.execute("job_list", {}, context)
        assert "Start-Sleep -Seconds 30" in listed.content
        assert "$ErrorActionPreference" not in listed.content
        text = started.content
        cursor = started.data["cursor"]
        deadline = time.monotonic() + 5
        while "后台就绪" not in text and time.monotonic() < deadline:
            result = registry.execute("job_output", {"job_id": job_id, "cursor": cursor}, context)
            cursor = result.data["cursor"]
            text += result.content
            if "后台就绪" not in text:
                time.sleep(.05)
        assert "后台就绪" in text
        stopped = registry.execute("job_kill", {"job_id": job_id}, context)
        assert stopped.ok and stopped.data["stopped"]
    finally:
        context.jobs.close_all()


def test_mcp_transport_failure_is_a_tool_result(tmp_path):
    from types import SimpleNamespace

    from nexus.mcp import MCPError, ToolDeclaration
    def fail(*args):
        raise MCPError("disconnected")
    declaration = ToolDeclaration("echo", "Echo", {"type": "object"})
    server = SimpleNamespace(name="stub", call_tool=fail)
    live = SimpleNamespace(tools={"mcp__stub__echo": (server, declaration)})
    registry, context = build(root=tmp_path, mcp=live)
    result = registry.execute("mcp__stub__echo", {}, context)
    assert not result.ok
    assert "disconnected" in result.content


def test_foreground_command_observes_shared_stop_signal(tmp_path):
    import sys
    import threading
    import time
    marker = tmp_path / "ready"
    script = tmp_path / "long.py"
    script.write_text(
        f"from pathlib import Path\nimport time\nPath({str(marker)!r}).touch()\ntime.sleep(30)\n",
        encoding="utf-8",
    )
    stopped = threading.Event()
    registry, context = build(root=tmp_path, cancel_event=stopped)
    results = []
    thread = threading.Thread(target=lambda: results.append(registry.execute(
        "bash", {"command": f'"{sys.executable}" "{script}"'}, context)))
    thread.start()
    try:
        deadline = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists(), "command did not start"
        stopped.set()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert not results[0].ok
        assert "[cancelled]" in results[0].content
    finally:
        stopped.set()
        thread.join(timeout=5)


def test_dispatcher_refuses_a_tool_queued_before_stop(tmp_path):
    import threading
    stopped = threading.Event()
    registry, context = build(root=tmp_path, cancel_event=stopped)
    stopped.set()
    result = registry.execute("write_file", {"path": "queued", "content": "x"}, context)
    assert result.error_code == "cancelled"
    assert not (tmp_path / "queued").exists()
