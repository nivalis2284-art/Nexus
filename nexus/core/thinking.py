"""How assistant thinking is (and is not) replayed in each protocol.

Thinking text is always kept for display. A provider may also return a native
continuation that is valid only at its source:

* anthropic_messages: signed ``thinking`` blocks, and opaque
  ``redacted_thinking`` blocks, in their original order;
* openai_chat_completions: ``reasoning_content`` that the response actually
  returned;
* openai_responses: ``reasoning`` items carrying ``encrypted_content``.

``core.replay.capture`` keeps only those blocks. Request builders replay them
only when API family, model, and base URL match exactly. Any other history keeps
the ordinary text and tool representation, so display text is never sent as
unsigned or unencrypted reasoning.
"""

from __future__ import annotations

REPLAY_SUPPORTED = True


def note() -> str:
    return (
        "思考内容用于展示；仅在 API 类型、模型和地址完全一致时，"
        "回传带签名或加密载荷的原生思考块。"
    )
