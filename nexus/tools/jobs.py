"""Background job tools: start, inspect, stop.

Split from ``shell.py`` so a foreground command and a background job stay
separate concerns, while both share the same argument-parsing rules.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ._jobs import MAX_WAIT, JobRegistry
from .registry import ToolSpec
from .shell import _argv, _environment


class StartArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: str = Field(min_length=1, max_length=4000)


class OutputArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str = Field(min_length=1, max_length=40)
    wait_seconds: float = Field(default=0.0, ge=0.0, le=MAX_WAIT)
    cursor: int | None = Field(
        default=None, ge=0, description="Opaque position from a previous read."
    )


class KillArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str = Field(min_length=1, max_length=40)


class ListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _registry(context: Any) -> JobRegistry:
    registry = getattr(context, "jobs", None)
    if registry is None:
        registry = JobRegistry()
        context.jobs = registry
    return registry


def start(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    argv = _argv(args["command"])
    job = _registry(context).start(
        argv, cwd=str(context.root), env=_environment()
    )
    # Give very fast commands a moment so a one-shot does not need a second call.
    deadline = time.monotonic() + 0.4
    while job.running and time.monotonic() < deadline:
        time.sleep(0.05)
    chunk, cursor = job.read()
    info = job.snapshot()
    state = "finished" if not info["running"] else "running"
    head = f"$ {' '.join(argv)}\njob: {job.id} ({state}"
    if info["returncode"] is not None:
        head += f", exit {info['returncode']}"
    head += ")"
    body = (head + "\n" + chunk).rstrip()
    return {
        "content": body,
        "job_id": job.id,
        "running": info["running"],
        "cursor": cursor,
        "returncode": info["returncode"],
    }


def output(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    job = _registry(context).get(args["job_id"])
    if job is None:
        raise ValueError(f"No such job: {args['job_id']}")
    wait = args.get("wait_seconds") or 0.0
    if wait and job.running:
        deadline = time.monotonic() + wait
        while job.running and time.monotonic() < deadline:
            time.sleep(0.1)
    chunk, cursor = job.read(args.get("cursor"))
    info = job.snapshot()
    if not chunk:
        chunk = "(no new output)"
    if info["running"]:
        status = "running"
    else:
        status = f"finished (exit {info['returncode']})"
    header = f"job {job.id}: {status}, {info['elapsed']}s"
    return {
        "content": header + "\n" + chunk,
        "job_id": job.id,
        "running": info["running"],
        "cursor": cursor,
        "returncode": info["returncode"],
    }


def kill(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    job = _registry(context).get(args["job_id"])
    if job is None:
        raise ValueError(f"No such job: {args['job_id']}")
    stopped = job.kill()
    return {
        "content": (
            f"job {job.id} {'terminated' if stopped else 'was not running'}"
        ),
        "job_id": job.id,
        "stopped": stopped,
    }


def listing(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    jobs = _registry(context).list()
    if not jobs:
        return {"content": "(no background jobs)", "jobs": []}
    lines = [
        f"- {item['id']}: {'running' if item['running'] else 'done'}"
        f" · {item['elapsed']}s · {' '.join(item['argv'])[:80]}"
        for item in jobs
    ]
    return {"content": "\n".join(lines), "jobs": jobs}


TOOLS = [
    ToolSpec(
        "bash_background",
        "Start a command in the background and return immediately with a job id. "
        "Use this for anything that may run longer than about a minute: installs, "
        "test suites, builds, servers. Read its output with job_output.",
        StartArgs,
        start,
    ),
    ToolSpec(
        "job_output",
        "Read a background job's output. Returns only what is new since the cursor. "
        "Set wait_seconds to block briefly for more output; it returns the moment "
        "the job exits, so it is not a sleep.",
        OutputArgs,
        output,
        read_only=True,
    ),
    ToolSpec(
        "job_kill",
        "Stop a running background job.",
        KillArgs,
        kill,
    ),
    ToolSpec(
        "job_list",
        "List background jobs and whether each is still running.",
        ListArgs,
        listing,
        read_only=True,
    ),
]
