"""Tests for the launcher's virtual environment preparation."""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

import pytest

from nexus import bootstrap


def _completed(
    command: list[str], code: int = 0, *, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, code, stdout, stderr)


def _version(command: list[str]) -> subprocess.CompletedProcess[str] | None:
    if "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" in command:
        return _completed(command, stdout="(3, 12)\n")
    return None


def _base(tmp_path: Path) -> Path:
    python = tmp_path / "python3.12.exe"
    python.write_bytes(b"")
    return python


def _venv_python(root: Path) -> Path:
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"")
    return python


def test_invalid_venv_is_preserved_before_creating_a_replacement(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    old = _venv_python(root)
    old.write_text("old environment", encoding="utf-8")
    base = _base(tmp_path)
    calls: list[list[str]] = []

    def run(command, **kwargs):
        command = list(command)
        calls.append(command)
        if (
            command[0] == str(root / ".venv" / "Scripts" / "python.exe")
            and old.read_bytes() == b"old environment"
        ):
            return _completed(command, 1)
        version = _version(command)
        if version is not None:
            return version
        if "import httpx, pydantic, tzdata" in command:
            return _completed(command, 1)
        if command[-2:] == ["venv", str(root / ".venv")]:
            _venv_python(root)
            return _completed(command)
        return _completed(command)

    result = bootstrap.prepare(root, base, run=run)

    backups = list(root.glob(".venv.invalid-*"))
    assert len(backups) == 1
    assert (backups[0] / "Scripts" / "python.exe").read_text(encoding="utf-8") == "old environment"
    assert result == _venv_python(root)
    assert any(call[-3:-1] == ["-m", "venv"] for call in calls)
    install = next(call for call in calls if "install" in call)
    assert install[1:] == [
        "-m",
        "pip",
        "install",
        "--quiet",
        "--disable-pip-version-check",
        "-e",
        ".",
    ]


def test_base_interpreter_is_used_for_creation(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    base = _base(tmp_path)
    calls: list[list[str]] = []

    def run(command, **kwargs):
        command = list(command)
        calls.append(command)
        if "venv" in command:
            _venv_python(root)
        version = _version(command)
        if version is not None:
            return version
        if "import httpx, pydantic, tzdata" in command:
            return _completed(command)
        return _completed(command)

    bootstrap.prepare(root, base, run=run)
    create = next(call for call in calls if "venv" in call)
    assert create[0] == str(base)


def test_wrong_base_python_is_rejected_without_touching_venv(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    old = _venv_python(root)
    base = _base(tmp_path)

    def run(command, **kwargs):
        command = list(command)
        return _completed(command, 1)

    with pytest.raises(bootstrap.BootstrapError, match="no working Python 3.12"):
        bootstrap.prepare(root, base, run=run)
    assert old.exists()
    assert not list(root.glob(".venv.invalid-*"))


def test_missing_dependencies_are_installed_from_pyproject(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _venv_python(root)
    base = _base(tmp_path)
    calls: list[list[str]] = []

    def run(command, **kwargs):
        command = list(command)
        calls.append(command)
        version = _version(command)
        if version is not None:
            return version
        if "import httpx, pydantic, tzdata" in command:
            return _completed(command, 1, stderr="ModuleNotFoundError")
        return _completed(command)

    bootstrap.prepare(root, base, run=run)
    install = next(call for call in calls if "install" in call)
    assert install[1:] == [
        "-m",
        "pip",
        "install",
        "--quiet",
        "--disable-pip-version-check",
        "-e",
        ".",
    ]


def test_existing_working_venv_does_not_need_a_base_python(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _venv_python(root)
    calls: list[list[str]] = []

    def run(command, **kwargs):
        command = list(command)
        calls.append(command)
        version = _version(command)
        if version is not None:
            return version
        return _completed(command)

    result = bootstrap.prepare(root, None, run=run)
    assert result == root / ".venv" / "Scripts" / "python.exe"
    assert not any("venv" in call for call in calls)


def test_invalid_venv_without_base_python_is_left_untouched(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    python = _venv_python(root)
    python.write_text("broken", encoding="utf-8")

    def run(command, **kwargs):
        return _completed(list(command), 1)

    with pytest.raises(bootstrap.BootstrapError, match="no working Python 3.12"):
        bootstrap.prepare(root, None, run=run)
    assert python.read_text(encoding="utf-8") == "broken"
    assert not list(root.glob(".venv.invalid-*"))


def test_failed_install_keeps_environment_and_reports_error(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _venv_python(root)
    base = _base(tmp_path)

    def run(command, **kwargs):
        command = list(command)
        version = _version(command)
        if version is not None:
            return version
        if "import httpx, pydantic, tzdata" in command:
            return _completed(command, 1)
        if "install" in command:
            return _completed(command, 1, stderr="network is unavailable")
        return _completed(command)

    with pytest.raises(bootstrap.BootstrapError, match="network is unavailable"):
        bootstrap.prepare(root, base, run=run)
    assert (root / ".venv" / "Scripts" / "python.exe").exists()


def test_backup_name_does_not_overwrite_an_earlier_recovery(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    now = dt.datetime(2026, 10, 19, 12, 34, 56)
    first = bootstrap._backup_name(root, now=now)
    first.mkdir()
    second = bootstrap._backup_name(root, now=now)
    assert first.name == ".venv.invalid-20261019-123456"
    assert second.name == ".venv.invalid-20261019-123456-2"


def test_archive_is_refused_when_venv_resolves_outside_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "Scripts").mkdir()
    (elsewhere / "Scripts" / "python.exe").write_bytes(b"")
    try:
        (root / ".venv").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(bootstrap.BootstrapError, match="outside the repository"):
        bootstrap._archive_invalid_venv(root)
    assert elsewhere.is_dir()
    assert not list(root.glob(".venv.invalid-*"))
