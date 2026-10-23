"""Prepare the isolated Python environment used by a loaded plugin.

The plugin manager calls :func:`prepare` when it loads a plugin. Discovery and
importing plugin metadata must not call it, because dependency installation can
take a long time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

VENV_DIR_NAME = "venv"
STAMP_NAME = "environment.json"

Progress = Callable[[str], None]


class PluginEnvironmentError(RuntimeError):
    """The plugin's Python environment could not be prepared."""


def _dependency_specs(manifest: dict[str, Any]) -> list[str]:
    raw = manifest.get("dependencies")
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise TypeError("manifest.dependencies must be a list of package requirements")
    dependencies = list(raw)
    if any(not isinstance(item, str) or not item.strip() for item in dependencies):
        raise ValueError("manifest.dependencies must contain non-empty strings")
    return dependencies


def _python_package(plugin_dir: Path, manifest: dict[str, Any]) -> tuple[str | None, Path | None]:
    raw = manifest.get("python_package")
    if raw is None:
        return None, None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("manifest.python_package must be a non-empty relative path")
    relative = Path(raw)
    if relative.is_absolute():
        raise ValueError("manifest.python_package must be relative to the plugin directory")
    return raw, (plugin_dir / relative).resolve()


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _run(command: list[str], *, cwd: Path, action: str) -> None:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATIONFLAGS,
        )
    except OSError as exc:
        raise PluginEnvironmentError(f"{action} failed: {exc}") from exc
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        message = f"{action} failed with exit code {result.returncode}."
        if details:
            message = f"{message}\n{details}"
        raise PluginEnvironmentError(message)


def _stamp_path(data_dir: Path) -> Path:
    return data_dir / VENV_DIR_NAME / STAMP_NAME


def _stamp_payload(dependencies: list[str], python_package: str | None) -> dict[str, Any]:
    return {"dependencies": dependencies, "python_package": python_package}


def _read_stamp(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _write_stamp(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise PluginEnvironmentError(f"Saving the plugin dependency stamp failed: {exc}") from exc


def prepare(
    plugin_dir: Path,
    data_dir: Path,
    manifest: dict[str, Any],
    *,
    on_progress: Progress | None = None,
) -> Path:
    """Return the Python executable to use for a plugin.

    Plugins without dependencies or a local Python package use the running
    interpreter. Otherwise ``data_dir/venv`` is created or reused, dependencies
    are installed, and a stamp is written only after a successful install.

    ``on_progress`` receives short status messages suitable for display while
    preparation runs in a background thread.
    """
    plugin_dir = Path(plugin_dir)
    data_dir = Path(data_dir)
    dependencies = _dependency_specs(manifest)
    python_package, package_path = _python_package(plugin_dir, manifest)
    if not dependencies and package_path is None:
        return Path(sys.executable)

    data_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = data_dir / VENV_DIR_NAME
    python = _venv_python(venv_dir)
    stamp_path = _stamp_path(data_dir)
    expected_stamp = _stamp_payload(dependencies, python_package)
    if python.is_file() and _read_stamp(stamp_path) == expected_stamp:
        return python

    # An old stamp does not describe an install that is allowed to fail partway
    # through. Remove it before changing the environment, and restore it only
    # after pip succeeds.
    try:
        stamp_path.unlink(missing_ok=True)
    except OSError as exc:
        raise PluginEnvironmentError(f"Removing the stale plugin dependency stamp failed: {exc}") from exc

    if not python.is_file():
        if on_progress is not None:
            on_progress("Creating plugin Python environment...")
        _run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=data_dir,
            action="Creating the plugin Python environment",
        )
        if not python.is_file():
            raise PluginEnvironmentError(
                f"Creating the plugin Python environment did not produce {python}."
            )

    command = [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    command.extend(dependencies)
    if package_path is not None:
        command.extend(["-e", str(package_path)])
    if on_progress is not None:
        on_progress("Installing plugin dependencies...")
    _run(command, cwd=plugin_dir, action="Installing plugin dependencies")
    _write_stamp(stamp_path, expected_stamp)
    return python
