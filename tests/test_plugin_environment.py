"""Targeted tests for plugin Python environment preparation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nexus.plugins import environment


def _venv_python(data_dir: Path) -> Path:
    if os.name == "nt":
        return data_dir / "venv" / "Scripts" / "python.exe"
    return data_dir / "venv" / "bin" / "python"


def _stamp(data_dir: Path) -> Path:
    return data_dir / "venv" / environment.STAMP_NAME


class FakePip:
    def __init__(self, data_dir: Path, *, install_error: str | None = None):
        self.data_dir = data_dir
        self.install_error = install_error
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, command, **kwargs):
        command = list(command)
        self.calls.append(command)
        self.kwargs.append(kwargs)
        result = subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["-m", "venv"]:
            python = _venv_python(self.data_dir)
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_bytes(b"")
            return result
        if "install" in command and self.install_error is not None:
            return subprocess.CompletedProcess(command, 1, "", self.install_error)
        return result


def test_plugin_without_dependencies_uses_the_running_interpreter(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"
    runner = FakePip(data_dir)
    monkeypatch.setattr(environment.subprocess, "run", runner)

    python = environment.prepare(plugin_dir, data_dir, {})

    assert python == Path(sys.executable)
    assert runner.calls == []
    assert not (data_dir / "venv").exists()


def test_dependencies_create_a_venv_and_are_installed_without_a_shell(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"
    runner = FakePip(data_dir)
    monkeypatch.setattr(environment.subprocess, "run", runner)
    progress: list[str] = []

    python = environment.prepare(
        plugin_dir,
        data_dir,
        {"dependencies": ["httpx>=0.28", "pydantic>=2.8"]},
        on_progress=progress.append,
    )

    assert python == _venv_python(data_dir)
    assert runner.calls[0][0] == sys.executable
    assert runner.calls[0][1:3] == ["-m", "venv"]
    install = runner.calls[1]
    assert install[:5] == [str(python), "-m", "pip", "install", "--disable-pip-version-check"]
    assert install[5:] == ["httpx>=0.28", "pydantic>=2.8"]
    for call in runner.kwargs:
        assert call["shell"] is False
        assert call["capture_output"] is True
        assert call["text"] is True
        assert call["encoding"] == "utf-8"
    expected_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    assert all(call["creationflags"] == expected_flags for call in runner.kwargs)
    assert progress == [
        "Creating plugin Python environment...",
        "Installing plugin dependencies...",
    ]
    assert json.loads(_stamp(data_dir).read_text(encoding="utf-8")) == {
        "dependencies": ["httpx>=0.28", "pydantic>=2.8"],
        "python_package": None,
    }


def test_matching_stamp_reuses_the_environment(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"
    runner = FakePip(data_dir)
    monkeypatch.setattr(environment.subprocess, "run", runner)
    manifest = {"dependencies": ["httpx>=0.28"]}

    first = environment.prepare(plugin_dir, data_dir, manifest)
    calls_after_first = list(runner.calls)
    progress: list[str] = []
    second = environment.prepare(plugin_dir, data_dir, manifest, on_progress=progress.append)

    assert second == first
    assert runner.calls == calls_after_first
    assert progress == []


def test_changed_dependencies_update_the_environment_and_stamp(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"
    runner = FakePip(data_dir)
    monkeypatch.setattr(environment.subprocess, "run", runner)

    environment.prepare(plugin_dir, data_dir, {"dependencies": ["httpx>=0.28"]})
    environment.prepare(
        plugin_dir,
        data_dir,
        {"dependencies": ["httpx>=0.28", "openpyxl>=3.1"]},
    )

    installs = [call for call in runner.calls if "install" in call]
    assert len(installs) == 2
    assert installs[-1][5:] == ["httpx>=0.28", "openpyxl>=3.1"]
    assert json.loads(_stamp(data_dir).read_text(encoding="utf-8"))["dependencies"] == [
        "httpx>=0.28",
        "openpyxl>=3.1",
    ]


def test_python_package_is_installed_in_editable_mode(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"
    runner = FakePip(data_dir)
    monkeypatch.setattr(environment.subprocess, "run", runner)

    python = environment.prepare(plugin_dir, data_dir, {"python_package": "packages/example"})

    assert python == _venv_python(data_dir)
    install = next(call for call in runner.calls if "install" in call)
    assert install[-2:] == ["-e", str((plugin_dir / "packages" / "example").resolve())]
    assert json.loads(_stamp(data_dir).read_text(encoding="utf-8"))["python_package"] == (
        "packages/example"
    )


def test_absolute_python_package_is_rejected_before_running_anything(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"
    runner = FakePip(data_dir)
    monkeypatch.setattr(environment.subprocess, "run", runner)

    with pytest.raises(ValueError, match="must be relative"):
        environment.prepare(plugin_dir, data_dir, {"python_package": str(tmp_path / "elsewhere")})
    assert runner.calls == []
    assert not _stamp(data_dir).exists()


def test_failed_install_reports_pip_output_and_leaves_no_success_stamp(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"
    good = FakePip(data_dir)
    monkeypatch.setattr(environment.subprocess, "run", good)
    environment.prepare(plugin_dir, data_dir, {"dependencies": ["httpx>=0.28"]})
    assert _stamp(data_dir).exists()

    failing = FakePip(data_dir, install_error="ERROR: Could not find a version that satisfies httpx>=9")
    monkeypatch.setattr(environment.subprocess, "run", failing)
    with pytest.raises(environment.PluginEnvironmentError) as exc_info:
        environment.prepare(plugin_dir, data_dir, {"dependencies": ["httpx>=9"]})

    assert "Could not find a version that satisfies httpx>=9" in str(exc_info.value)
    assert not _stamp(data_dir).exists()
    assert _venv_python(data_dir).exists()


def test_failed_venv_creation_is_reported_without_a_stamp(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    data_dir = tmp_path / "data"

    def failing(command, **kwargs):
        return subprocess.CompletedProcess(list(command), 1, "", "venv creation failed")

    monkeypatch.setattr(environment.subprocess, "run", failing)
    with pytest.raises(environment.PluginEnvironmentError, match="venv creation failed"):
        environment.prepare(plugin_dir, data_dir, {"dependencies": ["httpx>=0.28"]})
    assert not _stamp(data_dir).exists()
