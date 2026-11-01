"""Shared scratch space for Nexus-generated temporary work."""
from pathlib import Path

from .projects import PROJECT_ROOT

CACHE = PROJECT_ROOT / "Cache"


def cache_dir() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE


def process_environment(env: dict[str, str]) -> dict[str, str]:
    folder = str(cache_dir())
    return {**env, "TEMP": folder, "TMP": folder, "TMPDIR": folder}
