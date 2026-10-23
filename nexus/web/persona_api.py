"""Local persona editing and an explicitly requested, unsaved summary draft."""

from __future__ import annotations

import json
from typing import Any

from ..core import agent as agent_loop
from ..core.config import build_profile
from ..core.protocol import request_body
from ..model import ModelGateway, RuntimeCredentials
from ..persona import PersonaStore


class SummaryGenerationError(RuntimeError):
    pass


def store(state: dict[str, Any]) -> PersonaStore:
    return PersonaStore(state["store"].home)


async def generate_summary(settings: Any, name: str, body: str, *, request_history=None) -> str:
    if not isinstance(name, str) or not isinstance(body, str):
        raise ValueError("名称和详细正文必须是文字。")
    if not body.strip():
        raise ValueError("请先填写详细正文，再生成摘要建议。")
    if not settings.api_key or not settings.provider.base_url:
        raise ValueError("请先配置主会话模型的 Base URL 和 API Key。")
    profile = build_profile(settings.provider, settings.model)
    payload = request_body(
        profile,
        "根据用户提供的人设条目整理一段简短、可独立理解的中文人设摘要。"
        "保留原文的性格、倾向和条件，不添加新设定。"
        "摘要将直接作为常驻人设正文影响表达与判断，不要写成目录介绍或读取提示。"
        "只输出摘要文本，不输出解释，不修改任何已保存的资料。",
        [{"role": "user", "content": json.dumps({"name": name, "body": body}, ensure_ascii=False)}],
        [],
        effort=settings.effort,
    )
    parts: list[str] = []
    async with agent_loop._client() as client:
        gateway = ModelGateway(client, request_history=request_history,
                               request_scope={"kind": "persona_summary", "owner_id": "persona"})
        async for event in gateway.stream(profile, payload, credentials=RuntimeCredentials(settings.api_key)):
            if event.type == "text_delta" and event.text:
                parts.append(event.text)
            elif event.type == "error":
                raise SummaryGenerationError(
                    event.error.message if event.error else "模型生成失败，请重试。"
                )
    summary = "".join(parts).strip()
    if not summary:
        raise SummaryGenerationError("模型没有返回摘要建议，请重试。")
    return summary
