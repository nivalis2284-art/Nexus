"""Prepare the local virtual environment used by ``start.bat``.

This module only uses the standard library so it can run before the project's
runtime dependencies are available.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


class BootstrapError(RuntimeError):
    """An actionable environment setup failure."""


Run = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    command: Sequence[str], *, root: Path, timeout: float = 120
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _venv_python(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "python.exe"


def _is_python_312(
    python: Path,
    *,
    root: Path,
    run: Run,
) -> bool:
    executable = shutil.which(str(python)) if not python.is_file() else str(python)
    if executable is None:
        return False
    try:
        result = run(
            [
                executable,
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)",
            ],
            root=root,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _backup_name(root: Path, *, now: dt.datetime | None = None) -> Path:
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    base = root / f".venv.invalid-{stamp}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = Path(f"{base}-{suffix}")
        suffix += 1
    return candidate


def _archive_invalid_venv(root: Path) -> Path:
    source = root / ".venv"
    target = _backup_name(root)
    try:
        resolved_source = source.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError(f"Could not inspect the existing .venv: {exc}") from exc
    resolved_target = target.resolve()
    if (
        source.name != ".venv"
        or resolved_source.parent.resolve() != root
        or resolved_target.parent.resolve() != root
        or resolved_source == resolved_target
    ):
        raise BootstrapError(
            "Refusing to move an environment outside the repository. Nothing was changed."
        )
    try:
        source.rename(target)
    except OSError as exc:
        raise BootstrapError(
            f"The existing .venv is unusable, but it could not be moved to {target.name}.\n"
            "Nothing was deleted. Close programs using .venv, or rename it yourself, "
            "then run start.bat again."
        ) from exc
    print(f"[prepare] Moved the unusable .venv to {target.name}.")
    return target


def _install_dependencies(python: Path, *, root: Path, run: Run) -> None:
    print("[prepare] Installing dependencies declared by pyproject.toml...")
    try:
        result = run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "-e",
                ".",
            ],
            root=root,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"Could not run pip in .venv: {exc}") from exc
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise BootstrapError(
            "Installing the project dependencies failed. The environment was kept.\n" + details
        )


def _dependencies_import(python: Path, *, root: Path, run: Run) -> bool:
    try:
        result = run(
            [str(python), "-c", "import httpx, pydantic"],
            root=root,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def prepare(
    root: Path,
    base_python: Path | None,
    *,
    run: Run | None = None,
) -> Path:
    """Return a usable 3.12 virtualenv, creating or replacing one as needed."""
    root = root.resolve()
    if base_python is not None:
        resolved = shutil.which(str(base_python))
        if resolved is not None:
            base_python = Path(resolved).resolve()
    runner = run or _run

    python = _venv_python(root)
    venv_exists = (root / ".venv").exists()
    venv_works = _is_python_312(python, root=root, run=runner)
    base_works = False
    if base_python is not None:
        base_works = _is_python_312(base_python, root=root, run=runner)

    if venv_exists and not venv_works and not base_works:
        raise BootstrapError(
            "The existing .venv is unusable and no working Python 3.12 was found.\n"
            "Nothing was changed. Install Python 3.12, then run start.bat again."
        )
    if base_python is not None and not base_works and not venv_works:
        raise BootstrapError(
            f"{base_python} is not a working Python 3.12 interpreter. "
            "Install Python 3.12 and run start.bat again."
        )

    if venv_exists and not venv_works:
        _archive_invalid_venv(root)

    python = _venv_python(root)
    if not _is_python_312(python, root=root, run=runner):
        if base_python is None:
            raise BootstrapError(
                "A usable .venv does not exist, and no Python 3.12 interpreter was supplied."
            )
        print(f"[prepare] Creating .venv with Python 3.12 ({base_python})...")
        try:
            result = runner(
                [str(base_python), "-m", "venv", str(root / ".venv")],
                root=root,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BootstrapError(f"Could not create a virtual environment: {exc}") from exc
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            raise BootstrapError("Creating the virtual environment failed.\n" + details)

    python = _venv_python(root)
    if not _is_python_312(python, root=root, run=runner):
        raise BootstrapError(
            "The newly created .venv does not run Python 3.12. "
            "Check the selected Python installation, then run start.bat again."
        )
    if not _dependencies_import(python, root=root, run=runner):
        _install_dependencies(python, root=root, run=runner)
    return python


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the Nexus virtual environment")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base", type=Path, help="interpreter used to create .venv")
    args = parser.parse_args(argv)
    try:
        python = prepare(args.root, args.base)
    except BootstrapError as exc:
        print(f"[!] {exc}", file=sys.stderr)
        return 1
    print(f"[prepare] Environment ready: {python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
