"""Shell tool: argument-list execution with resource bounds.

No shell interpolation. The command is parsed into an argv list, so a stray ``|`` or
``&&`` is a literal argument, not a new process pipeline it can smuggle through.
"""

from __future__ import annotations

import locale
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
    """Parse quoted arguments without invoking a shell or expanding operators.

    Both quote styles group text, including --key="a b". Backslashes remain
    literal except before double quotes, where Windows escaping rules apply.
    """
    if any(char in command for char in "\n\r\0"):
        raise ValueError("Command must be a single line.")
    parts, token = [], []
    quote = None
    started = False
    index = 0
    while index < len(command):
        char = command[index]
        if char.isspace() and quote is None:
            if started:
                parts.append("".join(token))
                token, started = [], False
            index += 1
            continue
        started = True
        if char == "\\" and quote != "'":
            end = index
            while end < len(command) and command[end] == "\\":
                end += 1
            count = end - index
            if end < len(command) and command[end] == '"':
                token.append("\\" * (count // 2))
                if count % 2:
                    token.append('"')
                    index = end + 1
                    continue
            else:
                token.append("\\" * count)
                index = end
                continue
            index = end
            char = command[index]
        if char in ("'", '"') and (quote is None or quote == char):
            quote = None if quote else char
        else:
            token.append(char)
        index += 1
    if quote is not None:
        raise ValueError("Cannot parse command: unclosed quote")
    if started:
        parts.append("".join(token))
    if not parts or not parts[0]:
        raise ValueError("Empty command.")
    return parts


def _decode_output(data: bytes) -> str:
    # Choose per line so one native-encoded line cannot corrupt UTF-8 lines.
    encoding = locale.getencoding()
    if os.name == "nt":
        import ctypes
        codepage = ctypes.windll.kernel32.GetConsoleOutputCP()
        if codepage and codepage != 65001:
            encoding = f"cp{codepage}"

    def decode_line(line: bytes) -> str:
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError:
            utf8 = line.decode("utf-8", errors="replace")
        errors = utf8.count("\ufffd")
        # A few invalid/truncated bytes are not evidence that the entire line
        # uses a legacy encoding. Preserve its successfully decoded UTF-8 text.
        valid_bytes = sum(len(char.encode("utf-8")) for char in utf8
                          if ord(char) >= 128 and char != "\ufffd")
        if valid_bytes >= 3 * errors:
            return utf8
        local = line.decode(encoding, errors="replace")
        return local if local.count("\ufffd") < errors else utf8

    return "".join(decode_line(line) for line in data.splitlines(keepends=True))


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
    out = _decode_output(started.stdout)
    err = _decode_output(started.stderr)
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
