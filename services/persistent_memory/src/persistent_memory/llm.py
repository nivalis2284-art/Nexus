"""Minimal OpenAI-compatible Chat Completions client used by the engine."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx


class ModelError(RuntimeError):
    """A model response was missing, malformed, or could not be used."""


class ModelResult:
    def __init__(self, value: Any, usage: Any, elapsed_seconds: float) -> None:
        self.value = value
        self.usage = usage
        self.elapsed_seconds = elapsed_seconds


class ChatModel:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: float,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._transport = transport

    def complete_json(self, system: str, user: str) -> ModelResult:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        started = time.monotonic()
        try:
            deadline = started + self.timeout
            with httpx.Client(  # noqa: SIM117 -- the response stream belongs to this client.
                timeout=httpx.Timeout(self.timeout, pool=self.timeout, connect=min(5.0, self.timeout)),
                transport=self._transport,
            ) as client:
                with client.stream("POST", url, headers=headers, json=payload) as response:
                    response.raise_for_status()
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if time.monotonic() > deadline:
                            raise httpx.ReadTimeout("total deadline exceeded", request=response.request)
        except httpx.TimeoutException as exc:
            raise ModelError(f"model request timed out after {self.timeout:g}s") from exc
        except httpx.HTTPStatusError as exc:
            raise ModelError(f"model request failed with HTTP {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise ModelError(f"model request failed: {exc.__class__.__name__}") from exc

        try:
            data = json.loads(bytes(body))
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError("model response has no message content") from exc
        if not isinstance(content, str):
            raise ModelError("model message content is not text")
        usage = data.get("usage")
        return ModelResult(
            parse_json_content(content),
            usage if isinstance(usage, dict) else None,
            round(time.monotonic() - started, 3),
        )


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_json_content(content: str) -> Any:
    """Accept bare JSON or one fenced JSON block, as returned by chat models."""
    text = content.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelError("model returned invalid JSON") from exc
