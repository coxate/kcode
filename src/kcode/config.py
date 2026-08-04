from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from kcode.errors import ConfigError

ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class ProviderConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    protocol: Literal["anthropic", "openai"]
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr
    thinking: bool = False


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    active_provider: str
    providers: dict[str, ProviderConfig]

    @property
    def active(self) -> ProviderConfig:
        return self.providers[self.active_provider]


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Cannot read config {path}: {exc}. Check file permissions.") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}. Fix the YAML syntax.") from exc
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"Invalid config {path}: root must be a mapping.")
    providers = value.get("providers", [])
    if not isinstance(providers, list):
        raise ConfigError(f"Invalid config {path}, field 'providers': expected a list.")
    names: set[str] = set()
    for index, provider in enumerate(providers):
        if not isinstance(provider, dict):
            raise ConfigError(
                f"Invalid config {path}, field 'providers[{index}]': expected a mapping."
            )
        name = provider.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(
                f"Invalid config {path}, field 'providers[{index}].name': add a non-empty name."
            )
        if name in names:
            raise ConfigError(
                f"Invalid config {path}, field 'providers': duplicate provider name '{name}'."
            )
        names.add(name)
    return value


def _merge_configs(configs: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    active: str | None = None
    merged: dict[str, dict[str, Any]] = {}
    for _, raw in configs:
        if "active_provider" in raw:
            active = raw["active_provider"]
        for provider in raw.get("providers", []):
            name = provider["name"]
            merged[name] = {**merged.get(name, {}), **provider}
    return {"active_provider": active, "providers": list(merged.values())}


def _expand_key(value: Any, *, path_label: str, environ: Mapping[str, str]) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigError(
            f"Invalid config {path_label}, field 'api_key': use a non-empty value or ${{ENV_VAR}}."
        )
    match = ENV_REFERENCE.fullmatch(value)
    if not match:
        return value
    variable = match.group(1)
    resolved = environ.get(variable)
    if not resolved:
        raise ConfigError(
            f"Invalid config {path_label}, field 'api_key': environment variable "
            f"{variable} is missing. Set it before starting KCode."
        )
    return resolved


def load_config(
    user_path: Path | None = None,
    project_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    """Load user config, then overlay project config provider-by-provider."""
    env = os.environ if environ is None else environ
    candidates = [path for path in (user_path, project_path) if path is not None and path.is_file()]
    if not candidates:
        expected = project_path or user_path or Path(".kcode/config.yaml")
        raise ConfigError(
            f"No KCode config found. Create {expected} from config.example.yaml."
        )
    loaded = [(path, _read_yaml(path)) for path in candidates]
    merged = _merge_configs(loaded)
    source_label = " then ".join(str(path) for path in candidates)
    providers: dict[str, ProviderConfig] = {}
    try:
        for raw in merged["providers"]:
            raw = dict(raw)
            raw["api_key"] = _expand_key(
                raw.get("api_key"), path_label=source_label, environ=env
            )
            parsed = ProviderConfig.model_validate(raw)
            providers[parsed.name] = parsed
        active = merged["active_provider"]
        if not isinstance(active, str) or not active:
            raise ConfigError(
                f"Invalid config {source_label}, field 'active_provider': choose a provider name."
            )
        if active not in providers:
            raise ConfigError(
                f"Invalid config {source_label}, field 'active_provider': provider '{active}' "
                "does not exist."
            )
        return AppConfig(active_provider=active, providers=providers)
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        field = ".".join(str(part) for part in first["loc"])
        raise ConfigError(
            f"Invalid config {source_label}, field '{field}': {first['msg']}. Fix this value."
        ) from None


def default_config_paths(cwd: Path | None = None) -> tuple[Path, Path]:
    return Path.home() / ".kcode" / "config.yaml", (cwd or Path.cwd()) / ".kcode" / "config.yaml"
