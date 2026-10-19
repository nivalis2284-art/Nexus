from nexus.core.permission import ALLOW, ASK, DENY, default_policy, target
from nexus.tools import build


def test_write_and_command_variants_share_default_confirmation():
    policy = default_policy("ask")
    for name in ["write_file", "edit_file", "apply_patch", "bash", "bash_background"]:
        assert policy.decide(name, "value") == ASK
        assert default_policy("yolo").decide(name, "value") == ALLOW
    assert target("apply_patch", {"path": "a.txt"}) == "a.txt"
    assert target("bash_background", {"command": "git status"}) == "git status"
    configured = default_policy("ask", [{"tool": "bash_background", "pattern": "git *", "action": DENY}])
    assert configured.decide("bash_background", "git status") == DENY


def test_nonzero_command_reports_failure_without_losing_details(tmp_path):
    import sys
    registry, context = build(root=tmp_path)
    result = registry.execute("bash", {"command": f'"{sys.executable}" -c "raise SystemExit(7)"'}, context)
    assert not result.ok
    assert result.data["exit_code"] == 7
    assert "exit: 7" in result.content


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
    script.write_text(f"from pathlib import Path\nimport time\nPath({str(marker)!r}).touch()\ntime.sleep(30)\n")
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
