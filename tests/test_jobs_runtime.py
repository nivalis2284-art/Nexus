"""Regression tests for background job output and process cleanup."""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

import pytest

import nexus.tools._jobs as jobs_module
from nexus.tools._jobs import MAX_BUFFER_CHARS, Job, JobRegistry


def _wait_for(predicate, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.WaitForSingleObject.restype = ctypes.c_uint32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="ascii") as source:
                stat = source.read()
            if stat.rsplit(")", 1)[1].split()[0] == "Z":
                return False
        except (OSError, IndexError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int) -> None:
    if not _pid_alive(pid):
        return
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x0001, False, pid)  # PROCESS_TERMINATE
        if handle:
            kernel32.TerminateProcess(handle, 1)
            kernel32.CloseHandle(handle)
        return
    try:
        os.kill(pid, 9)
    except OSError:
        pass


def test_cursor_is_absolute_across_ring_eviction():
    job = Job(id="job_cursor", argv=["test"])
    job.append("A" * MAX_BUFFER_CHARS)
    chunk, cursor = job.read(0, limit=MAX_BUFFER_CHARS)
    assert len(chunk) == MAX_BUFFER_CHARS
    assert cursor == MAX_BUFFER_CHARS

    job.append("NEW")
    chunk, cursor = job.read(cursor)

    assert chunk == "NEW"
    assert cursor == MAX_BUFFER_CHARS + 3
    assert job.snapshot()["truncated"] is True


def test_cursor_before_evicted_head_resumes_at_oldest_retained_text():
    job = Job(id="job_gap", argv=["test"])
    job.append("OLD")
    _, cursor = job.read(0)
    job.append("B" * MAX_BUFFER_CHARS)

    chunks = []
    while True:
        chunk, cursor = job.read(cursor, limit=50_000)
        if not chunk:
            break
        chunks.append(chunk)

    assert "".join(chunks) == "B" * MAX_BUFFER_CHARS
    assert cursor == 3 + MAX_BUFFER_CHARS


def test_default_read_advances_while_explicit_cursor_is_repeatable():
    job = Job(id="job_default_cursor", argv=["test"])
    job.append("first")

    chunk, cursor = job.read()
    assert chunk == "first"
    assert cursor == 5

    job.append("-second")
    chunk, cursor = job.read()
    assert chunk == "-second"
    assert cursor == 12
    assert job.read() == ("", 12)

    # Passing a cursor explicitly makes the read repeatable and does not
    # move the default position.
    assert job.read(0) == ("first-second", 12)
    assert job.read(0) == ("first-second", 12)
    assert job.read() == ("", 12)


def test_output_without_newlines_is_streamed_and_utf8_is_incremental():
    registry = JobRegistry()
    child_code = (
        "import sys,time; "
        "out=sys.stdout.buffer; "
        "out.write(b'first'); out.flush(); time.sleep(0.3); "
        "out.write(bytes([0xE2])); out.flush(); time.sleep(0.3); "
        "out.write(bytes([0x82,0xAC]) + b'-last'); out.flush(); time.sleep(0.3)"
    )
    try:
        job = registry.start(
            [sys.executable, "-c", child_code], cwd=os.getcwd(), env=os.environ.copy()
        )
        assert job.running
        observed = ""
        cursor = 0
        saw_live_prefix = False
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            chunk, cursor = job.read(cursor)
            observed += chunk
            if "first" in observed and not saw_live_prefix:
                saw_live_prefix = job.running
            if job._done.is_set():
                break
            time.sleep(0.05)
        assert job.wait_closed(2)
        chunk, cursor = job.read(cursor)
        observed += chunk

        assert saw_live_prefix, "unterminated output was not visible before exit"
        assert "first\u20ac-last" in observed
        assert "\ufffd" not in observed
        assert cursor > 0
    finally:
        registry.close_all()


