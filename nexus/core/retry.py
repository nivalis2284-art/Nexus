"""Retry policy for model requests.

Modeled on DSH's ``dsh-llm-retry`` (186 lines), keeping what matters and dropping
what does not:

* exponential backoff **with jitter**, capped by ``max_delay``
* prefer the server's own guidance: an HTTP ``Retry-After`` wins over local
  backoff, unless it asks for longer than we are willing to wait
* only retry failures that can plausibly succeed on a second attempt

The one rule DSH does not need and this design does: **never retry after the
stream has produced visible output.** A retry would replay text the user has
already seen. Once anything has been yielded, a failure is final.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# Statuses worth a second attempt. 400/401/403/404/413/422 are excluded on
# purpose: they will fail identically forever, and retrying only wastes the
# user's time while hiding the real problem.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})

# Error codes from the gateway that are transport-level and retryable.
RETRYABLE_CODES = frozenset({"http_error", "transport_error", "timeout"})

# Provider-side codes that mean "busy", not "wrong".
RETRYABLE_REASONS = frozenset(
    {"overloaded", "rate_limit", "server_error", "unavailable", "internal_error"}
)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    initial_delay: float = 0.3
    max_delay: float = 8.0
    jitter_ratio: float = 0.2

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Seconds to wait before retry number ``attempt`` (1-based)."""
        if retry_after is not None and retry_after > 0:
            # The server told us when to come back. Honour it, but never sleep
            # longer than our own ceiling.
            return min(retry_after, self.max_delay)
        exponent = min(attempt - 1, 16)
        base = min(self.initial_delay * (2**exponent), self.max_delay)
        spread = 1.0 - self.jitter_ratio + 2.0 * self.jitter_ratio * random.random()
        return min(base * spread, self.max_delay)


def should_retry(
    *,
    code: str,
    status: int | None,
    reason: str | None,
    produced_output: bool,
) -> bool:
    """Whether this failure is worth another attempt."""
    if produced_output:
        return False
    if status is not None:
        return status in RETRYABLE_STATUS
    if code not in RETRYABLE_CODES:
        return False
    if reason and reason in RETRYABLE_REASONS:
        return True
    # transport_error and timeout carry no reason from this gateway, and both
    # are retryable by nature.
    return code in ("transport_error", "timeout") or reason in RETRYABLE_REASONS
