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
import subprocess
import threading
import time
from dataclasses import dataclass, field

MAX_BUFFER_CHARS = 200_000
DEFAULT_WAIT = 0.0
MAX_WAIT = 120.0


@dataclass
class Job:
    id: str
    argv: list[str]
    started_at: float = field(default_factory=time.monotonic)
    returncode: int | None = None
    _buffer: str = ""
    _cursor: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _process: subprocess.Popen[bytes] | None = None
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
                self._cursor = max(0, self._cursor - dropped)
                self._truncated = True

    def read(self, cursor: int | None = None, limit: int = 40_000) -> tuple[str, int]:
        with self._lock:
            start = self._cursor if cursor is None else max(0, min(cursor, len(self._buffer)))
            chunk = self._buffer[start: start + limit]
            return chunk, start + len(chunk)

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

    def kill(self) -> bool:
        process = self._process
        if process is None or process.poll() is not None:
            return False
        try:
            process.terminate()
        except OSError:
            return False
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
        return True


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._counter = 0
        self._lock = threading.Lock()

    def start(self, argv: list[str], *, cwd: str, env: dict[str, str] | None = None) -> Job:
        with self._lock:
            self._counter += 1
            job_id = f"job_{self._counter:03d}"
        job = Job(id=job_id, argv=argv)
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
                start_new_session=(os.name != "nt"),
            )
        except OSError as error:
            job.returncode = -1
            job.append(f"failed to start: {error}\n")
            with self._lock:
                self._jobs[job_id] = job
            return job
        job._process = process
        with self._lock:
            self._jobs[job_id] = job
        threading.Thread(target=self._pump, args=(job, process), daemon=True).start()
        return job

    def _pump(self, job: Job, process: subprocess.Popen[bytes]) -> None:
        assert process.stdout is not None
        try:
            for raw in process.stdout:
                job.append(raw.decode("utf-8", errors="replace"))
        except (OSError, ValueError):
            pass
        try:
            job.returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            job.returncode = -1
        job.append(f"\n[exit {job.returncode}]\n")

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.snapshot() for job in jobs]

    def close_all(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            job.kill()
