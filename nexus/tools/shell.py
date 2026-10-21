"""Shell tool: argument-list execution with resource bounds.

No shell interpolation. The model supplies an argv list, so a stray ``|`` or
``&&`` is a literal argument, not a new process pipeline it can smuggle through.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ._jobobject import ResourceLimits
from ._jobobject import available as job_objects_available
from ._process import run_process
from .registry import ToolSpec

DEFAULT_TIMEOUT = 120.0
MAX_TIMEOUT = 900.0
MAX_OUTPUT = 100_000


class BashArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    command: str = Field(min_length=1, max_length=4000, description=(
        'One command-line STRING, e.g. python "script.py" "data.md". '
        'Do not encode an argv list as JSON. Use file tools for reading/listing files.'))
    timeout: float = Field(default=DEFAULT_TIMEOUT, gt=0, le=MAX_TIMEOUT)


def _argv(command: str) -> list[str]:
    """Split into an argument list and refuse shell metacharacters.

    Windows has no reliable POSIX shell here, so a single string is tokenized
    with the platform's own rules when that is unambiguous. Control operators are
    rejected; the model can call the tool twice instead.
    """
    if any(char in command for char in "\n\r\0"):
        raise ValueError("Command must be a single line.")
    import shlex

    try:
        parts = shlex.split(command, posix=False)
    except ValueError as error:
        raise ValueError(f"Cannot parse command: {error}") from error
    cleaned = [part.strip('"') if len(part) >= 2 and part[0] == part[-1] == '"' else part
               for part in parts]
    if not cleaned or not cleaned[0]:
        raise ValueError("Empty command.")
    return cleaned


def bash(context: Any, args: dict[str, Any]) -> dict[str, Any]:
    argv = _argv(args["command"])
    if os.name == "nt" and argv[0].lower() in ("cd", "set", "export", "source"):
        raise ValueError(
            f"'{argv[0]}' is a shell builtin and cannot run without a shell. "
            "Use a real executable, or pass `cwd`-relative paths to file tools."
        )
    limits = None
    if job_objects_available():
        limits = ResourceLimits(pids_limit=64, memory_bytes=2 * 1024**3, cpu_seconds=args["timeout"])
    started = run_process(
        argv,
        timeout=args["timeout"],
        max_output_bytes=MAX_OUTPUT,
        cwd=str(context.root),
        env=_environment(),
        job_limits=limits,
        new_session=os.name != "nt",
        cancel_event=getattr(context, "cancel_event", None),
    )
    out = started.stdout.decode("utf-8", errors="replace")
    err = started.stderr.decode("utf-8", errors="replace")
    parts = [f"$ {' '.join(argv)}", f"exit: {started.returncode}"]
    if out.strip():
        parts.append("stdout:\n" + out.rstrip())
    if err.strip():
        parts.append("stderr:\n" + err.rstrip())
    if started.timed_out:
        parts.append(f"[timed out after {args['timeout']:.0f}s]")
    if started.cancelled:
        parts.append("[cancelled]")
    if started.truncated:
        parts.append("[output truncated]")
    if started.error:
        parts.append(f"[error: {started.error}]")
    return {
        "content": "\n".join(parts),
        "exit_code": started.returncode,
        "ok": started.ok,
        "timed_out": started.timed_out,
    }


def _environment() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("API_KEY", "TOKEN", "SECRET"))}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("NO_COLOR", "1")
    if sys.platform == "win32":
        env.setdefault("TERM", "dumb")
    return env


def which(name: str) -> str | None:
    return shutil.which(name)


TOOLS = [
    ToolSpec(
        "bash",
        "Run a program from a command-line string, split into argv (no shell). "
        "Do not pass a JSON array string. cwd is the workspace root. "
        "Output is captured and truncated at the ends.",
        BashArgs, bash,
    ),
]
