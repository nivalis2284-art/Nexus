"""Background jobs: start a process, poll it, stop it.

Needed because a foreground-only ``bash`` makes ordinary work impossible: an
``npm install`` or a test suite that runs for minutes hits the timeout and the
agent is stuck with no way to make progress.

Design points that matter:

* Output is captured into a **bounded ring**, not an unbounded buffer. A chatty
  build must not grow memory without limit.
* Reads are **incremental and cursor-based**: asking twice returns only what is
  new, so polling does not re-send the same megabytes.
* The process runs in its own session/Job Object, so killing the job kills the
  whole tree, including children Windows would otherwise orphan.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from codecs import getincrementaldecoder
from dataclasses import dataclass, field

from ._jobobject import CREATE_SUSPENDED, JobObject, ResourceLimits, resume_process

MAX_BUFFER_CHARS = 200_000
DEFAULT_WAIT = 0.0
MAX_WAIT = 120.0
READ_CHUNK_BYTES = 8192
CLEANUP_TIMEOUT = 5.0


def _discard_start(
    process: subprocess.Popen[bytes], scope: JobObject | None
) -> None:
    """Destroy a child that may still be suspended after setup failed."""
    try:
        process.kill()
    except OSError:
        pass
    if scope is not None:
        scope.terminate()
        scope.close()
    try:
        process.wait(timeout=CLEANUP_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        process.stdout.close()
    except (OSError, ValueError, AttributeError):
        pass


@dataclass
class Job:
    id: str
    argv: list[str]
    started_at: float = field(default_factory=time.monotonic)
    returncode: int | None = None
    _buffer: str = ""
    # Cursors are absolute positions in the job's lifetime output. ``_base``
    # advances when the ring drops its oldest characters, so a cursor remains
    # valid after trimming.
    _base: int = 0
    _cursor: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _process: subprocess.Popen[bytes] | None = None
    _job: JobObject | None = None
    _terminate_lock: threading.Lock = field(default_factory=threading.Lock)
    _cleanup_lock: threading.Lock = field(default_factory=threading.Lock)
    _done: threading.Event = field(default_factory=threading.Event)
    _pump_thread: threading.Thread | None = None
    _reaper_thread: threading.Thread | None = None
    _truncated: bool = False

    @property
    def running(self) -> bool:
        return self.returncode is None

    def append(self, text: str) -> None:
        with self._lock:
            self._buffer += text
            # Keep the tail: the newest output is what the reader needs.
            if len(self._buffer) > MAX_BUFFER_CHARS:
                dropped = len(self._buffer) - MAX_BUFFER_CHARS
                self._buffer = self._buffer[dropped:]
                self._base += dropped
                self._truncated = True

    def read(self, cursor: int | None = None, limit: int = 40_000) -> tuple[str, int]:
        with self._lock:
            end = self._base + len(self._buffer)
            start = self._cursor if cursor is None else cursor
            start = max(self._base, min(start, end))
            offset = start - self._base
            chunk = self._buffer[offset: offset + max(0, limit)]
            next_cursor = start + len(chunk)
            if cursor is None:
                self._cursor = next_cursor
            return chunk, next_cursor

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            total = len(self._buffer)
            truncated = self._truncated
        return {
            "id": self.id,
            "argv": self.argv,
            "running": self.running,
            "returncode": self.returncode,
            "elapsed": round(time.monotonic() - self.started_at, 1),
            "buffered_chars": total,
            "truncated": truncated,
        }

    def _close_tree(self) -> None:
        with self._cleanup_lock:
            scope = self._job
            self._job = None
        if scope is not None:
            scope.close()

    def _terminate_tree(self, process: subprocess.Popen[bytes]) -> None:
        with self._cleanup_lock:
            scope = self._job
            if scope is not None:
                scope.terminate()
                return
        if os.name == "posix":
            try:
                # start_new_session makes the child the leader of its own group.
                os.killpg(process.pid, signal.SIGKILL)
                return
            except OSError:
                pass
        if process.poll() is not None:
            return
        try:
            process.kill()
        except OSError:
            pass

    def kill(self) -> bool:
        process = self._process
        if process is None:
            return False
        with self._terminate_lock:
            was_running = process.poll() is None
            self._terminate_tree(process)
            try:
                process.wait(timeout=CLEANUP_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self._close_tree()
            try:
                process.wait(timeout=CLEANUP_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired):
                pass
        thread = self._pump_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=CLEANUP_TIMEOUT)
        return was_running

    def wait_closed(self, timeout: float = CLEANUP_TIMEOUT) -> bool:
        return self._done.wait(timeout)


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._counter = 0
        self._lock = threading.Lock()
        self._closed = False

    def start(self, argv: list[str], *, cwd: str, env: dict[str, str] | None = None) -> Job:
        with self._lock:
            if self._closed:
                raise RuntimeError("Job registry is closed")
            self._counter += 1
            job_id = f"job_{self._counter:03d}"
            job = Job(id=job_id, argv=argv)
            scope = None
            creationflags = 0
            if os.name == "nt":
                try:
                    scope = JobObject(ResourceLimits())
                except OSError as error:
                    job.returncode = -1
                    job.append(f"failed to start: process tree isolation unavailable: {error}\n")
                    job._done.set()
                    self._jobs[job_id] = job
                    return job
                creationflags = CREATE_SUSPENDED
            try:
                process = subprocess.Popen(
                    argv,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=env,
                    bufsize=0,
                    creationflags=creationflags,
                    start_new_session=(os.name != "nt"),
                )
            except OSError as error:
                if scope is not None:
                    scope.close()
                job.returncode = -1
                job.append(f"failed to start: {error}\n")
                job._done.set()
                self._jobs[job_id] = job
                return job
            job._job = scope
            if scope is not None:
                try:
                    scope.assign(process._handle)
                    resumed = resume_process(process._handle)
                except OSError as error:
                    _discard_start(process, scope)
                    job._job = None
                    job.returncode = -1
                    job.append(f"failed to start: process tree isolation unavailable: {error}\n")
                    job._done.set()
                    self._jobs[job_id] = job
                    return job
                if not resumed:
                    _discard_start(process, scope)
                    job._job = None
                    job.returncode = -1
                    job.append("failed to start: cannot resume process tree\n")
                    job._done.set()
                    self._jobs[job_id] = job
                    return job
            job._process = process
            thread = threading.Thread(target=self._pump, args=(job, process), daemon=True)
            reaper = threading.Thread(target=self._reap, args=(job, process), daemon=True)
            job._pump_thread = thread
            job._reaper_thread = reaper
            self._jobs[job_id] = job
            thread.start()
            reaper.start()
            return job

    def _reap(self, job: Job, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait()
        except OSError:
            pass
        if os.name == "posix":
            # A descendant may hold the pipe after its group leader exited;
            # the group still exists until every member is gone.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        # On Windows, closing the job kills remaining descendants, letting
        # the reader reach EOF and finish.
        job._close_tree()

    def _pump(self, job: Job, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        decoder = getincrementaldecoder("utf-8")(errors="replace")
        read_chunk = getattr(process.stdout, "read1", process.stdout.read)
        try:
            while raw := read_chunk(READ_CHUNK_BYTES):
                text = decoder.decode(raw)
                if text:
                    job.append(text)
            tail = decoder.decode(b"", final=True)
            if tail:
                job.append(tail)
        except (OSError, ValueError):
            pass
        finally:
            try:
                process.stdout.close()
            except (OSError, ValueError):
                pass
        reaper = job._reaper_thread
        if reaper is not None and reaper is not threading.current_thread():
            reaper.join(timeout=CLEANUP_TIMEOUT)
        returncode = -1
        try:
            returncode = process.wait()
        except OSError:
            pass
        job.returncode = returncode
        try:
            job.append(f"\n[exit {returncode}]\n")
        finally:
            job._close_tree()
            job._done.set()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.snapshot() for job in jobs]

    def close_all(self) -> None:
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        for job in jobs:
            job.kill()
        for job in jobs:
            job.wait_closed()
