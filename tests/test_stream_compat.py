"""Regression tests for compatibility quirks found against a real reasoning endpoint.

Every case here was observed in a live stream, not invented. A stricter parser
rejected a stream the provider considers valid.
"""

from __future__ import annotations

import pytest

from nexus.model._parsers import ChatParser
from nexus.model._sse import ProtocolError


def frame(delta: dict, finish: str | None = None, id_value: str | None = None) -> dict:
    payload: dict = {
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        "usage": None,
    }
    if id_value is not None:
        payload["id"] = id_value
    return payload


def test_empty_opening_id_is_not_a_conflict():
    """The first frame of a reasoning stream may carry id="".

    Observed: ``{"id": "", ... "reasoning_content": "We need"}`` as the opening
    chunk, with the real id arriving several frames later.
    """
    parser = ChatParser()
    parser.feed(frame({"reasoning_content": "We need"}, id_value=""))
    parser.feed(frame({"reasoning_content": " to compare"}, id_value=""))
    events = parser.feed(frame({"content": "done"}, id_value="cmb-1"))
    texts = [e.text for e in events if e.type in ("thinking_delta", "text_delta")]
    assert texts == ["done"]


def test_first_nonempty_id_after_empty_is_accepted():
    parser = ChatParser()
    parser.feed(frame({"content": "a"}, id_value=""))
    parser.feed(frame({"content": "b"}, id_value="cmb-1"))
    parser.feed(frame({"content": "c"}, id_value="cmb-1"))
    assert parser.response.get("id") == "cmb-1"


def test_a_genuinely_changing_id_is_still_rejected():
    parser = ChatParser()
    parser.feed(frame({"content": "a"}, id_value="cmb-1"))
    with pytest.raises(ProtocolError):
        parser.feed(frame({"content": "b"}, id_value="cmb-2"))


def test_stream_without_any_id_still_completes():
    parser = ChatParser()
    parser.feed(frame({"content": "hello"}, id_value=""))
    parser.feed(frame({"content": ""}, finish="stop", id_value=""))
    events = parser.done()
    assert events and events[0].type == "complete"


def test_reasoning_content_streams_as_thinking():
    parser = ChatParser()
    events = []
    events += parser.feed(frame({"reasoning_content": "step one "}, id_value="c"))
    events += parser.feed(frame({"reasoning_content": "step two"}, id_value="c"))
    kinds = [e.type for e in events]
    assert kinds == ["thinking_delta", "thinking_delta"]
    assert events[0].text == "step one "


def test_empty_reasoning_on_finish_frame_is_harmless():
    """The finish frame repeats reasoning_content="" ; that is not new content."""
    parser = ChatParser()
    parser.feed(frame({"reasoning_content": "real thought"}, id_value="c"))
    events = parser.feed(
        frame({"content": "", "reasoning_content": "", "role": "assistant"},
              finish="stop", id_value="c")
    )
    assert [e.type for e in events if e.type == "thinking_delta"] == []


def test_missing_finish_reason_is_still_an_error():
    parser = ChatParser()
    parser.feed(frame({"content": "x"}, id_value="c"))
    with pytest.raises(ProtocolError):
        parser.done()
