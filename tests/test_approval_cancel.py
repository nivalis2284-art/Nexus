"""Regression: a pending human interaction must never hold the run lock.

Observed in real use: the browser was closed while a question was pending. The
loop stayed parked inside ``Event.wait(600)``, the run lock stayed held, and
every new message got 409 for ten minutes.
"""

from __future__ import annotations

import threading
import time

from nexus.web.server import APPROVAL_TIMEOUT, Approvals


def test_a_pending_wait_can_be_cancelled():
    approvals = Approvals()
    token = approvals.create()
    result: dict = {}

    def waiter():
        mark = time.monotonic()
        result["value"] = approvals.wait(token)
        result["elapsed"] = time.monotonic() - mark

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)
    woken = approvals.cancel_all()
    thread.join(timeout=5)

    assert woken == 1
    assert result["value"] is False, "a cancelled approval must not be granted"
    assert result["elapsed"] < 2, f"waited {result['elapsed']:.1f}s instead of returning"


def test_a_pending_question_is_cancelled_too():
    approvals = Approvals()
    token = approvals.create()
    result: dict = {}

    def waiter():
        result["value"] = approvals.wait_answer(token)

    thread = threading.Thread(target=waiter)
    thread.start()
    time.sleep(0.1)
    approvals.cancel_all()
    thread.join(timeout=5)

    # None means "no usable answer", which the tool turns into a refusal.
    assert result["value"] is None


def test_approval_and_question_are_cancelled_differently():
    """The two shapes must stay distinguishable after a cancel."""
    approvals = Approvals()
    approve_token = approvals.create()
    ask_token = approvals.create()

    results: dict = {}

    threads = [
        threading.Thread(
            target=lambda: results.update(approval=approvals.wait(approve_token))
        ),
        threading.Thread(
            target=lambda: results.update(answer=approvals.wait_answer(ask_token))
        ),
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    approvals.cancel_all()
    for thread in threads:
        thread.join(timeout=5)

    assert results["approval"] is False
    assert results["answer"] is None


def test_cancel_wakes_every_waiter():
    approvals = Approvals()
    tokens = [approvals.create() for _ in range(3)]
    done = []

    threads = [
        threading.Thread(target=lambda t=token: (approvals.wait(t), done.append(t)))
        for token in tokens
    ]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    assert approvals.cancel_all() == 3
    for thread in threads:
        thread.join(timeout=5)
    assert len(done) == 3


def test_cancel_with_nothing_pending_is_harmless():
    approvals = Approvals()
    assert approvals.cancel_all() == 0


def test_cancel_flag_does_not_leak_to_a_later_regular_answer():
    """A cancelled turn must not permanently break the next one."""
    approvals = Approvals()
    approvals.cancel_all()  # nothing pending

    token = approvals.create()
    assert approvals.resolve(token, True) is True
    assert approvals.wait(token, timeout=1) is True


def test_resolve_after_cancel_still_works():
    approvals = Approvals()
    token = approvals.create()
    approvals.cancel_all()
    # A late click on a stale dialog must not raise or hang.
    assert approvals.resolve(token, True) in (True, False)


def test_the_default_timeout_is_still_bounded():
    # The long timeout stays as a backstop; cancellation is what makes it safe.
    assert APPROVAL_TIMEOUT <= 600
