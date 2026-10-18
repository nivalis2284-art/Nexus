"""Configuration: one JSON file, no database.

Layout under the data directory (``~/.nexus`` by default)::

    config.json     model profiles and preferences (never credentials)
    credentials.json  api keys, written 0600 (never in config.json)
    sessions/       one JSONL file per conversation
    sessions/<id>.meta.json  title, model, timestamps

Credentials live in a separate file on purpose: the config file is the thing
people copy around and paste into issues.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import APIFamily, ModelCapabilities, ModelProfile

CONFIG_NAME = "config.json"
CREDENTIALS_NAME = "credentials.json"
SESSIONS_DIR = "sessions"


def default_home() -> Path:
    override = os.environ.get("NEXUS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "Nexus"
    return Path.home() / ".nexus"


@dataclass
class Settings:
    """Validated, non-secret settings."""

    profile: ModelProfile
    api_key: str = field(repr=False, default="")
    max_steps: int = 40
    context_limit: int | None = None
    permission: str = "ask"
    workspace: str = ""

    @property
    def context_window(self) -> int:
        limit = self.context_limit or self.profile.context_window_tokens
        return limit or 32_000


DEFAULT_PROFILE = {
    "api_family": APIFamily.ANTHROPIC_MESSAGES.value,
    "model": "deepseek-v4.1-flash",
    "base_url": "https://cf.hetune.top",
    "capabilities": {"tools": True, "streaming": True},
    "context_window_tokens": 65536,
    "max_output_tokens": 8192,
}


class ConfigStore:
    def __init__(self, home: Path | None = None) -> None:
        self.home = Path(home or default_home()).expanduser().resolve()

    @property
    def config_path(self) -> Path:
        return self.home / CONFIG_NAME

    @property
    def credentials_path(self) -> Path:
        return self.home / CREDENTIALS_NAME

    @property
    def sessions_dir(self) -> Path:
        path = self.home / SESSIONS_DIR
        path.mkdir(parents=True, exist_ok=True)
        return path

    def ensure(self) -> Settings:
        """Read settings, creating a usable default config on first run."""
        if not self.config_path.exists():
            self.home.mkdir(parents=True, exist_ok=True)
            self.write_config(
                {
                    "profile": dict(DEFAULT_PROFILE),
                    "max_steps": 40,
                    "permission": "ask",
                    "workspace": os.getcwd(),
                }
            )
        return self.load()

    def read_config(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def write_config(self, value: dict[str, Any]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.config_path)

    def read_credentials(self) -> dict[str, str]:
        try:
            raw = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {key: value for key, value in raw.items() if isinstance(value, str)}

    def write_credential(self, profile_id: str, api_key: str) -> None:
        values = self.read_credentials()
        if api_key:
            values[profile_id] = api_key
        else:
            values.pop(profile_id, None)
        self.home.mkdir(parents=True, exist_ok=True)
        temporary = self.credentials_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        temporary.replace(self.credentials_path)

    def load(self) -> Settings:
        raw = self.read_config()
        profile_raw = raw.get("profile")
        if not isinstance(profile_raw, dict):
            profile_raw = DEFAULT_PROFILE
        merged = {**DEFAULT_PROFILE, **profile_raw}
        capabilities = merged.get("capabilities")
        if isinstance(capabilities, dict):
            merged["capabilities"] = ModelCapabilities(
                **{k: v for k, v in capabilities.items() if k in ModelCapabilities.model_fields}
            )
        profile = ModelProfile(**merged)
        max_steps = raw.get("max_steps")
        if type(max_steps) is not int or not 1 <= max_steps <= 500:
            max_steps = 40
        permission = raw.get("permission")
        if permission not in ("ask", "yolo"):
            permission = "ask"
        context_limit = raw.get("context_limit")
        if type(context_limit) is not int or context_limit <= 0:
            context_limit = None
        workspace = raw.get("workspace")
        if not isinstance(workspace, str) or not workspace:
            workspace = os.getcwd()
        api_key = self.read_credentials().get("default", "")
        return Settings(
            profile=profile,
            api_key=api_key,
            max_steps=max_steps,
            context_limit=context_limit,
            permission=permission,
            workspace=workspace,
        )

    def save_profile(self, profile: ModelProfile) -> None:
        raw = self.read_config()
        raw["profile"] = json.loads(profile.model_dump_json())
        self.write_config(raw)

    def save_preferences(self, **values: Any) -> None:
        raw = self.read_config()
        for key in ("max_steps", "permission", "workspace", "context_limit"):
            if key in values and values[key] is not None:
                raw[key] = values[key]
        self.write_config(raw)
