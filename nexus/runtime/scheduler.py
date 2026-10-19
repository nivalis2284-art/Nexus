"""One-shot durable scheduling in the Nexus service process."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from ..task import TaskStore
from .controller import TaskController


class TaskScheduler:
    def __init__(self, store: TaskStore, controller: TaskController, *, interval: float = 1.0) -> None:
        self.store = store
        self.controller = controller
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="nexus-task-scheduler", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=3)

    def _run(self) -> None:
        startup_cutoff = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        while not self._stop.wait(self.interval):
            try:
                # Only schedules that were already overdue when this scheduler
                # started count as missed. A schedule created during this run
                # keeps its requested auto mode.
                self.tick(startup=True, startup_cutoff=startup_cutoff)
                startup_cutoff = ""
            except Exception:
                # One malformed schedule must not stop the scheduler. A later
                # tick retries after the persistent state is repaired.
                continue

    def tick(self, now: datetime | None = None, *, startup: bool = False,
             startup_cutoff: str = "") -> list[str]:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        claimed = []
        for schedule in self.store.due_schedules(current.isoformat(timespec="milliseconds")):
            result = self.store.claim_schedule(
                schedule["task_id"], request_id=f"due-{schedule['task_id']}-{schedule['version']}"
            )
            if result is None:
                continue
            task = self.store.get(schedule["task_id"])
            missed = startup and bool(startup_cutoff) and str(schedule["due_at"]) <= startup_cutoff
            if schedule["mode"] == "confirm" or missed:
                self.store.request_confirmation(
                    task.id, request_id=f"confirm-{schedule['task_id']}-{schedule['version']}",
                    payload={"due_at": schedule["due_at"], "timezone": schedule["timezone"],
                             "reason": "missed_while_stopped" if missed else "due"},
                )
            else:
                self.controller.submit(task.id, request_id=f"auto-{schedule['task_id']}-{schedule['version']}")
            self.store.complete_schedule(task.id)
            claimed.append(task.id)
        return claimed
