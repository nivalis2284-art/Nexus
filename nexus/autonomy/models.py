"""The main Agent's proposals, never executable callbacks or user messages."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Goal(Proposal):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_version: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=160)
    question: str = Field(min_length=1, max_length=4000)
    status: Literal["candidate", "active", "waiting", "completed", "abandoned"]
    reason: str = Field(min_length=1, max_length=3000)
    understanding: str = Field(min_length=1, max_length=5000)
    sources: list[str] = Field(min_length=1, max_length=30)


class Plan(Proposal):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_version: int = Field(ge=0)
    goal_id: str
    due_at: str
    reason: str = Field(min_length=1, max_length=2000)
    condition: str = Field(default="", max_length=2000)
    status: Literal["pending", "cancelled", "done"] = "pending"

    @field_validator("due_at")
    @classmethod
    def time_is_explicit(cls, value):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("未来回顾必须带时区")
        return parsed.isoformat()


class Watch(Proposal):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    expected_version: int = Field(ge=0)
    goal_id: str
    path: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    recursive: bool = True
    status: Literal["active", "paused", "stopped"] = "active"


class Action(Proposal):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    goal_id: str
    title: str = Field(min_length=1, max_length=160)
    goal: str = Field(min_length=1, max_length=10000)
    constraints: str = Field(default="", max_length=4000)
    sources: list[str] = Field(min_length=1, max_length=30)


class Contact(Proposal):
    text: str = Field(min_length=1, max_length=8000)
    reason: str = Field(min_length=1, max_length=2000)
    sources: list[str] = Field(min_length=1, max_length=30)


class StopAction(Proposal):
    id: str
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class Decision(Proposal):
    reason: str = Field(min_length=1, max_length=4000)
    goals: list[Goal] = Field(default_factory=list, max_length=6)
    plans: list[Plan] = Field(default_factory=list, max_length=10)
    watches: list[Watch] = Field(default_factory=list, max_length=10)
    actions: list[Action] = Field(default_factory=list, max_length=3)
    cancel_actions: list[StopAction] = Field(default_factory=list, max_length=3)
    message: Contact | None = None


class SourceRead(Proposal):
    source_id: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=8000, ge=1, le=16000)
