"""How assistant thinking is (and is not) replayed in each protocol.

Reasoning text is display material. Replaying it is only correct where the
protocol carries it as a first-class block:

* anthropic_messages: a ``thinking`` content block may precede the answer, but
  only when thinking was enabled *and* the original signature is preserved. We
  do not keep signatures, so thinking is shown and then dropped before sending.
* openai_chat_completions: ``reasoning_content`` is an unofficial echo used by
  some compatible servers. Sending it back is not required and can 400.
* openai_responses: the native reasoning item needs an encrypted payload we do
  not retain, so it is not reconstructed.

Conclusion: thinking is persisted for the user and never replayed to the model.
That keeps a hard invariant — what we send is always something we can prove the
provider accepts.
"""

from __future__ import annotations

REPLAY_SUPPORTED = False


def note() -> str:
    return (
        "思考内容仅用于展示，不随请求回传：三种协议都不保证未签名/未加密的"
        "推理文本可被接受。"
    )
