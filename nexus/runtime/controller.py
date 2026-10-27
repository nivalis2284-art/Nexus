"""Service-owned work queue: three executions, serialized project writers."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..task import Task, TaskConflict, TaskStore, TaskValidationError


class TaskRunnerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RunFnResult:
    status: str = "complete"
    text: str = ""
    wait_reason: str = ""


RunFn = Callable[[Task, threading.Event], RunFnResult | str | None]


def project_key(workspace: str) -> str:
    root = Path(workspace).expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            root = Path(result.stdout.strip()).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.normcase(str(root))


def needs_project_write(spec):
    # Lifecycle and bookkeeping tools do not modify the shared project.
    return not spec.read_only and spec.name not in {
        "work_start",
        "work_message",
        "work_control",
        "work_model",
        "work_pause_all",
        "task_update",
        "task_evidence",
        "task_finish",
        "task_pause",
        "task_attempt",
        "task_resolve_operation",
        "ask_user",
        "todo",
        "save_instructions",
        "subagent",
        "subagent_list", "subagent_read", "subagent_message", "subagent_control", "subagent_wait", "subagent_model",
        "job_kill",
    }


class TaskController:
    def __init__(self, store: TaskStore, *, run: RunFn | None = None,
                 on_change: Callable[[str], None] | None = None) -> None:
        self.store, self.run = store, run
        self.on_change = on_change
        self._lock = threading.RLock()
        self._change = threading.Condition(self._lock)
        self._queue: deque[str] = deque()
        self._active: dict[str, str] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._writers: dict[str, str] = {}
        self._projects: dict[str, str] = {}
        self._stopping: set[str] = set()
        self._closed = False
        self._workers: list[threading.Thread] = []

    def start(self) -> None:
        with self._change:
            if self._closed:
                raise TaskConflict("work runtime is closed")
            if self._workers:
                return
            for index in range(3):
                worker = threading.Thread(
                    target=self._work, name=f"nexus-work-{index}", daemon=True
                )
                self._workers.append(worker)
                worker.start()

    def _changed(self, task_id: str) -> None:
        relation = self.store.child_relation(task_id)
        if not self._closed and relation and relation["parent_kind"] == "task":
            from .subagents import collect_children
            collect_children(self.store, self, relation["parent_id"])
        if self.on_change is not None:
            self.on_change(task_id)

    @property
    def active_task(self) -> str | None:
        # Retained for consumers that only display a single active reference.
        with self._lock:
            return next(iter(self._active), None)

    def is_active(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._active

    def queued_ids(self) -> list[str]:
        with self._lock:
            return list(self._queue)

    def submit(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        with self._change:
            if self._closed:
                raise TaskConflict("work runtime is closed")
            if self.store.pending_interactions(task_id):
                raise TaskConflict("answer the pending interaction before resuming")
            task = self.store.command(
                task_id, "queued", request_id=request_id, expected_version=expected_version
            )
            self._projects[task_id] = project_key(task.workspace)
            if task_id not in self._queue and task_id not in self._active:
                self._queue.append(task_id)
            self._change.notify_all()
        self._changed(task_id)
        return task

    def resume(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        return self.submit(task_id, request_id=request_id, expected_version=expected_version)

    def _claim(self):
        for task_id in list(self._queue):
            task = self.store.get(task_id)
            if task.state != "queued":
                self._queue.remove(task_id)
                continue
            key = self._projects[task_id]
            if not task.read_only and self._writers.get(key) not in (None, task_id):
                continue
            self._queue.remove(task_id)
            if not task.read_only:
                self._writers[key] = task_id
            run = self.store.begin_run(task_id)
            self._active[task_id] = run["id"]
            cancel = self._cancel_events[task_id] = threading.Event()
            return task, cancel, run["id"]
        return None

    def acquire_write(
        self, owner: str, workspace: str, cancel: threading.Event, *, wait: bool = True
    ) -> None:
        key = project_key(workspace)
        with self._change:
            while self._writers.get(key) not in (None, owner):
                if not wait:
                    raise TaskConflict("project is being modified by another work")
                if cancel.is_set() or self._closed:
                    raise TaskConflict("write cancelled while waiting for project")
                self._change.wait(0.1)
            if cancel.is_set() or self._closed:
                raise TaskConflict("work is stopping")
            self._writers[key] = owner
            if owner in self._active and self.store.get(owner).read_only:
                self.store.update_read_only(owner, False)

    def owns_write(self, owner: str) -> bool:
        with self._lock:
            return owner in self._writers.values()

    def release_write(self, owner: str) -> None:
        with self._change:
            self._writers = {key: value for key, value in self._writers.items() if value != owner}
            self._change.notify_all()

    def _work(self) -> None:
        while True:
            with self._change:
                claimed = None
                while not self._closed and claimed is None:
                    claimed = self._claim()
                    if claimed is None:
                        self._change.wait()
                if self._closed:
                    return
            task, cancel, run_id = claimed
            try:
                self._execute(task, cancel, run_id=run_id)
            except BaseException as error:
                self._record_failure(task.id, error)
                self.store.end_run(run_id, status="failed")
            finally:
                from .subagents import WAITING
                current = self.store.get(task.id)
                waiting = current.state == "waiting_user" or current.wait_reason == WAITING
                if not waiting:
                    self.stop_task_resources(task.id)
                with self._change:
                    self._active.pop(task.id, None)
                    self._cancel_events.pop(task.id, None)
                    current = self.store.get(task.id)
                    if current.state == "pausing" and task.id not in self._stopping:
                        self.store.command(task.id, "paused", request_id=f"stopped-{run_id}")
                    elif current.state == "queued" and not self._closed:
                        if task.id not in self._queue:
                            self._queue.append(task.id)
                    self._change.notify_all()
                jobs = getattr(self.run, "_jobs", {}).get(task.id) if waiting else None
                self.release_when_jobs_finish(task.id, jobs)
                self._changed(task.id)

    def release_when_jobs_finish(self, owner, jobs):
        if not self.owns_write(owner):
            return

        def release():
            if jobs:
                for value in jobs.list():
                    job = jobs.get(str(value["id"]))
                    while job.running:
                        job.wait_closed(0.2)
            with self._change:
                # A resumed work has reclaimed the same owner reference.
                if owner not in self._active and owner not in self._stopping:
                    self.release_write(owner)

        if jobs and any(value["running"] for value in jobs.list()):
            threading.Thread(target=release, name="nexus-work-jobs", daemon=True).start()
        else:
            release()

    def close(self, timeout: float = 8.0) -> None:
        with self._change:
            self._closed = True
            for task_id in list(self._queue):
                if self.store.get(task_id).state == "queued":
                    self.store.command(
                        task_id,
                        "paused",
                        request_id=f"shutdown-{task_id}-{time.time_ns()}",
                        reason="服务已停止；请明确继续",
                    )
            self._queue.clear()
            active = set(self._active) | set(getattr(self.run, "_jobs", {}))
            for event in self._cancel_events.values():
                event.set()
            self._change.notify_all()
        for task_id in active:
            self.stop_task_resources(task_id)
        deadline = time.monotonic() + timeout
        for worker in self._workers:
            if worker is not threading.current_thread():
                worker.join(max(0.0, deadline - time.monotonic()))

    def _execute(self, task: Task, cancel: threading.Event, *, run_id: str) -> None:
        # Queued tasks begin paused in storage; mark strict internal ownership
        # before the runner picks them up so a concurrent start is idempotent.
        current = self.store.get(task.id)
        if current.state not in ("queued", "pending"):
            self.store.end_run(run_id, status="superseded")
            return
        if self.run is None:
            raise TaskRunnerUnavailable("no task runner is configured")
        if current.state == "queued":
            running = self.store.command(
                task.id, "running", request_id=f"claim-{task.id}-{int(time.time_ns())}"
            )
        else:
            running = current
        result = self.run(running, cancel)
        if cancel.is_set():
            if self.store.get(task.id).state == "running":
                self.store.command(
                    task.id, "paused", request_id=f"stop-{task.id}-{int(time.time_ns())}"
                )
            self.store.end_run(run_id, status="suspended")
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
                self.store.add_message(
                    task.id, kind="notice", text="本轮结束，尚未标记任务完成；已挂起，可恢复继续。"
                )
        elif result.status == "paused":
            current = self.store.get(task.id)
            if current.state == "running":
                # A runner's reported pause reason is an error. Model
                # task_pause has already persisted itself with source='agent'.
                source = "error" if result.wait_reason else "user"
                self.store.command(
                    task.id,
                    "paused",
                    request_id=f"pause-{task.id}-{int(time.time_ns())}",
                    reason=result.wait_reason,
                    source=source,
                )
        elif result.status != "stalled":
            raise TaskValidationError(f"unknown task runner result: {result.status}")
        elif result.status == "stalled":
            current = self.store.get(task.id)
            if current.state == "running":
                self.store.command(
                    task.id,
                    "paused",
                    request_id=f"stalled-{task.id}-{int(time.time_ns())}",
                    reason=result.wait_reason,
                    source="error",
                )
        self.store.end_run(run_id, status=result.status)

    def _record_failure(self, task_id: str, error: BaseException) -> None:
        try:
            reason = f"{type(error).__name__}: {error}"
            self.store.add_message(task_id, kind="notice", text=f"任务执行中止：{reason}")
            if self.store.get(task_id).state in ("running", "pausing"):
                self.store.command(
                    task_id,
                    "paused",
                    request_id=f"error-{task_id}-{int(time.time_ns())}",
                    reason=reason,
                    source="error",
                )
        except Exception:
            pass

    def stop_task_resources(self, task_id: str) -> dict[str, Any]:
        with self._change:
            jobs = getattr(self.run, "_jobs", {}).pop(task_id, None)
            if jobs is None:
                return {"stopped_jobs": 0}
            self._stopping.add(task_id)
        stopped = 0
        try:
            snapshots = []
            for value in jobs.list():
                job = jobs.get(str(value["id"]))
                tail, _ = job.read(0, limit=8000) if job else ("", 0)
                stopped += bool(value.get("running"))
                snapshots.append({**value, "tail": tail})
            if callable(getattr(self.run, "record_job_tails", None)):
                self.run.record_job_tails(task_id, snapshots)
            jobs.close_all()
            self.store.mark_jobs_interrupted(task_id)
        finally:
            with self._change:
                self._stopping.discard(task_id)
                if task_id not in self._active:
                    if self.store.get(task_id).state == "pausing":
                        self.store.command(
                            task_id,
                            "paused",
                            request_id=f"resources-stopped-{task_id}-{time.time_ns()}",
                        )
                    self.release_write(task_id)
                self._change.notify_all()
        return {"stopped_jobs": stopped}

    def pause(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        from .subagents import WAITING
        with self._change:
            current = self.store.get(task_id)
            if expected_version is not None and current.control_version != expected_version:
                raise TaskConflict("task control changed; reload before trying again")
            if current.state == "paused" and current.wait_reason == WAITING:
                with self.store._lock, self.store._transaction() as connection:
                    version = current.control_version + 1
                    connection.execute("UPDATE tasks SET wait_reason=?,control_version=? WHERE id=?",
                                       ("Paused by owner; explicit resume required", version, task_id))
                    self.store._advance_control_version(connection, task_id,
                        previous_version=current.control_version, new_version=version, target_changed=False)
                    self.store._event(connection, task_id, "task.control", {
                        "operation": "paused", "state": "paused", "source": "user",
                        "wait_reason": "Paused by owner; explicit resume required"})
                current = self.store.get(task_id)
            if current.state not in ("paused", "pausing"):
                jobs = getattr(self.run, "_jobs", {}).get(task_id)
                has_jobs = jobs is not None and any(value["running"] for value in jobs.list())
                operation = "pausing" if (
                    task_id in self._active and current.state == "running"
                    or has_jobs and current.state == "waiting_user"
                ) else "paused"
                self.store.command(task_id, operation, request_id=request_id, expected_version=expected_version)
                if task_id in self._queue:
                    self._queue.remove(task_id)
                event = self._cancel_events.get(task_id)
                if event:
                    event.set()
                self._change.notify_all()
        # Mark the owner first: child transitions must not reawaken this parent.
        for child in self.store.children(task_id, parent_kind="task"):
            if child["state"] not in ("completed", "cancelled", "paused"):
                self.pause(child["task_id"], request_id=request_id + "-" + child["task_id"])
        self.stop_task_resources(task_id)
        result = self.store.get(task_id)
        if result.state != "pausing":
            self._changed(task_id)
        return result

    def pause_all(self) -> list[Task]:
        from .subagents import WAITING
        return [
            self.pause(task.id, request_id=f"pause-all-{task.id}-{time.time_ns()}")
            for task in self.store.list()
            if task.state in ("pending", "queued", "running", "waiting_user")
            or task.state == "paused" and task.wait_reason == WAITING
        ]

    def cancel(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        with self._change:
            self.store.command(
                task_id, "cancelled", request_id=request_id, expected_version=expected_version
            )
            if task_id in self._queue:
                self._queue.remove(task_id)
            event = self._cancel_events.get(task_id)
            if event:
                event.set()
            self._change.notify_all()
        for child in self.store.children(task_id, parent_kind="task"):
            if child["state"] not in ("completed", "cancelled"):
                self.cancel(child["task_id"], request_id=request_id + "-" + child["task_id"])
        self.stop_task_resources(task_id)
        result = self.store.get(task_id)
        self._changed(task_id)
        return result

    def reopen(self, task_id: str, *, request_id: str, expected_version: int | None = None) -> Task:
        task = self.store.command(
            task_id, "reopen", request_id=request_id, expected_version=expected_version
        )
        self._changed(task_id)
        return task

    def archive(
        self, task_id: str, *, request_id: str, expected_version: int | None = None
    ) -> Task:
        with self._lock:
            if task_id in self._queue or task_id in self._active:
                raise TaskConflict("cannot archive active work")
            task = self.store.command(
                task_id, "archive", request_id=request_id, expected_version=expected_version
            )
        self._changed(task_id)
        return task

    def unarchive(
        self, task_id: str, *, request_id: str, expected_version: int | None = None
    ) -> Task:
        task = self.store.command(
            task_id, "unarchive", request_id=request_id, expected_version=expected_version
        )
        self._changed(task_id)
        return task

    def answer(
        self, task_id: str, interaction_id: str, *, answer: Any, expected_version: int
    ) -> Task:
        interaction = self.store.interaction(interaction_id)
        if interaction["task_id"] != task_id or interaction["kind"] not in ("approval", "question"):
            raise TaskConflict("request belongs to a different owner or action")
        self.store.interaction_answer(
            interaction_id, answer=answer, expected_version=expected_version
        )
        return self.store.get(task_id)