@pytest.mark.parametrize("action", ["kill", "close_all"])
def test_process_tree_is_terminated_and_reader_is_joined(action):
    registry = JobRegistry()
    parent_code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(child.pid, flush=True); "
        "time.sleep(60)"
    )
    child_pid = None
    try:
        job = registry.start(
            [sys.executable, "-c", parent_code], cwd=os.getcwd(), env=os.environ.copy()
        )
        assert job.running
        text = ""
        cursor = 0

        def reported_child():
            nonlocal text, cursor
            chunk, cursor = job.read(cursor)
            text += chunk
            return bool(text.strip())

        assert _wait_for(reported_child), f"child pid was not reported: {text!r}"
        child_pid = int(text.strip().splitlines()[0])
        assert _pid_alive(child_pid)

        if action == "kill":
            assert job.kill() is True
        else:
            registry.close_all()

        assert job.wait_closed(6)
        assert not job.running
        assert not job._pump_thread.is_alive()
        assert _wait_for(lambda: not _pid_alive(child_pid), timeout=6), (
            f"descendant {child_pid} survived {action}"
        )
    finally:
        registry.close_all()
        if child_pid is not None:
            _terminate_pid(child_pid)


def test_natural_parent_exit_still_cleans_descendants_holding_output(tmp_path):
    registry = JobRegistry()
    ready = tmp_path / "child-ready"
    parent_code = (
        "import pathlib,subprocess,sys,time; "
        "ready=pathlib.Path(sys.argv[1]); "
        "code=('import pathlib,sys,time; '"
        "'pathlib.Path(sys.argv[1]).write_text(\"ready\"); time.sleep(60)'); "
        "child=subprocess.Popen([sys.executable,'-c',code,str(ready)]); "
        "deadline=time.monotonic()+5; "
        "exec('while not ready.exists() and time.monotonic() < deadline: time.sleep(0.02)'); "
        "print(child.pid, flush=True)"
    )
    child_pid = None
    try:
        job = registry.start(
            [sys.executable, "-c", parent_code, str(ready)],
            cwd=os.getcwd(),
            env=os.environ.copy(),
        )
        text = ""
        cursor = 0

        def reported_child():
            nonlocal text, cursor
            chunk, cursor = job.read(cursor)
            text += chunk
            return bool(text.strip())

        assert _wait_for(reported_child), f"child pid was not reported: {text!r}"
        child_pid = int(text.strip().splitlines()[0])
        assert ready.exists(), "parent exited without a live child holding its output"

        assert job.wait_closed(6), "parent exit left the reader blocked on inherited output"
        assert not job.running
        assert not job._pump_thread.is_alive()
        assert _wait_for(lambda: not _pid_alive(child_pid), timeout=6), (
            f"descendant {child_pid} survived after its parent exited"
        )
    finally:
        registry.close_all()
        if child_pid is not None:
            _terminate_pid(child_pid)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object setup path")
def test_job_assign_failure_discards_the_suspended_child(monkeypatch):
    events = []

    class FailingScope:
        def assign(self, handle):
            raise OSError("assign failed")

        def terminate(self):
            events.append("terminate")

        def close(self):
            events.append("close")

    real_popen = jobs_module.subprocess.Popen
    children = []

    def recording_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(jobs_module, "JobObject", lambda limits: FailingScope())
    monkeypatch.setattr(jobs_module.subprocess, "Popen", recording_popen)
    registry = JobRegistry()

    job = registry.start(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=os.getcwd()
    )
    try:
        assert job.returncode == -1
        assert job.wait_closed(0)
        assert len(children) == 1
        assert children[0].poll() is not None
        assert children[0].stdout.closed
        assert events == ["terminate", "close"]
        assert not _pid_alive(children[0].pid)
    finally:
        registry.close_all()


def test_close_all_rejects_later_starts():
    registry = JobRegistry()
    registry.close_all()
    with pytest.raises(RuntimeError, match="closed"):
        registry.start([sys.executable, "-c", "pass"], cwd=os.getcwd())


def test_close_all_covers_a_start_in_progress(monkeypatch):
    registry = JobRegistry()
    real_popen = jobs_module.subprocess.Popen
    entered = threading.Event()
    release = threading.Event()
    started = []

    def delayed_popen(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(jobs_module.subprocess, "Popen", delayed_popen)

    def launch():
        started.append(
            registry.start(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                cwd=os.getcwd(),
                env=os.environ.copy(),
            )
        )

    starter = threading.Thread(target=launch)
    closer = threading.Thread(target=registry.close_all)
    try:
        starter.start()
        assert entered.wait(5)
        closer.start()
        time.sleep(0.1)
        assert closer.is_alive(), "close_all returned while a start was in flight"
        release.set()
        starter.join(8)
        closer.join(8)

        assert not starter.is_alive()
        assert not closer.is_alive()
        job = started[0]
        assert job.wait_closed(2)
        assert not job.running
        assert not job._pump_thread.is_alive()
    finally:
        release.set()
        registry.close_all()
