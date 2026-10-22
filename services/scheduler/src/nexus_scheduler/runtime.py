"""Scheduler loop and HTTP callback delivery.

The service holds rules; Nexus owns waking the agent. A due rule is only
advanced after Nexus returns a 2xx response for the callback.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from .store import SchedulerStore, now_iso


def _reason(schedule: dict[str, Any]) -> dict[str, Any]:
    purpose = schedule.get("purpose", "task")
    return {
        "type": purpose if purpose in ("task", "wake", "planning") else "task",
        "title": schedule.get("title", ""),
        "schedule_id": schedule.get("id", ""),
        "origin": schedule.get("origin", ""),
        "scheduled_for": schedule.get("next_run_at") or schedule.get("due_at"),
        "prompt": schedule.get("prompt", ""),
        "at": now_iso(),
    }


class SchedulerRuntime:
    def __init__(
        self,
        data_dir,
        *,
        reader_factory: Callable[[], Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.store = SchedulerStore(self.data_dir / "scheduler.sqlite3")
        self._reader_factory = reader_factory
        self._transport = transport
        self._callback_url: str | None = None
        self._callback_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reader = None
        self._reader_lock = threading.Lock()
        self._pages = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scheduler-pages")
        self.error = ""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="nexus-scheduler", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._close_reader()
        self._pages.shutdown(wait=True)

    def _close_reader(self) -> None:
        with self._reader_lock:
            reader, self._reader = self._reader, None
        if reader is not None:
            self._pages.submit(reader.close).result()

    def _read_page(self, url: str) -> str:
        # Creation, reads, and close all run on this single executor thread.
        return self._pages.submit(self._read_on_pages_thread, url).result()

    def _read_on_pages_thread(self, url: str) -> str:
        reader = self._reader
        if reader is None:
            if self._reader_factory is not None:
                reader = self._reader_factory()
            else:
                from .webpage import PageReader

                reader = PageReader(self.data_dir / "browser")
            with self._reader_lock:
                self._reader = reader
        return reader.read(url)

    def state(self) -> dict[str, Any]:
        value = self.store.snapshot()
        value["error"] = self.error
        return value

    def connect(self, callback_url: str) -> dict[str, Any]:
        callback_url = str(callback_url).strip()
        if not callback_url.startswith(("http://", "https://")):
            raise ValueError("callback_url must be an absolute http or https URL")
        with self._callback_lock:
            self._callback_url = callback_url
        return {"connected": True}

    def _post(self, payload: dict[str, Any]) -> bool:
        with self._callback_lock:
            url = self._callback_url
        if not url:
            self.error = "No Nexus callback registered; due work is waiting"
            return False
        try:
            with httpx.Client(timeout=15, transport=self._transport) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
        except Exception as exc:
            self.error = f"Callback failed: {type(exc).__name__}: {exc}"
            return False
        self.error = ""
        return True

    def _deliver(
        self,
        reasons: list[dict[str, Any]],
        *,
        prompt: str = "",
        notify: bool = False,
        kind: str = "wake",
    ) -> bool:
        payload = {
            "source": "scheduler",
            "reasons": reasons,
            "prompt": prompt,
            "notify": bool(notify),
            "kind": kind,
        }
        return self._post(payload)

    def schedule_run(self, schedule_id: str) -> dict[str, Any]:
        schedule = self.store.get_schedule(schedule_id)
        if schedule is None:
            raise ValueError("schedule not found")
        accepted = self._deliver(
            [_reason(schedule)],
            prompt=schedule.get("prompt", ""),
            notify=schedule.get("notify", False),
            kind="manual",
        )
        return {"accepted": accepted}

    def watch_check(self, watch_id: str) -> dict[str, Any]:
        watch = self.store.get_watch(watch_id)
        if watch is None:
            raise ValueError("watch not found")
        try:
            self._check(watch, now_iso(), manual=True)
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            return {"checked": False, "error": str(exc)}
        return {"checked": True, "error": ""}

    def tick(self, now: str | None = None) -> None:
        now = now or now_iso()
        settings = self.store.settings()
        if settings["enabled"]:
            for schedule in self.store.due_schedules(now):
                if self._deliver(
                    [_reason(schedule)],
                    prompt=schedule.get("prompt", ""),
                    notify=schedule.get("notify", False),
                    kind="task" if schedule.get("purpose") == "task" else "wake",
                ):
                    self.store.advance_schedule(
                        schedule["id"], now, expected_next_run_at=schedule["next_run_at"],
                    )
            for watch in self.store.due_watches(now):
                self._check(watch, now)

    def _check(self, watch: dict[str, Any], now: str, *, manual: bool = False) -> None:
        previous = watch.get("last_text")
        try:
            text = self._read_page(watch["url"])
        except Exception as exc:
            self.store.record_watch(watch["id"], None, str(exc), now)
            raise
        if previous is not None and previous != text:
            reason = {
                "type": "web_change", "url": watch["url"], "title": watch["title"],
                "before": previous, "after": text, "at": now,
            }
            if not self._deliver([reason], kind="manual" if manual else "wake"):
                # Keep the old baseline so the next check observes the same change.
                return
        self.store.record_watch(watch["id"], text, "", now)

    def _loop(self) -> None:
        while not self._stop.wait(1):
            try:
                self.tick()
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
