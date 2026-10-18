"""Tests for retry policy, abort, and the new tools."""

from __future__ import annotations

import pytest

from nexus.core.retry import RETRYABLE_STATUS, RetryPolicy, parse_retry_after, should_retry
from nexus.tools import build

# ---------------------------------------------------------------- retry policy


def test_backoff_grows_and_is_capped():
    policy = RetryPolicy(jitter_ratio=0.0, initial_delay=0.3, max_delay=2.0)
    assert policy.delay_for(1) == pytest.approx(0.3)
    assert policy.delay_for(2) == pytest.approx(0.6)
    assert policy.delay_for(3) == pytest.approx(1.2)
    # 2.4 exceeds the cap
    assert policy.delay_for(4) == pytest.approx(2.0)
    assert policy.delay_for(20) == pytest.approx(2.0)


def test_jitter_stays_within_bounds():
    policy = RetryPolicy(initial_delay=1.0, max_delay=10.0, jitter_ratio=0.2)
    samples = [policy.delay_for(1) for _ in range(200)]
    assert min(samples) >= 0.8 - 1e-9
    assert max(samples) <= 1.2 + 1e-9
    # It should actually vary, not return a constant.
    assert len(set(round(s, 6) for s in samples)) > 1


def test_retry_after_wins_over_local_backoff():
    policy = RetryPolicy(initial_delay=0.3, max_delay=8.0)
    assert policy.delay_for(1, retry_after=2.0) == pytest.approx(2.0)


def test_retry_after_is_capped():
    policy = RetryPolicy(max_delay=8.0)
    assert policy.delay_for(1, retry_after=999.0) == pytest.approx(8.0)


def test_retry_after_parser():
    assert parse_retry_after("5") == 5.0
    assert parse_retry_after(" 1.5 ") == 1.5
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") is None
    assert parse_retry_after("-1") is None
    assert parse_retry_after("99999") is None


@pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
def test_retryable_statuses_are_retried(status):
    assert should_retry(code="http_error", status=status, reason=None, produced_output=False)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_client_errors_are_not_retried(status):
    assert not should_retry(code="http_error", status=status, reason=None, produced_output=False)


def test_transport_and_timeout_are_retried():
    for code in ("transport_error", "timeout"):
        assert should_retry(code=code, status=None, reason=None, produced_output=False)


def test_malformed_stream_is_not_retried():
    assert not should_retry(
        code="malformed_stream", status=None, reason=None, produced_output=False
    )


def test_nothing_is_retried_after_visible_output():
    """A replay would duplicate text the user already saw."""
    for status in RETRYABLE_STATUS:
        assert not should_retry(
            code="http_error", status=status, reason=None, produced_output=True
        )
    assert not should_retry(
        code="transport_error", status=None, reason=None, produced_output=True
    )


# ---------------------------------------------------------------- abort


def test_agent_stop_sets_the_flag():
    from nexus.core.agent import Agent
    from nexus.core.permission import default_policy
    from nexus.core.workspace import Workspace
    from nexus.model import APIFamily, ModelProfile, RuntimeCredentials
    from pathlib import Path

    registry, context = build({}, config={})
    agent = Agent(
        profile=ModelProfile(
            api_family=APIFamily.OPENAI_CHAT_COMPLETIONS,
            model="m",
            base_url="https://x.test",
        ),
        credentials=RuntimeCredentials("k"),
        registry=registry,
        context=context,
        workspace=Workspace(Path.cwd()),
        policy=default_policy("ask"),
    )
    assert agent.stopped is False
    agent.stop()
    assert agent.stopped is True


# ---------------------------------------------------------------- todo tool


def test_todo_writes_and_summarises():
    from nexus.tools import todo

    registry, context = build({}, config={})
    result = registry.execute(
        "todo_write",
        {
            "todos": [
                {"content": "read the code", "status": "completed"},
                {"content": "write the fix", "status": "in_progress"},
                {"content": "run tests", "status": "pending"},
            ]
        },
        context,
    )
    assert result.ok
    assert context.todos[1]["status"] == "in_progress"
    assert "[x]" in result.content
    assert "1/3 done" in result.content


