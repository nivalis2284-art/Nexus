"""Tools available only inside a persistent task.

These tools write structured facts into the same SQLite task authority the UI
reads. They are injected into ``ToolContext.task_callbacks`` by the runtime, so
ordinary chat does not grow task tools or depend on the task package.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .registry import ToolSpec


class UpdateArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    steps: list[dict[str, Any]] | None = None
    decisions: list[dict[str, Any]] | None = None
    blocked: str | None = Field(default=None, max_length=8000)
    next_step: str | None = Field(default=None, max_length=8000)
    work_summary: str | None = Field(default=None, max_length=8000)


class EvidenceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: str = Field(min_length=1, max_length=80)
    source: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=4000)
    target: str = Field(default="", max_length=1000)
    limitations: str = Field(default="", max_length=2000)
    ok: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FinishArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(min_length=1, max_length=8000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=300)
    unresolved: list[str] = Field(default_factory=list, max_length=50)


class AttemptArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    tool: str = Field(min_length=1, max_length=80)
    error: str = Field(min_length=1, max_length=2000)
    attempt: str = Field(min_length=1, max_length=4000)
    premise: str = Field(default="", max_length=2000)
    new_information: list[str] = Field(default_factory=list, max_length=20)
    excluded: list[str] = Field(default_factory=list, max_length=20)
    next_reason: str = Field(default="", max_length=2000)


class ResolveArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=1, max_length=100)
    conclusion: Literal["succeeded", "failed", "not_applied", "unknown"]
    observation: str = Field(min_length=1, max_length=4000)


def _callbacks(context: Any):
    callbacks = getattr(context, "task_callbacks", None)
    if callbacks is None:
        raise PermissionError("This operation is available only inside a persistent task.")
    return callbacks


def update(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    changes = {key: value for key, value in args.items() if value is not None}
    callbacks = _callbacks(context)
    method = getattr(callbacks, "update_work", None)
    if method is None:
        raise PermissionError("Task progress callback is not connected.")
    changed = method(changes)
    return {"content": "Task progress updated.", **changed}


def evidence(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    callbacks = _callbacks(context)
    method = getattr(callbacks, "add_evidence", None)
    if method is None:
        raise PermissionError("Task evidence callback is not connected.")
    saved = method(args)
    return {"content": "Evidence recorded. This does not by itself complete the task.", **saved}


def finish(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    callbacks = _callbacks(context)
    method = getattr(callbacks, "finish", None)
    if method is None:
        raise PermissionError("Task completion callback is not connected.")
    result = method(args)
    # The callback raises if required work is still open; a success signal is
    # here visible to the model only after the server-side gate passed.
    return {"content": "Task completion accepted with recorded evidence.", **result}


def attempt(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    callbacks = _callbacks(context)
    method = getattr(callbacks, "record_attempt", None)
    if method is None:
        raise PermissionError("Task attempt callback is not connected.")
    result = method(args)
    return {"content": "Attempt recorded for the current task.", **result}


def resolve(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    callbacks = _callbacks(context)
    method = getattr(callbacks, "resolve_operation", None)
    if method is None:
        raise PermissionError("Task operation callback is not connected.")
    result = method(args)
    return {"content": "Operation outcome recorded.", **result}


TOOLS = [
    ToolSpec(
        "task_update",
        "Update the current task's steps, accepted decisions, blocker, next step, or concise working summary. "
        "The complete step list replaces the previous one. Do not use this to rewrite the user's goal.",
        UpdateArgs, update,
    ),
    ToolSpec(
        "task_evidence",
        "Attach an observed result to the current task and state what it does and does not prove. "
        "Actual tool observations are preferred; a user's claim is recorded as user-confirmed.",
        EvidenceArgs, evidence,
    ),
    ToolSpec(
        "task_finish",
        "Declare the whole task complete only after recording evidence covering its agreed goal. "
        "The server rejects completion while required steps, interactions, or unknown operations remain.",
        FinishArgs, finish,
    ),
    ToolSpec(
        "task_attempt",
        "Record a failed or inconclusive attempt, its premise, any genuinely new information, and why a next attempt is justified.",
        AttemptArgs, attempt,
    ),
    ToolSpec(
        "task_resolve_operation",
        "Resolve a previously unknown tool effect after checking the real environment or receiving explicit user confirmation.",
        ResolveArgs, resolve,
    ),
]
