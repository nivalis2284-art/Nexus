"""Background job tools: start, inspect, stop.

Split from ``shell.py`` so foreground commands and background jobs share
the same execution setup.
"""

from __future__ import annotations

import time
import os
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..cache import process_environment
from ._jobs import MAX_WAIT, JobRegistry
from .registry import ToolSpec
from .shell import _argv, _environment, _powershell_argv


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


def _start(context: Any, args: dict[str, Any], argv: list[str]) -> dict[str, Any]:
    job = _registry(context).start(
        argv, cwd=str(context.root), env=process_environment(_environment())
    )
    job.display_command = args["command"]
    # Give very fast commands a moment so a one-shot does not need a second call.
    deadline = time.monotonic() + 0.4
    while job.running and time.monotonic() < deadline:
        time.sleep(0.05)
    chunk, cursor = job.read()
    info = job.snapshot()
    state = "finished" if not info["running"] else "running"
    head = f"$ {args['command']}\njob: {job.id} ({state}"
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


def start(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    return _start(context, args, _argv(args["command"]))


def powershell_start(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    return _start(context, args, _powershell_argv(args["command"]))


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
        f" · {item['elapsed']}s · {(item.get('command') or ' '.join(item['argv']))[:80]}"
        for item in jobs
    ]
    return {"content": "\n".join(lines), "jobs": jobs}


TOOLS = [
    ToolSpec(
        "bash_background",
        "Start a command in the background and return immediately with a job id. "
        "Use this for anything that may run longer than about a minute: installs, "
        "test suites, builds, servers. Commands are split into argv without a shell; "
        "on Windows prefer powershell_background for PowerShell commands. "
        "Read its output with job_output.",
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

if os.name == "nt":
    TOOLS.append(ToolSpec(
        "powershell_background",
        "Run a PowerShell command or script in the background in the workspace. "
        "Use Windows PowerShell 5.1 syntax. Returns a job id; use job_output to "
        "read output and job_kill to stop it.",
        StartArgs, powershell_start,
    ))
