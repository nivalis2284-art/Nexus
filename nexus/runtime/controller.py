"""Small serial task executor.

The controller owns the single agent capacity. The HTTP layer only submits
commands and reads durable events; a closed browser cannot terminate a task.
The model-specific work is injected as ``RunFn`` so storage and lifecycle rules
can be verified without a provider.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from ..task import Task, TaskConflict, TaskStore, TaskValidationError


class TaskRunnerUnavailable(RuntimeError):
    """The selected provider/model is missing or cannot be assembled."""


@dataclass(frozen=True)
class RunFnResult:
    status: str = "complete"
    text: str = ""
    wait_reason: str = ""


RunFn = Callable[[Task, threading.Event], RunFnResult | str | None]


class TaskController:
    """Serial queue, one worker, durable state already owned by TaskStore."""

    def __init__(self, store: TaskStore, *, run: RunFn | None = None) -> None:
        self.store = store
        self.run = run
        self._lock = threading.RLock()
        self._change = threading.Condition(self._lock)
        self._queue: deque[str] = deque()
        self._active: str | None = None
        self._active_run: str | None = None
        self._cancel_events: dict[str, threading.Event] = {}
        self._closed = False
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            if self._closed:
                raise TaskValidationError("task controller is closed")
            self._worker = threading.Thread(target=self._work, name="nexus-task-runner", daemon=True)
            self._worker.start()

    def close(self, timeout: float = 8.0) -> None:
        if self._worker is threading.current_thread():
            self._closed = True
            return
        with self._lock:
            self._closed = True
            active = self._active
            if active:
                event = self._cancel_events.get(active)
                if event is not None:
                    event.set()
            self._queue.clear()
            worker = self._worker
            self._change.notify_all()
        if worker is not None:
            worker.join(timeout=max(0.0, timeout))

    @property
    def active_task(self) -> str | None:
        with self._lock:
            return self._active

    def queued_ids(self) -> list[str]:
        with self._lock:
            return list(self._queue)

    def submit(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        task = self.store.command(task_id, "queued", request_id=request_id, expected_version=expected_version)
        self._enqueue(task_id)
        return task

    def _enqueue(self, task_id: str) -> None:
        with self._lock:
            if self._closed or task_id == self._active or task_id in self._queue:
                return
            self._queue.append(task_id)
            self._change.notify_all()

    def _work(self) -> None:
        while True:
            task_id: str | None = None
            with self._lock:
                while not self._closed and not self._queue:
                    self._change.wait(0.5)
                if self._closed:
                    return
                while self._queue:
                    candidate = self._queue.popleft()
                    try:
                        task = self.store.get(candidate)
                    except Exception:
                        continue
                    if task.state == "queued":
                        task_id = candidate
                        break
                if task_id is None:
                    continue
                run = self.store.begin_run(task_id)
                self._active = task_id
                self._active_run = run["id"]
                cancel = threading.Event()
                self._cancel_events[task_id] = cancel
            try:
                self._execute(task, cancel, run_id=run["id"])
            except BaseException as error:  # keep the worker alive after a failed task
                self._record_failure(task_id, error)
                self.store.end_run(run["id"], status="failed")
            finally:
                with self._lock:
                    self._active = None
                    self._active_run = None
                    self._cancel_events.pop(task_id, None)
                    self._change.notify_all()

    def _execute(self, task: Task, cancel: threading.Event, *, run_id: str) -> None:
        # Queued tasks begin paused in storage; mark strict internal ownership
        # before the runner picks them up so a concurrent start is idempotent.
        current = self.store.get(task.id)
        if current.state not in ("queued", "pending"):
            return
        if self.run is None:
            raise TaskRunnerUnavailable("no task runner is configured")
        if current.state == "queued":
            running = self.store.command(task.id, "running", request_id=f"claim-{task.id}-{int(time.time_ns())}")
        else:
            running = current
        result = self.run(running, cancel)
        if cancel.is_set():
            if self.store.get(task.id).state in ("running", "pausing"):
                self.store.command(
                    task.id, "paused", request_id=f"stop-{task.id}-{int(time.time_ns())}"
                )
            return
        if isinstance(result, str):
            result = RunFnResult(status=result)
        elif result is None:
            result = RunFnResult()
        if result.text:
            self.store.add_message(task.id, kind="notice", text=result.text)
        if result.status == "waiting_user":
            current = self.store.get(task.id)
            if current.state == "running":
                self.store.command(
                    task.id, "waiting_user", request_id=f"wait-{task.id}-{int(time.time_ns())}"
                )
        elif result.status == "complete":
            current = self.store.get(task.id)
            if current.state == "running":
                self.store.command(
                    task.id, "paused", request_id=f"complete-{task.id}-{int(time.time_ns())}"
                )
                self.store.add_message(task.id, kind="notice", text="本轮结束；任务完成状态需独立验证。")
        elif result.status == "paused":
            if self.store.get(task.id).state == "running":
                self.store.command(
                    task.id, "paused", request_id=f"pause-{task.id}-{int(time.time_ns())}"
                )
        elif result.status != "stalled":
            raise TaskValidationError(f"unknown task runner result: {result.status}")
        elif result.status == "stalled":
            current = self.store.get(task.id)
            if current.state == "running":
                self.store.command(
                    task.id, "paused", request_id=f"stalled-{task.id}-{int(time.time_ns())}"
                )
        # A waiting_user result is already closed by the model runner's own
        # durable run record. Closing it again with the generic status would
        # hide why the task yielded.
        if result.status != "waiting_user":
            self.store.end_run(run_id, status=result.status)

    def _record_failure(self, task_id: str, error: BaseException) -> None:
        try:
            self.store.add_message(task_id, kind="notice", text=f"任务执行中止：{type(error).__name__}: {error}")
            if self.store.get(task_id).state in ("running", "pausing"):
                self.store.command(
                    task_id, "paused", request_id=f"error-{task_id}-{int(time.time_ns())}"
                )
        except Exception:
            pass

    def stop_task_resources(self, task_id: str) -> dict[str, Any]:
        """Stop only the task's own jobs and retain their terminal snapshots."""
        with self._lock:
            active_run = self._active_run if self._active == task_id else None
            runner = self.run
        jobs = getattr(runner, "_jobs", {}).pop(task_id, None) if runner is not None else None
        if jobs is not None:
            snapshots = []
            stopped = 0
            for value in jobs.list():
                job = jobs.get(str(value["id"]))
                tail = ""
                if job is not None:
                    tail, _ = job.read(0, limit=8_000)
                    if value.get("running"):
                        stopped += 1
                snapshots.append({**value, "tail": tail})
            if callable(getattr(self.run, "record_job_tails", None)):
                self.run.record_job_tails(task_id, snapshots)
            jobs.close_all()
            self.store.mark_jobs_interrupted(task_id)
        else:
            stopped = 0
        if active_run:
            self.store.end_run(active_run, status="suspended")
            self.store.command(
                task_id, "paused",
                request_id=f"suspend-{task_id}-{int(time.time_ns())}",
            )
        return {"stopped_jobs": stopped}

    def _wait_inactive(self, task_id: str, timeout: float = 8.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._active == task_id and time.monotonic() < deadline:
                self._change.wait(min(0.1, max(0.0, deadline - time.monotonic())))
            return self._active != task_id

    def pause(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        """Persist suspension intent immediately, then ask its worker to stop."""
        current = self.store.get(task_id)
        with self._lock:
            is_active = task_id == self._active
            was_queued = task_id in self._queue
            if was_queued:
                self._queue.remove(task_id)
        if is_active and current.state == "running":
            task = self.store.command(task_id, "pausing", request_id=request_id, expected_version=expected_version)
            with self._lock:
                event = self._cancel_events.get(task_id)
                if event is not None:
                    event.set()
            self._wait_inactive(task_id)
            self.stop_task_resources(task_id)
            return task
        self.stop_task_resources(task_id)
        return self.store.command(task_id, "paused", request_id=request_id, expected_version=expected_version)

    def cancel(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        current = self.store.get(task_id)
        with self._lock:
            if task_id in self._queue:
                self._queue.remove(task_id)
            event = self._cancel_events.get(task_id)
            if task_id == self._active and event is not None:
                event.set()
        task = self.store.command(task_id, "cancelled", request_id=request_id, expected_version=expected_version)
        if task_id == self._active:
            self._wait_inactive(task_id)
            self.stop_task_resources(task_id)
        return self.store.get(task_id)

    def resume(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        current = self.store.get(task_id)
        if current.state == "waiting_user" and self.store.pending_interactions(task_id):
            raise TaskConflict("answer the pending interaction before resuming")
        task = self.store.command(
            task_id, "queued", request_id=request_id,
            expected_version=expected_version or current.control_version,
        )
        self._enqueue(task_id)
        return task

    def reopen(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        return self.store.command(task_id, "reopen", request_id=request_id, expected_version=expected_version)

    def archive(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        with self._lock:
            if task_id in self._queue or task_id == self._active:
                raise TaskConflict("cannot archive an active task")
        return self.store.command(task_id, "archive", request_id=request_id, expected_version=expected_version)

    def unarchive(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        return self.store.command(task_id, "unarchive", request_id=request_id, expected_version=expected_version)

    def answer(self, task_id: str, interaction_id: str, *, answer: Any, expected_version: int) -> Task:
        self.store.interaction_answer(interaction_id, answer=answer, expected_version=expected_version)
        task = self.store.get(task_id)
        # A decision releases only its own task. Resume becomes an explicit
        # start/resume request; it does not bypass the persisted authorization.
        if task.state in ("waiting_user",):
            self.store.command(task_id, "paused", request_id=f"answer-{interaction_id}",
                               expected_version=task.control_version)
        return self.store.get(task_id)
