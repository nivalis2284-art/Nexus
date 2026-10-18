"""Tool registry: declarations are data, execution is one dispatcher.

Design notes carried over from a working system:

* A tool is declared once, with a strict argument model. Unknown arguments are
  rejected rather than ignored, so a model typo surfaces as an error instead of
  silently doing something unintended.
* Every call returns a bounded, structured result. Nothing raises out of the
  dispatcher: a failure is a result the model can read and react to.
* Results are clipped before they reach the transcript. A single ``read`` of a
  huge file must not consume the whole context window.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

MAX_RESULT_CHARS = 40_000
CLIP_HEAD = 24_000
CLIP_TAIL = 14_000


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: type[BaseModel]
    handler: Callable[[Any, dict[str, Any]], dict[str, Any]]
    read_only: bool = False
    needs_approval: bool = False
    # Configuration keys this tool needs to exist. A tool whose requirements are
    # unmet is not registered at all, rather than appearing in the schema and
    # failing when called. Learned from Cordis's `inject`: a plugin whose
    # dependencies are missing never starts.
    requires: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _model_schema(self.parameters),
        }

    def available(self, config: dict[str, Any]) -> bool:
        return all(bool(config.get(key)) for key in self.requires)


def _model_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return schema


@dataclass
class ToolResult:
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok, "content": self.content}
        if self.error_code:
            out["error"] = self.error_code
        if self.data:
            out["data"] = self.data
        return out


def clip(text: str, limit: int = MAX_RESULT_CHARS) -> tuple[str, bool]:
    """Keep head and tail; both ends carry useful information."""
    if len(text) <= limit:
        return text, False
    head = text[:CLIP_HEAD]
    tail = text[-CLIP_TAIL:]
    omitted = len(text) - len(head) - len(tail)
    return (
        f"{head}\n\n... [{omitted} characters truncated] ...\n\n{tail}",
        True,
    )


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def add(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def schema(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any], context: Any) -> ToolResult:
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(False, f"Unknown tool: {name}", error_code="unknown_tool")
        try:
            args = spec.parameters.model_validate(arguments).model_dump()
        except ValidationError as error:
            detail = "; ".join(
                f"{'.'.join(str(p) for p in item['loc'])}: {item['msg']}"
                for item in error.errors()[:5]
            )
            return ToolResult(
                False,
                f"Invalid arguments for {name}: {detail}",
                error_code="invalid_arguments",
            )
        try:
            payload = spec.handler(context, args)
        except PermissionError as error:
            return ToolResult(False, str(error), error_code="denied")
        except FileNotFoundError as error:
            return ToolResult(False, f"Not found: {error}", error_code="not_found")
        except (OSError, ValueError) as error:
            return ToolResult(False, f"{type(error).__name__}: {error}", error_code="failed")
        content = payload.pop("content", "")
        if not isinstance(content, str):
            content = str(content)
        content, truncated = clip(content)
        payload["truncated"] = truncated
        return ToolResult(True, content, data=payload)

    def declarations(self) -> str:
        """Compact tool list for the system prompt (names + one-line meaning)."""
        return "\n".join(f"- {spec.name}: {spec.description}" for spec in self._tools.values())


def json_result(value: dict[str, Any]) -> dict[str, Any]:
    return {"content": json.dumps(value, ensure_ascii=False, indent=1), **value}
