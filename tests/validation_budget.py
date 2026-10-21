"""Owner-authorized paid validation allowance; unrelated to product quota policy."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class ValidationLease:
    """One paid relay writer per ledger, including across process restarts."""
    def __init__(self, path):
        self.file = Path(str(path) + ".lock").open("a+b")  # noqa: SIM115 -- relay lifetime, released by close()
        try:
            if self.file.tell() == 0:
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise RuntimeError("Another paid relay already owns this round ledger") from error

    def close(self):
        self.file.close()  # The OS releases the lock, including on process exit.


def integer(value):
    return value if type(value) is int and value >= 0 else None


def measured(usage):
    """Return input, cached input, output; missing cache data gets no discount."""
    usage = usage or {}
    inputs = integer(usage.get("input_tokens", usage.get("prompt_tokens")))
    outputs = integer(usage.get("output_tokens", usage.get("completion_tokens")))
    details = usage.get("input_tokens_details", usage.get("prompt_tokens_details")) or {}
    cached = integer(details.get("cached_tokens")) if isinstance(details, dict) else None
    if cached is None:
        cached = integer(usage.get("prompt_cache_hit_tokens"))
    if inputs is None or (cached is not None and cached > inputs):
        return None
    return inputs, cached or 0, outputs


def safe_usage(usage):
    """Preserve native usage counters only, never response text or credentials."""
    result = {}
    for key in ("input_tokens", "output_tokens", "prompt_tokens", "completion_tokens",
                "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        if integer(usage.get(key)) is not None:
            result[key] = usage[key]
    for key in ("input_tokens_details", "prompt_tokens_details", "output_tokens_details",
                "completion_tokens_details"):
        if isinstance(usage.get(key), dict):
            result[key] = {k: v for k, v in usage[key].items() if integer(v) is not None}
    return result


class ValidationBudget:
    """Persist reservations before sending; exact integer tenths avoid rounding."""
    def __init__(self, path: Path, limit=1_000_000):
        self.path = path
        self.lock = threading.RLock()
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "policy": "input-minus-0.9-cached; output-excluded; unknown-reservation-retained",
            "limit": limit, "unit": "weighted_input_tokens", "requests": []}
        if self.data.get("unit") != "weighted_input_tokens" or self.data["limit"] != limit:
            raise ValueError("Cannot reuse a ledger with a different authorization")

    @staticmethod
    def charged_tenths(row):
        value = measured(row.get("usage")) if row.get("complete") and row.get("status") == "200" else None
        return 10 * value[0] - 9 * value[1] if value else 10 * row["reserved"]

    def used(self):
        with self.lock:
            return sum(self.charged_tenths(r) for r in self.data["requests"]) / 10

    def _save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def reserve(self, body):
        # UTF-8 bytes conservatively cover this text/tool fixture's input and
        # an extra 1024 covers provider framing. Output is expressly excluded.
        amount = len(body) + 1024
        with self.lock:
            if self.data.get("blocked_reason"):
                return None
            total = sum(self.charged_tenths(r) for r in self.data["requests"])
            if total + 10 * amount > 10 * self.data["limit"]:
                return None
            row = {"id": len(self.data["requests"]) + 1, "reserved": amount,
                   "at": time.time(), "status": "unknown", "complete": False, "usage": None}
            self.data["requests"].append(row)
            self._save()
            return row

    def settle(self, row, status, usage, complete):
        with self.lock:
            row.update(status=str(status), usage=safe_usage(usage or {}), complete=bool(complete))
            values = measured(row["usage"])
            if values and values[0] > row["reserved"]:
                self.data["blocked_reason"] = "Provider input exceeded preflight estimate; no further paid requests"
            self._save()
