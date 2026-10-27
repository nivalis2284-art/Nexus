"""Plugin-owned subscriptions to the application runtime feed."""

from __future__ import annotations

import queue
import threading
import time
from collections import deque
from copy import deepcopy


class ChatSubscription:
    def __init__(self, feed, on_close):
        self._feed = feed
        self._on_close = on_close
        self._condition = threading.Condition()
        self._events = deque()
        self._closed = False
        self._feed.subscribe(self)

    def put(self, event):
        if event is None:
            self.close()
            return
        with self._condition:
            if not self._closed:
                self._events.append(deepcopy(event))
                self._condition.notify()

    def get(self, timeout=None):
        if timeout is not None and timeout < 0:
            raise ValueError('timeout must be non-negative')
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._events and not self._closed:
                if deadline is None:
                    self._condition.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    self._condition.wait(remaining)
            if self._closed:
                return None
            return self._events.popleft()

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._events.clear()
            self._condition.notify_all()
        self._feed.unsubscribe(self)
        self._on_close(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
