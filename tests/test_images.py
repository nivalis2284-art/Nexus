"""Tests for image support: protocol shapes, limits, and gating."""

from __future__ import annotations

import pytest

from nexus.core.protocol import (
    blocks_to_text,
    request_body,
)
from nexus.model import APIFamily, ModelCapabilities, ModelProfile
from nexus.application import MAX_IMAGE_BYTES, MAX_IMAGES_PER_TURN, read_images as _read_images

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def a_profile(family: APIFamily) -> ModelProfile:
    return ModelProfile(
        api_family=family,
        model="m",
        base_url="https://x.test",
        capabilities=ModelCapabilities(images=True),
    )


def image_message() -> dict:
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image", "mime": "image/png", "data": PNG},
        ],
    }


# ---------------------------------------------------------------- validation


def test_read_images_accepts_a_png():
    images = _read_images([{"mime": "image/png", "data": PNG}])
    assert images[0]["mime"] == "image/png"


def test_read_images_rejects_unsupported_type():
    with pytest.raises(ValueError):
        _read_images([{"mime": "image/tiff", "data": PNG}])


def test_read_images_rejects_missing_data():
    with pytest.raises(ValueError):
        _read_images([{"mime": "image/png", "data": ""}])


def test_read_images_rejects_too_many():
    too_many = [{"mime": "image/png", "data": PNG}] * (MAX_IMAGES_PER_TURN + 1)
    with pytest.raises(ValueError):
        _read_images(too_many)


def test_read_images_rejects_oversized():
    huge = "A" * (MAX_IMAGE_BYTES * 4 // 3 + 100)
    with pytest.raises(ValueError):
        _read_images([{"mime": "image/png", "data": huge}])


def test_read_images_empty_is_fine():
    assert _read_images(None) == []
    assert _read_images([]) == []


# ---------------------------------------------------------------- blocks


def test_blocks_to_text_ignores_images():
    value = [
        {"type": "text", "text": "before "},
        {"type": "image", "mime": "image/png", "data": PNG},
        {"type": "text", "text": "after"},
    ]
    assert blocks_to_text(value) == "before after"


# ---------------------------------------------------------------- protocol


def test_chat_uses_image_url_parts():
    body = request_body(a_profile(APIFamily.OPENAI_CHAT_COMPLETIONS), "sys", [image_message()], [])
    content = body["messages"][-1]["content"]
    assert isinstance(content, list)
    kinds = [part["type"] for part in content]
    assert "image_url" in kinds
    url = next(part for part in content if part["type"] == "image_url")["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")


def test_anthropic_uses_a_base64_source_object():
    body = request_body(a_profile(APIFamily.ANTHROPIC_MESSAGES), "sys", [image_message()], [])
    content = body["messages"][-1]["content"]
    block = next(item for item in content if item["type"] == "image")
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
    assert block["source"]["data"] == PNG


def test_responses_uses_input_image():
    body = request_body(a_profile(APIFamily.OPENAI_RESPONSES), "sys", [image_message()], [])
    item = body["input"][-1]
    kinds = [part["type"] for part in item["content"]]
    assert "input_image" in kinds


def test_text_only_messages_are_unchanged():
    """Adding image support must not alter the plain-string path."""
    plain = [{"role": "user", "content": "just text"}]
    for family in APIFamily:
        body = request_body(a_profile(family), "sys", plain, [])
        assert "just text" in str(body)


@pytest.mark.parametrize("family", list(APIFamily))
def test_text_model_projection_keeps_history_available_for_vision(family):
    vision = a_profile(family)
    profile = vision.model_copy(update={"capabilities": ModelCapabilities(images=False)})
    history = [image_message()]
    body = request_body(profile, "sys", history, [])
    assert PNG not in str(body)
    assert "what is this" in str(body)
    assert "Image data omitted" in str(body)
    assert history[0]["content"][1]["data"] == PNG
    assert PNG in str(request_body(vision, "sys", history, []))


# ---------------------------------------------------------------- estimation


def test_image_does_not_count_base64_as_tokens():
    """A base64 payload is not text; counting its bytes would be wildly wrong."""
    from nexus.core.prompt import estimate, image_tokens, _text_of

    message = image_message()
    text_cost = estimate(_text_of(message))
    base64_cost = estimate(PNG)
    assert text_cost < base64_cost + 20, "image bytes leaked into the text estimate"
    assert image_tokens(message) > 0


def test_text_only_message_has_no_image_cost():
    from nexus.core.prompt import image_tokens

    assert image_tokens({"role": "user", "content": "hi"}) == 0