def test_todo_replaces_the_whole_list():
    registry, context = build({}, config={})
    registry.execute(
        "todo_write", {"todos": [{"content": "a"}, {"content": "b"}]}, context
    )
    registry.execute("todo_write", {"todos": [{"content": "c"}]}, context)
    assert [item["content"] for item in context.todos] == ["c"]


def test_todo_rejects_duplicates():
    registry, context = build({}, config={})
    result = registry.execute(
        "todo_write", {"todos": [{"content": "x"}, {"content": "X"}]}, context
    )
    assert not result.ok


def test_todo_rejects_unknown_status():
    registry, context = build({}, config={})
    result = registry.execute(
        "todo_write", {"todos": [{"content": "x", "status": "done"}]}, context
    )
    assert not result.ok


# ---------------------------------------------------------------- ask_user


def test_ask_user_without_a_channel_fails_loudly():
    registry, context = build({}, config={})
    result = registry.execute(
        "ask_user", {"questions": [{"question": "which?"}]}, context
    )
    assert not result.ok
    assert "declined" in result.content.lower() or "nobody" in result.content.lower()


def test_ask_user_returns_the_answer():
    registry, context = build({}, config={})
    context.ask = lambda questions: ["option-b"]
    result = registry.execute(
        "ask_user", {"questions": [{"question": "which?"}]}, context
    )
    assert result.ok
    assert "option-b" in result.content


def test_ask_user_asks_many_questions_at_once():
    registry, context = build({}, config={})
    seen: list = []

    def asker(questions):
        seen.append(questions)
        return ["red", "blue"]

    context.ask = asker
    result = registry.execute(
        "ask_user",
        {
            "questions": [
                {"question": "first?", "options": ["red", "blue"]},
                {"question": "second?"},
            ]
        },
        context,
    )
    assert result.ok
    # Both questions went to the human in a single call.
    assert len(seen) == 1
    assert len(seen[0]) == 2
    assert result.data["answers"] == ["red", "blue"]
    assert "red" in result.content and "blue" in result.content


def test_ask_user_rejects_mismatched_answer_count():
    registry, context = build({}, config={})
    context.ask = lambda questions: ["only one"]
    result = registry.execute(
        "ask_user",
        {"questions": [{"question": "a?"}, {"question": "b?"}]},
        context,
    )
    assert not result.ok


# ---------------------------------------------------------------- webfetch


def test_webfetch_absent_without_allowlist():
    registry, _ = build({}, config={})
    assert registry.get("webfetch") is None


def test_webfetch_refuses_unlisted_host():
    from nexus.tools import web

    registry, context = build({}, config={"webfetch_hosts": ["example.com"]})
    with pytest.raises(PermissionError) as caught:
        web.fetch(context, {"url": "https://evil.test/x", "max_chars": 1000})
    assert "allowlist" in str(caught.value)


def test_webfetch_refuses_private_addresses():
    """A literal loopback address must never be fetched, listed or not."""
    from nexus.tools import web

    registry, context = build({}, config={"webfetch_hosts": ["localhost", "127.0.0.1"]})
    with pytest.raises(PermissionError):
        web.fetch(context, {"url": "http://127.0.0.1/admin", "max_chars": 1000})


def test_webfetch_refuses_non_http_scheme():
    from nexus.tools import web

    registry, context = build({}, config={"webfetch_hosts": ["example.com"]})
    with pytest.raises(ValueError):
        web.fetch(context, {"url": "file:///etc/passwd", "max_chars": 1000})


def test_html_is_reduced_to_readable_text():
    from nexus.tools import web

    html = "<html><head><style>a{}</style></head><body><p>Hello</p><p>World</p></body></html>"
    text = web._to_text(html.encode(), "text/html; charset=utf-8")
    assert "Hello" in text
    assert "World" in text
    assert "<p>" not in text
    assert "a{}" not in text
