"""Configuration: one JSON file, no database.

Layout under the data directory (``~/.nexus`` by default)::

    config.json        providers, models, the current selection, preferences
    credentials.json   one api key per provider, written 0600
    sessions/          one JSONL file per conversation
    skills/            optional Markdown instruction files

Credentials live in a separate file on purpose: the config file is the thing
people copy around and paste into issues.

A provider is a connection (family + base URL + key). A model belongs to a
provider and carries its own limits and optional per-level wire values.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..model import APIFamily, ModelCapabilities, ModelProfile
from ..file_io import replace_text

_IO_LOCK = threading.RLock()

CONFIG_NAME = "config.json"
CREDENTIALS_NAME = "credentials.json"
SESSIONS_DIR = "sessions"
FAMILIES = tuple(item.value for item in APIFamily)


def default_home() -> Path:
    override = os.environ.get("NEXUS_HOME")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "Nexus"
    return Path.home() / ".nexus"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _normalize_model_window(model: dict[str, Any]) -> dict[str, Any]:
    """Copy a model row and validate its optional per-model context window.

    An empty value removes any previous override so the model inherits the
    default window. The window only affects local budgeting, never the API
    request or the provider's own limit.
    """
    entry = dict(model)
    raw = entry.get("context_window_tokens")
    if raw is None or raw == "":
        entry.pop("context_window_tokens", None)
        return entry
    if type(raw) is not int or raw <= 0:
        raise ValueError("context_window_tokens must be a positive integer")
    return entry


@dataclass
class Provider:
    id: str
    name: str
    family: str
    base_url: str
    models: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "base_url": self.base_url,
            "models": self.models,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Provider":
        models = []
        for item in raw.get("models") or []:
            if not isinstance(item, dict):
                continue
            entry = dict(item)
            # Older configs predate the capabilities block; reasoning is on by
            # default, so add the declaration rather than silently disabling it.
            if not isinstance(entry.get("capabilities"), dict):
                entry["capabilities"] = {"streaming": True, "tools": True, "thinking": True}
            models.append(entry)
        return cls(
            id=str(raw.get("id") or _new_id("prov")),
            name=str(raw.get("name") or "未命名"),
            family=raw.get("family") if raw.get("family") in FAMILIES else FAMILIES[0],
            base_url=str(raw.get("base_url") or ""),
            models=models,
        )


@dataclass
class Settings:
    provider: Provider
    model: dict[str, Any]
    api_key: str = field(repr=False, default="")
    effort: str | None = None
    max_steps: int = 40
    permission: str = "ask"
    workspace: str = ""
    context_limit: int | None = None
    # Empty means "use the built-in rules", so an old config behaves as before.
    permission_rules: list[dict[str, Any]] = field(default_factory=list)
    # Network tools are opt-in; an empty list means webfetch is not registered.
    webfetch_hosts: list[str] = field(default_factory=list)
    search_endpoint: str = ""
    # Raw `mcp.servers` entries. Validated and launched by the caller, kept raw
    # here so a bad entry can be reported instead of silently dropped.
    mcp_servers: list[dict[str, Any]] = field(default_factory=list)

    @property
    def profile(self) -> ModelProfile:
        return build_profile(self.provider, self.model)

    @property
    def context_window(self) -> int:
        return (
            self.context_limit
            or self.model.get("context_window_tokens")
            or 64_000
        )


def build_profile(provider: Provider, model: dict[str, Any]) -> ModelProfile:
    """A validated transport profile. Effort overrides are not part of it.

    ``effort_levels`` must be declared or the gateway refuses to send an effort
    field at all. A model row may narrow the list; the default is every level the
    product offers, because the level vocabulary is ours, not the provider's.
    """
    from .effort import LEVELS

    capabilities = model.get("capabilities")
    caps: ModelCapabilities
    if isinstance(capabilities, dict):
        allowed = ModelCapabilities.model_fields
        caps = ModelCapabilities(**{k: v for k, v in capabilities.items() if k in allowed})
    else:
        caps = ModelCapabilities()
    declared = model.get("effort_levels")
    if isinstance(declared, list) and declared:
        levels = tuple(str(item) for item in declared if isinstance(item, str))
    else:
        levels = LEVELS
    caps = caps.model_copy(update={"effort_levels": levels})
    return ModelProfile(
        api_family=APIFamily(provider.family),
        model=str(model.get("id") or "unknown"),
        base_url=provider.base_url,
        capabilities=caps,
        context_window_tokens=model.get("context_window_tokens") or None,
        max_output_tokens=model.get("max_output_tokens") or None,
    )


DEFAULT_PROVIDER = {
    "name": "DeepSeek",
    "family": APIFamily.ANTHROPIC_MESSAGES.value,
    # No default endpoint is shipped: nobody should inherit someone else's
    # gateway by cloning this repo. First run asks for it in Settings, and a
    # chat turn refuses to start until it is set.
    "base_url": "",
}
DEFAULT_MODEL = {
    "id": "deepseek-v4.1-flash",
    "context_window_tokens": 65536,
    "max_output_tokens": 8192,
    # Reasoning is a first-class feature here; a new model row opts in, and a
    # gateway that rejects the field is then a real error rather than a silent
    # downgrade to a request the model never asked for.
    "capabilities": {"streaming": True, "tools": True, "thinking": True},
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

    # --- raw io ----------------------------------------------------------

    def read_config(self) -> dict[str, Any]:
        with _IO_LOCK:
            try:
                raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            return raw if isinstance(raw, dict) else {}

    def write_config(self, value: dict[str, Any]) -> None:
        with _IO_LOCK:
            self.home.mkdir(parents=True, exist_ok=True)
            replace_text(self.config_path, json.dumps(value, ensure_ascii=False, indent=2))

    def read_credentials(self) -> dict[str, str]:
        with _IO_LOCK:
            try:
                raw = json.loads(self.credentials_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {}
            if not isinstance(raw, dict):
                return {}
            return {k: v for k, v in raw.items() if isinstance(v, str)}

    def write_credential(self, provider_id: str, api_key: str) -> None:
        with _IO_LOCK:
            values = self.read_credentials()
            if api_key:
                values[provider_id] = api_key
            else:
                values.pop(provider_id, None)
            self.home.mkdir(parents=True, exist_ok=True)
            replace_text(self.credentials_path, json.dumps(values, ensure_ascii=False, indent=2),
                         private=True)

    # --- defaults --------------------------------------------------------

    def ensure(self) -> Settings:
        if not self.config_path.exists():
            provider = Provider(
                id=_new_id("prov"),
                name=DEFAULT_PROVIDER["name"],
                family=DEFAULT_PROVIDER["family"],
                base_url=DEFAULT_PROVIDER["base_url"],
                models=[dict(DEFAULT_MODEL)],
            )
            self.write_config(
                {
                    "providers": [provider.to_dict()],
                    "current": {"provider": provider.id, "model": DEFAULT_MODEL["id"]},
                    "effort": "medium",
                    "max_steps": 40,
                    "permission": "ask",
                    "workspace": os.getcwd(),
                }
            )
        return self.load()

    def providers(self) -> list[Provider]:
        raw = self.read_config().get("providers")
        if not isinstance(raw, list):
            return []
        return [Provider.from_dict(item) for item in raw if isinstance(item, dict)]

    def save_providers(self, providers: list[Provider]) -> None:
        raw = self.read_config()
        raw["providers"] = [p.to_dict() for p in providers]
        self.write_config(raw)

    # --- selection -------------------------------------------------------

    def select(self, provider_id: str, model_id: str, effort: str | None = None) -> None:
        raw = self.read_config()
        raw["current"] = {"provider": provider_id, "model": model_id}
        if effort is not None:
            raw["effort"] = effort
        self.write_config(raw)

    def load(self) -> Settings:
        raw = self.read_config()
        providers = self.providers()
        current = raw.get("current") if isinstance(raw.get("current"), dict) else {}
        provider = next(
            (p for p in providers if p.id == current.get("provider")),
            providers[0] if providers else None,
        )
        if provider is None:
            provider = Provider.from_dict(DEFAULT_PROVIDER | {"id": _new_id("prov")})
            provider.models = [dict(DEFAULT_MODEL)]
        model = next(
            (m for m in provider.models if m.get("id") == current.get("model")),
            provider.models[0] if provider.models else dict(DEFAULT_MODEL),
        )
        effort = raw.get("effort")
        if effort is not None and not isinstance(effort, str):
            effort = None
        if isinstance(effort, str):
            from .effort import normalize

            # Persisted "off"/"" mean provider default, never a literal wire value.
            effort = normalize(effort)
        max_steps = raw.get("max_steps")
        if type(max_steps) is not int or not 1 <= max_steps <= 500:
            max_steps = 40
        permission = raw.get("permission")
        if permission not in ("ask", "yolo"):
            permission = "ask"
        workspace = raw.get("workspace")
        if not isinstance(workspace, str) or not workspace:
            workspace = os.getcwd()
        context_limit = raw.get("context_limit")
        if type(context_limit) is not int or context_limit <= 0:
            context_limit = None
        rules = raw.get("permissions")
        if not isinstance(rules, list):
            rules = []
        hosts = raw.get("webfetch_hosts")
        if isinstance(hosts, str):
            hosts = [item.strip() for item in hosts.split(",") if item.strip()]
        if not isinstance(hosts, list):
            hosts = []
        search_endpoint = raw.get("search_endpoint")
        if not isinstance(search_endpoint, str):
            search_endpoint = ""
        mcp_block = raw.get("mcp")
        mcp_servers: list[dict[str, Any]] = []
        if isinstance(mcp_block, dict):
            candidate = mcp_block.get("servers")
            if isinstance(candidate, list):
                mcp_servers = [dict(item) for item in candidate if isinstance(item, dict)]
                secrets = self.read_credentials()
                for entry in mcp_servers:
                    if "mcp:" + str(entry.get("name", "")) in secrets:
                        entry["token"] = secrets["mcp:" + entry["name"]]
        return Settings(
            provider=provider,
            model=model,
            api_key=self.read_credentials().get(provider.id, ""),
            effort=effort,
            max_steps=max_steps,
            permission=permission,
            workspace=workspace,
            context_limit=context_limit,
            permission_rules=[item for item in rules if isinstance(item, dict)],
            webfetch_hosts=[str(item) for item in hosts if item],
            search_endpoint=search_endpoint,
            mcp_servers=mcp_servers,
        )

    def save_preferences(self, **values: Any) -> None:
        raw = self.read_config()
        for key in ("max_steps", "permission", "workspace", "context_limit", "effort"):
            if key in values and values[key] is not None:
                raw[key] = values[key]
        self.write_config(raw)

    # --- mutations used by the settings API ------------------------------

    def add_provider(self, value: dict[str, Any]) -> Provider:
        providers = self.providers()
        family = value.get("family")
        base_url = str(value.get("base_url") or "").strip()
        if family not in FAMILIES:
            raise ValueError(f"unknown api family: {family}")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        models = value.get("models") or []
        if not isinstance(models, list):
            raise ValueError("models must be a list")
        provider = Provider(
            id=_new_id("prov"),
            name=str(value.get("name") or "未命名").strip()[:60],
            family=family,
            base_url=base_url.rstrip("/"),
            models=[
                _normalize_model_window(m)
                for m in models
                if isinstance(m, dict)
            ],
        )
        if not provider.models:
            provider.models = [{"id": str(value.get("model") or "default")}]
        providers.append(provider)
        self.save_providers(providers)
        api_key = value.get("api_key")
        if isinstance(api_key, str) and api_key.strip():
            self.write_credential(provider.id, api_key.strip())
        return provider

    def update_provider(self, provider_id: str, value: dict[str, Any]) -> Provider:
        providers = self.providers()
        target = next((p for p in providers if p.id == provider_id), None)
        if target is None:
            raise ValueError("provider not found")
        if value.get("name") is not None:
            target.name = str(value["name"]).strip()[:60] or target.name
        if value.get("family") is not None:
            if value["family"] not in FAMILIES:
                raise ValueError("unknown api family")
            target.family = value["family"]
        if value.get("base_url") is not None:
            base_url = str(value["base_url"]).strip()
            if not base_url.startswith(("http://", "https://")):
                raise ValueError("base_url must start with http:// or https://")
            target.base_url = base_url.rstrip("/")
        if isinstance(value.get("models"), list):
            target.models = [
                _normalize_model_window(m) for m in value["models"] if isinstance(m, dict)
            ]
        self.save_providers(providers)
        api_key = value.get("api_key")
        if isinstance(api_key, str):
            self.write_credential(provider_id, api_key.strip())
        return target

    def remove_provider(self, provider_id: str) -> None:
        providers = [p for p in self.providers() if p.id != provider_id]
        if not providers:
            raise ValueError("at least one provider is required")
        self.save_providers(providers)
        self.write_credential(provider_id, "")
        raw = self.read_config()
        current = raw.get("current") if isinstance(raw.get("current"), dict) else {}
        if current.get("provider") == provider_id:
            first = providers[0]
            raw["current"] = {
                "provider": first.id,
                "model": first.models[0].get("id") if first.models else "",
            }
            self.write_config(raw)

    def has_key(self, provider_id: str) -> bool:
        return bool(self.read_credentials().get(provider_id))
