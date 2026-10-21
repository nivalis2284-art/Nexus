"""Process-level tests for the dedicated local-text observation MCP."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from nexus.autonomy import observer as observer_module
from nexus.autonomy.observer import IMPLEMENTATION, ObserverService
from nexus.autonomy.observer_client import ObserverClient, ObserverClientError

ROOT = Path(__file__).resolve().parents[1]


def _client(tmp_path: Path, source: Path, *, interval: float = 0.1) -> ObserverClient:
    return ObserverClient(tmp_path / "observer-data", [str(source)], interval=interval)


def _put(client: ObserverClient, watch_id: str, path: Path, *, spec: int = 1,
         control: int = 1, request: str | None = None, recursive: bool = True) -> dict:
    return client.call("watch_put", {
        "watch_id": watch_id,
        "path": str(path),
        "recursive": recursive,
        "interval": 0.1,
        "spec_version": spec,
        "control_version": control,
        "request_id": request or f"put-{watch_id}-{spec}-{control}",
    })


def _control(client: ObserverClient, name: str, watch_id: str, *, spec: int = 1,
             control: int = 1, request: str | None = None) -> dict:
    return client.call(name, {
        "watch_id": watch_id,
        "spec_version": spec,
        "control_version": control,
        "request_id": request or f"{name}-{watch_id}-{control}",
    })


def _renew(client: ObserverClient, *, control: int = 1, ttl: float = 30) -> dict:
    return client.call("lease_renew", {"control_version": control, "ttl": ttl})


def _events(client: ObserverClient, after: int = 0, limit: int = 100) -> dict:
    return client.call("events_read", {"after": after, "limit": limit})


def _wait_for_events(client: ObserverClient, *, after: int = 0, kinds: set[str] | None = None,
                     timeout: float = 5) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        page = _events(client, after)
        if page["events"] and (kinds is None or any(item["kind"] in kinds for item in page["events"])):
            return page
        time.sleep(0.05)
    raise AssertionError(f"no matching observer events after {after}: {_events(client, after)}")


def test_unicode_evidence_uses_utf8_even_with_legacy_windows_stdout(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "cp1252")
    source = tmp_path / "notes"
    source.mkdir()
    note = source / "note.md"
    note.write_text("校准记录：灵观察到 4 与 9，尚未核对单位。🌱\n", encoding="utf-8")
    with _client(tmp_path, source) as client:
        _put(client, "unicode", source)
        _renew(client)
        page = _wait_for_events(client, kinds={"baseline"})
        assert "校准记录" in page["events"][0]["content"]
        assert "🌱" in page["events"][0]["content"]
        note.write_text("新证据：9 对应 4.5，不能断言漂移。\n", encoding="utf-8")
        changed = _wait_for_events(client, after=page["next_cursor"], kinds={"modified"})
        assert "新证据" in changed["events"][-1]["content"]
        assert client.call("status", {})["health"]["state"] == "collecting"


def test_no_collection_without_lease_then_baseline_and_content_change(tmp_path: Path):
    source = tmp_path / "notes"
    source.mkdir()
    note = source / "note.md"
    note.write_bytes(b"first\n")

    with _client(tmp_path, source) as client:
        initial = client.call("status", {})
        assert initial["health"]["state"] == "waiting_lease"
        assert initial["health"]["watches"]["total"] == 0
        put = _put(client, "watch-1", source)
        assert put["watch"]["status"] == "pending_lease"
        assert put["baseline"]["status"] == "not_collected"
        time.sleep(0.4)
        assert _events(client) == {
            "epoch": initial["epoch"], "events": [], "next_cursor": 0,
            "has_more": False, "earliest_seq": None, "latest_seq": None,
        }

        renewed = _renew(client)
        assert renewed["health"]["state"] == "collecting"
        listed = client.call("watches_list", {})
        assert listed["watches"][0]["status"] == "active"
        baseline = _wait_for_events(client, kinds={"baseline"})
        event = baseline["events"][0]
        assert event["kind"] == "baseline"
        assert event["content"] == "first\n"
        assert event["path"] == str(note.resolve())
        assert len(event["hash"]) == 64

        time.sleep(0.35)
        assert _events(client, baseline["next_cursor"])["events"] == []

        note.write_bytes(b"second version\n")
        changed = _wait_for_events(client, after=baseline["next_cursor"], kinds={"modified"})
        modified = changed["events"][0]
        assert modified["kind"] == "modified"
        assert modified["content"] == "second version\n"
        assert modified["previous_hash"] == event["hash"]
        assert modified["hash"] != event["hash"]


def test_runtime_call_shape_uses_tenth_second_interval(tmp_path: Path):
    source = tmp_path / "authorized"
    source.mkdir()
    note = source / "check.md"
    note.write_bytes(b"runtime shape\n")
    with ObserverClient(tmp_path / "data", [str(source)], interval=0.1) as client:
        status = client.call("status", {})
        renewed = client.call("lease_renew", {"control_version": 1, "ttl": 120})
        result = client.call("watch_put", {
            "watch_id": "check", "path": str(source), "recursive": True,
            "interval": 0.1, "spec_version": 1, "control_version": 1,
            "request_id": "check1",
        })
        assert status["epoch"] == renewed["epoch"] == result["epoch"]
        assert result["watch"]["status"] == "active"
        page = _wait_for_events(client, kinds={"baseline"})
        assert {"seq", "event_id", "watch_id", "kind"} <= set(page["events"][0])
        assert page["next_cursor"] == page["events"][-1]["seq"]


def test_restart_replays_same_epoch_and_cursor(tmp_path: Path):
    source = tmp_path / "docs"
    source.mkdir()
    (source / "a.txt").write_bytes(b"alpha\n")
    data = tmp_path / "observer-data"

    first = ObserverClient(data, [str(source)], interval=0.1)
    first.start()
    try:
        first_epoch = first.call("status", {})["epoch"]
        _put(first, "durable", source)
        _renew(first)
        page = _wait_for_events(first, kinds={"baseline"})
        cursor = page["next_cursor"]
        process = first._server._process
        assert process is not None
        process.kill()
        process.wait(timeout=5)
        with pytest.raises(ObserverClientError, match="connection failed|closed before answering"):
            first.call("status", {})
    finally:
        first.close()

    (source / "b.txt").write_bytes(b"beta\n")
    second = ObserverClient(data, [str(source)], interval=0.1)
    second.start()
    try:
        status = second.call("status", {})
        assert status["epoch"] == first_epoch
        assert status["health"]["state"] == "waiting_lease"
        listed = second.call("watches_list", {})["watches"]
        assert len(listed) == 1 and listed[0]["status"] == "active"
        replay = _events(second, cursor)
        assert replay["epoch"] == first_epoch
        assert replay["events"] == []
        _renew(second, control=2)
        resumed = _wait_for_events(second, after=cursor, kinds={"gap", "created"})
        assert resumed["events"][0]["kind"] == "gap"
        assert any(item["kind"] == "created" and item["path"].endswith("b.txt")
                   for item in resumed["events"])
    finally:
        second.close()


def test_same_control_version_renews_and_restart_reactivates_but_pause_stays(tmp_path: Path):
    source = tmp_path / "renew"
    source.mkdir()
    note = source / "renew.txt"
    note.write_bytes(b"renew\n")
    data = tmp_path / "data"
    first = ObserverClient(data, [str(source)], interval=0.1)
    first.start()
    try:
        _renew(first, control=3, ttl=0.5)
        first_lease = _renew(first, control=3, ttl=30)
        time.sleep(0.6)
        assert first.call("status", {})["health"]["state"] == "collecting"
        _put(first, "active", note, control=3, request="renew-active")
        _put(first, "paused", note, control=3, request="renew-paused")
        _control(first, "watch_pause", "paused", control=4, request="renew-pause")
        active_baseline = _wait_for_events(first, kinds={"baseline"})
        assert active_baseline["events"]
    finally:
        first.close()

    second = ObserverClient(data, [str(source)], interval=0.1)
    second.start()
    try:
        status = second.call("status", {})
        assert status["epoch"] == first_lease["epoch"]
        assert status["health"]["state"] == "waiting_lease"
        renewed = _renew(second, control=4, ttl=30)
        assert renewed["health"]["state"] == "collecting"
        watches = {item["watch_id"]: item for item in second.call("watches_list", {})["watches"]}
        assert watches["active"]["status"] == "active"
        assert watches["paused"]["status"] == "paused"
        page = _wait_for_events(second, after=active_baseline["next_cursor"], kinds={"gap"})
        assert page["events"][0]["kind"] == "gap"
        assert all(item["watch_id"] != "paused" for item in page["events"])
    finally:
        second.close()


def test_rule_crud_pause_resume_stop_and_tombstone(tmp_path: Path):
    source = tmp_path / "rules"
    source.mkdir()
    file = source / "rule.txt"
    file.write_text("one\n", encoding="utf-8")

    with _client(tmp_path, source) as client:
        _renew(client)
        created = _put(client, "rule", file, recursive=False)
        assert created["watch"]["status"] == "active"
        assert created["watch"]["baseline_done"] is True
        assert created["baseline"]["status"] == "established"
        original = created["watch"]

        updated = _put(client, "rule", file, spec=2, control=2, recursive=False)
        assert updated["watch"]["spec_version"] == 2
        assert updated["baseline"]["status"] == "preserved"

        paused = _control(client, "watch_pause", "rule", spec=2, control=3)
        assert paused["watch"]["status"] == "paused"
        file.write_text("while paused\n", encoding="utf-8")
        time.sleep(0.35)
        paused_events = _events(client)
        assert not any(item["kind"] == "modified" for item in paused_events["events"])

        resumed = _control(client, "watch_resume", "rule", spec=2, control=4)
        assert resumed["watch"]["status"] == "active"
        after_resume = _wait_for_events(client, after=paused_events["next_cursor"], kinds={"gap"})
        assert after_resume["events"][0]["kind"] == "gap"

        stopped = _control(client, "watch_stop", "rule", spec=2, control=5)
        assert stopped["watch"]["status"] == "stopped"
        tombstone = client.call("watches_list", {})["watches"][0]
        assert tombstone["status"] == "stopped"
        with pytest.raises(ObserverClientError) as error:
            _control(client, "watch_resume", "rule", spec=2, control=6)
        assert error.value.code == "watch_stopped"
        with pytest.raises(ObserverClientError) as error:
            _put(client, "rule", file, spec=3, control=6, request="recreate")
        assert error.value.code == "watch_stopped"
        assert original["spec_version"] == 1


def test_paused_watch_does_not_scan_until_explicit_resume(tmp_path: Path):
    source = tmp_path / "pause"
    source.mkdir()
    target = source / "pause.md"
    target.write_text("old\n", encoding="utf-8")
    with _client(tmp_path, source) as client:
        _renew(client)
        _put(client, "pause", target)
        initial = _wait_for_events(client, kinds={"baseline"})
        _control(client, "watch_pause", "pause", control=2)
        target.write_text("new\n", encoding="utf-8")
        time.sleep(0.4)
        assert _events(client, initial["next_cursor"])["events"] == []
        _control(client, "watch_resume", "pause", control=3)
        resumed = _wait_for_events(client, after=initial["next_cursor"], kinds={"modified"})
        assert resumed["events"][0]["kind"] == "gap"
        assert any(item["kind"] == "modified" for item in resumed["events"])


def test_out_of_scope_symlink_junction_and_unsupported_file(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("not authorized\n", encoding="utf-8")
    (allowed / "binary.pdf").write_bytes(b"%PDF")
    link = allowed / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this host")

    with _client(tmp_path, allowed) as client:
        _renew(client)
        with pytest.raises(ObserverClientError) as error:
            _put(client, "escape", link, recursive=False)
        assert error.value.code == "out_of_scope"
        with pytest.raises(ObserverClientError) as error:
            _put(client, "pdf", allowed / "binary.pdf", recursive=False)
        assert error.value.code == "unsupported_file"
        with pytest.raises(ObserverClientError) as error:
            _put(client, "outside", secret, recursive=False)
        assert error.value.code == "out_of_scope"
        assert client.call("watches_list", {})["watches"] == []


def test_idempotent_requests_and_version_conflicts(tmp_path: Path):
    source = tmp_path / "idem"
    source.mkdir()
    note = source / "idem.txt"
    note.write_text("idem\n", encoding="utf-8")
    with _client(tmp_path, source) as client:
        _renew(client, control=5)
        first = _put(client, "idem", note, control=5, request="same-put")
        second = _put(client, "idem", note, control=5, request="same-put")
        assert first == second
        with pytest.raises(ObserverClientError) as error:
            _put(client, "idem", note, spec=2, control=5, request="same-put")
        assert error.value.code == "request_conflict"
        with pytest.raises(ObserverClientError) as error:
            _control(client, "watch_pause", "idem", control=4, request="stale-control")
        assert error.value.code == "stale_control_version"
        with pytest.raises(ObserverClientError) as error:
            _control(client, "watch_pause", "idem", spec=9, control=5, request="stale-spec")
        assert error.value.code == "stale_spec_version"


def test_client_rejects_malformed_schema_and_error_results(tmp_path: Path):
    source = tmp_path / "schema"
    source.mkdir()
    with _client(tmp_path, source) as client:
        with pytest.raises(ObserverClientError) as error:
            client.call("events_read", {"after": 0, "limit": 0})
        assert error.value.code == "invalid_arguments"
        with pytest.raises(ObserverClientError) as error:
            client.call("watch_put", {"watch_id": "only"})
        assert error.value.code == "invalid_arguments"
        assert client.call("status", {})["health"]["state"] == "waiting_lease"
        original = client._server._request
        client._server._request = lambda *_args, **_kwargs: {
            "content": [{"type": "text", "text": "{}"}], "isError": False,
        }
        try:
            with pytest.raises(ObserverClientError, match="missing field"):
                client.call("status", {})
        finally:
            client._server._request = original


def test_storage_quota_stops_collection_with_health(tmp_path: Path):
    source = tmp_path / "quota"
    source.mkdir()
    (source / "large.txt").write_text("x" * 4096, encoding="utf-8")
    service = ObserverService(tmp_path / "data", [str(source)], interval=0.1, storage_quota=4096)
    try:
        service.store.start_collection(1, "2099-01-01T00:00:00.000Z", 30)
        service.watch_put({
            "watch_id": "quota", "path": str(source), "recursive": False,
            "interval": 0.1, "spec_version": 1, "control_version": 1,
            "request_id": "quota-put",
        })
        service._tick()
        health = service.status()["health"]
        assert health["state"] == "quota_exhausted"
        assert health["scanning"] is False
    finally:
        service.close()


def test_eof_stops_and_single_owner_lock(tmp_path: Path):
    source = tmp_path / "eof"
    source.mkdir()
    (source / "eof.md").write_text("eof\n", encoding="utf-8")
    data = tmp_path / "data"
    command = [sys.executable, "-m", "nexus.autonomy.observer", "--data", str(data),
               "--roots-json", json.dumps([str(source)])]
    process = subprocess.Popen(
        command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        }) + "\n")
        process.stdin.flush()
        assert process.stdout is not None
        initialized = json.loads(process.stdout.readline())
        assert initialized["result"]["protocolVersion"] == "2025-06-18"

        second = subprocess.run(
            command, check=False, cwd=ROOT, input="", text=True, encoding="utf-8",
            capture_output=True, timeout=15,
        )
        assert second.returncode == 2
        assert "owns this data directory" in second.stderr
    finally:
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        process.wait(timeout=10)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
    assert process.returncode == 0
    with sqlite3.connect(data / "observer.sqlite3") as connection:
        row = connection.execute(
            "SELECT value FROM meta WHERE key='collection_active'"
        ).fetchone()
    assert row == ("0",)


def test_implementation_change_creates_new_epoch(tmp_path: Path):
    source = tmp_path / "epoch"
    source.mkdir()
    data = tmp_path / "data"
    first = ObserverService(data, [str(source)])
    first_epoch = first.epoch
    first.close()
    with sqlite3.connect(data / "observer.sqlite3") as connection:
        connection.execute(
            "UPDATE meta SET value=? WHERE key='implementation'", ("other-observer/9",)
        )
    second = ObserverService(data, [str(source)])
    try:
        assert second.epoch != first_epoch
        with sqlite3.connect(data / "observer.sqlite3") as connection:
            implementation = connection.execute(
                "SELECT value FROM meta WHERE key='implementation'"
            ).fetchone()[0]
        assert implementation == IMPLEMENTATION
    finally:
        second.close()


def test_pending_lease_does_not_block_baseline_after_first_renew(tmp_path: Path):
    source = tmp_path / "pending"
    source.mkdir()
    note = source / "pending.md"
    note.write_bytes(b"pending lease\n")
    with _client(tmp_path, source) as client:
        _put(client, "pending", note, request="pending-put")
        before = client.call("watches_list", {})["watches"][0]
        assert before["status"] == "pending_lease"
        assert before["baseline_done"] is False
        _renew(client, control=2, ttl=30)
        baseline = _wait_for_events(client, kinds={"baseline"})
        assert baseline["events"][0]["content"] == "pending lease\n"
        after = client.call("watches_list", {})["watches"][0]
        assert after["status"] == "active"
        assert after["baseline_done"] is True


def test_runtime_replay_keeps_pause_and_reports_actual_version(tmp_path: Path):
    source = tmp_path / "runtime-replay"
    source.mkdir()
    note = source / "runtime.md"
    note.write_bytes(b"runtime replay\n")
    with _client(tmp_path, source) as client:
        _renew(client, control=1)
        created = _put(client, "runtime", note, control=1, request="runtime-put")
        assert created["watch"]["status"] == "active"
        paused = _control(client, "watch_pause", "runtime", control=2, request="runtime-pause")
        assert paused["watch"]["status"] == "paused"
        _renew(client, control=2)
        replayed = _put(client, "runtime", note, control=2, request="runtime-replay")
        assert replayed["watch"]["status"] == "paused"
        assert replayed["watch"]["control_version"] == 2
        same = _put(client, "runtime", note, control=2, request="runtime-replay")
        assert same == replayed
        with pytest.raises(ObserverClientError) as error:
            _control(client, "watch_resume", "runtime", spec=2, control=3,
                     request="resume-active")
        assert error.value.code in ("stale_spec_version", "watch_not_paused")


def test_failed_scan_does_not_build_partial_baseline(tmp_path: Path, monkeypatch):
    source = tmp_path / "partial"
    source.mkdir()
    (source / "good.txt").write_bytes(b"good\n")
    (source / "bad.txt").write_bytes(b"bad\n")
    service = ObserverService(tmp_path / "data", [str(source)], interval=0.1)
    try:
        service.store.start_collection(1, "2099-01-01T00:00:00.000Z", 30)
        real_scan = observer_module._scan_files

        def partial_scan(watch, roots):
            files, errors, _complete, missing = real_scan(watch, roots)
            return files, [*errors, {"reason": "read_error", "path": "bad.txt"}], False, missing

        monkeypatch.setattr(observer_module, "_scan_files", partial_scan)
        put = service.watch_put({
            "watch_id": "partial", "path": str(source), "recursive": False,
            "interval": 0.1, "spec_version": 1, "control_version": 1,
            "request_id": "partial-put",
        })
        assert put["watch"]["baseline_done"] is False
        page = service.events_read({"after": 0, "limit": 30})
        assert page["events"]
        assert any(item["kind"] == "error" for item in page["events"])
    finally:
        service.close()


def test_large_evidence_is_truncated_and_reported(tmp_path: Path):
    source = tmp_path / "large"
    source.mkdir()
    note = source / "large.md"
    note.write_bytes(b"a" * (observer_module.MAX_EVIDENCE_BYTES + 10))
    with _client(tmp_path, source) as client:
        _renew(client)
        _put(client, "large", note, recursive=False)
        page = _wait_for_events(client, kinds={"baseline"})
        event = page["events"][0]
        assert event["truncated"] is True
        assert len(event["content"].encode("utf-8")) <= observer_module.MAX_EVIDENCE_BYTES
        assert event["size"] == observer_module.MAX_EVIDENCE_BYTES + 10


def test_expired_lease_stops_collection_and_resume_requires_new_lease(tmp_path: Path):
    source = tmp_path / "lease"
    source.mkdir()
    note = source / "lease.txt"
    note.write_bytes(b"before expiry\n")
    with _client(tmp_path, source) as client:
        _put(client, "lease", note)
        _renew(client, ttl=0.2)
        first = _wait_for_events(client, kinds={"baseline"})
        _control(client, "watch_pause", "lease", control=2)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = client.call("status", {})
            if status["health"]["state"] == "lease_expired":
                break
            time.sleep(0.05)
        assert status["health"]["state"] == "lease_expired"
        assert status["health"]["scanning"] is False
        _control(client, "watch_resume", "lease", control=3)
        resumed = client.call("watches_list", {})["watches"][0]
        assert resumed["status"] == "pending_lease"
        note.write_bytes(b"after expiry\n")
        time.sleep(0.35)
        assert _events(client, first["next_cursor"])["events"] == []
        _renew(client, control=4)
        changed = _wait_for_events(client, after=first["next_cursor"], kinds={"modified"})
        assert changed["events"][0]["kind"] == "gap"
        assert any(item["kind"] == "modified" for item in changed["events"])


def test_unstable_read_and_deletion_are_reported(tmp_path: Path, monkeypatch):
    source = tmp_path / "unstable"
    source.mkdir()
    note = source / "note.txt"
    note.write_bytes(b"stable\n")
    service = ObserverService(tmp_path / "data", [str(source)], interval=0.1)
    try:
        service.store.start_collection(1, "2099-01-01T00:00:00.000Z", 30)
        service.watch_put({
            "watch_id": "unstable", "path": str(source), "recursive": False,
            "interval": 0.1, "spec_version": 1, "control_version": 1,
            "request_id": "unstable-put",
        })
        service.scan_watch("unstable", force=True)
        real_read = observer_module._read_stable

        def unstable(path: Path) -> dict:
            if path.name == "note.txt":
                raise observer_module._SourceUnstable("unstable", "metadata kept changing")
            return real_read(path)

        monkeypatch.setattr(observer_module, "_read_stable", unstable)
        service.scan_watch("unstable", force=True)
        page = service.events_read({"after": 0, "limit": 20})
        assert page["events"][-1]["kind"] == "error"
        assert page["events"][-1]["details"]["errors"][0]["reason"] == "unstable"
        monkeypatch.setattr(observer_module, "_read_stable", real_read)
        note.unlink()
        service.scan_watch("unstable", force=True)
        page = service.events_read({"after": 0, "limit": 20})
        assert [item["kind"] for item in page["events"]] == ["baseline", "error", "deleted"]
        assert page["events"][-1]["path"] == str(note.resolve())
    finally:
        service.close()


def test_connect_failure_is_not_swallowed(tmp_path: Path):
    source = tmp_path / "missing-command"
    source.mkdir()
    client = ObserverClient(
        tmp_path / "data", [str(source)], command=["definitely-not-an-observer-xyz"]
    )
    with pytest.raises(ObserverClientError, match="cannot start observer"):
        client.start()
    with pytest.raises(ObserverClientError, match="not started"):
        client.call("status", {})
