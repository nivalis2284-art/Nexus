"""Validated task data shared by storage, runtime, HTTP, and the web UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


TASK_STATES = (
    "pending",
    "queued",
    "running",
    "pausing",
    "paused",
    "waiting_user",
    "completed",
    "cancelled",
)
STEP_STATES = ("pending", "in_progress", "completed", "blocked")
VERIFICATION_STATES = ("unverified", "verified", "failed", "unknown")
ACTIVITY_STATES = ("pending", "queued", "running", "pausing", "paused", "waiting_user", "completed", "cancelled")


class TaskError(ValueError):
    """A task request cannot be applied as supplied."""


class TaskConflict(TaskError):
    """The task or arrangement changed since the caller read it."""


class TaskNotFound(TaskError):
    """No persistent task has this identifier."""


class TaskValidationError(TaskError):
    """A field violates the task contract."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _text(value: Any, *, name: str, max_length: int, required: bool = False) -> str:
    if not isinstance(value, str):
        raise TaskValidationError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise TaskValidationError(f"{name} is required")
    if len(value) > max_length:
        raise TaskValidationError(f"{name} is too long (max {max_length})")
    return value


def _count(value: Any, *, name: str) -> int:
    if type(value) is not int or value < 0:
        raise TaskValidationError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class Step:
    """One model-visible piece of work inside a task."""

    content: str
    status: str = "pending"
    verification: str = "unverified"
    evidence: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Any) -> "Step":
        if not isinstance(raw, dict):
            raise TaskValidationError("each step must be an object")
        content = _text(raw.get("content"), name="step.content", max_length=1000, required=True)
        status = raw.get("status", "pending")
        if status not in STEP_STATES:
            raise TaskValidationError(f"invalid step status: {status!r}")
        verification = raw.get("verification", "unverified")
        if verification not in VERIFICATION_STATES:
            raise TaskValidationError(f"invalid verification state: {verification!r}")
        evidence = raw.get("evidence", [])
        if not isinstance(evidence, list) or len(evidence) > 32:
            raise TaskValidationError("step evidence must be a list of up to 32 ids")
        ids: list[str] = []
        for item in evidence:
            if not isinstance(item, str) or not item.strip():
                raise TaskValidationError("step evidence ids must be non-empty strings")
            ids.append(item.strip())
        return cls(content=content, status=status, verification=verification, evidence=tuple(ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "status": self.status,
            "verification": self.verification,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class Evidence:
    """A persisted observation tied to a task, with its verification limits."""

    kind: str
    source: str
    summary: str
    ok: bool | None = None
    target: str = ""
    limitations: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Any) -> "Evidence":
        if not isinstance(raw, dict):
            raise TaskValidationError("evidence must be an object")
        kind = _text(raw.get("kind"), name="evidence.kind", max_length=80, required=True)
        source = _text(raw.get("source"), name="evidence.source", max_length=120, required=True)
        summary = _text(raw.get("summary"), name="evidence.summary", max_length=4000, required=True)
        target = _text(raw.get("target", ""), name="evidence.target", max_length=1000)
        limitations = _text(raw.get("limitations", ""), name="evidence.limitations", max_length=2000)
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            raise TaskValidationError("evidence.metadata must be an object")
        ok = raw.get("ok")
        if ok is not None and type(ok) is not bool:
            raise TaskValidationError("evidence.ok must be boolean or null")
        return cls(kind, source, summary, ok, target, limitations, metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source": self.source,
            "summary": self.summary,
            "ok": self.ok,
            "target": self.target,
            "limitations": self.limitations,
            "metadata": self.metadata,
        }


@dataclass
class Task:
    id: str
    title: str
    goal: str
    constraints: str = ""
    effective_goal: str = ""
    state: str = "pending"
    wait_reason: str = ""
    archived: bool = False
    workspace: str = ""
    provider_id: str = ""
    model_id: str = ""
    effort: str | None = None
    read_only: bool = False
    progress_version: int = 1
    control_version: int = 1
    steps: list[Step] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    blocked: str = ""
    next_step: str = ""
    work_summary: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def active(self) -> bool:
        return not self.archived and self.state not in ("completed", "cancelled")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "goal": self.goal,
            "constraints": self.constraints,
            "effective_goal": self.effective_goal,
            "state": self.state,
            "wait_reason": self.wait_reason,
            "archived": self.archived,
            "workspace": self.workspace,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "effort": self.effort,
            "read_only": self.read_only,
            "progress_version": self.progress_version,
            "control_version": self.control_version,
            "steps": [item.to_dict() for item in self.steps],
            "decisions": self.decisions,
            "evidence": [item.to_dict() for item in self.evidence],
            "blocked": self.blocked,
            "next_step": self.next_step,
            "work_summary": self.work_summary,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def task_from_row(row: Any) -> Task:
    """Build a Task from the flat SQLite columns owned by ``TaskStore``."""
    raw_steps = row["steps_json"]
    raw_evidence = row["evidence_json"]
    import json
    try:
        steps = [Step.from_dict(item) for item in json.loads(raw_steps or "[]")]
        evidence = [Evidence.from_dict(item) for item in json.loads(raw_evidence or "[]")]
        decisions = json.loads(row["decisions_json"] or "[]")
    except (TypeError, ValueError) as error:
        raise TaskValidationError(f"invalid persisted task {row['id']}: {error}") from error
    if not isinstance(decisions, list):
        raise TaskValidationError("persisted decisions must be a list")
    return Task(
        id=row["id"], title=row["title"], goal=row["goal"],
        constraints=row["constraints"], effective_goal=row["effective_goal"],
        state=row["state"], wait_reason=row["wait_reason"],
        archived=bool(row["archived"]), workspace=row["workspace"],
        provider_id=row["provider_id"], model_id=row["model_id"],
        effort=row["effort"], read_only=bool(row["read_only"]),
        progress_version=row["progress_version"],
        control_version=row["control_version"],
        steps=steps, decisions=decisions, evidence=evidence, blocked=row["blocked"],
        next_step=row["next_step"], work_summary=row["work_summary"],
        created_at=row["created_at"], updated_at=row["updated_at"],
    )
