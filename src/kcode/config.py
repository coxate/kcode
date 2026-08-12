from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from kcode.errors import ConfigError

ENV_REFERENCE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
ENV_INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class StdioMcpServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    source: Literal["user", "project"]
    type: Literal["stdio"]
    command: str = Field(min_length=1)
    args: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)


class HttpMcpServerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    source: Literal["user", "project"]
    type: Literal["http"]
    url: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("use an absolute http or https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL must not contain a username or password")
        return value


McpServerConfig = Annotated[
    StdioMcpServerConfig | HttpMcpServerConfig,
    Field(discriminator="type"),
]
MCP_SERVER_ADAPTER = TypeAdapter(McpServerConfig)


class MissingMcpEnvironment(ValueError):
    def __init__(self, variables: tuple[str, ...]) -> None:
        super().__init__("missing MCP environment variables: " + ", ".join(variables))
        self.variables = variables


class ProviderConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    protocol: Literal["anthropic", "openai"]
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr
    thinking: bool = False
    context_window: int | None = Field(default=None, ge=1)


class AgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_iterations: int = Field(default=10, ge=1, le=100)
    max_parallel_tools: int = Field(default=4, ge=1, le=16)


class MemoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False


class SubAgentConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    background_enabled: bool = True
    auto_background_seconds: float = Field(default=120.0, ge=0.1, le=3600.0)
    max_running: int = Field(default=4, ge=1, le=16)
    max_retained: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def retained_covers_running(self) -> SubAgentConfig:
        if self.max_retained < self.max_running:
            raise ValueError("max_retained must be greater than or equal to max_running")
        return self


class TeamConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    max_members: int = Field(default=3, ge=1, le=3)


class AppConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    active_provider: str
    providers: dict[str, ProviderConfig]
    agent: AgentConfig = AgentConfig()
    memory: MemoryConfig = MemoryConfig()
    memory_warnings: tuple[str, ...] = ()
    subagents: SubAgentConfig = SubAgentConfig()
    subagent_warnings: tuple[str, ...] = ()
    teams: TeamConfig = TeamConfig()
    team_warnings: tuple[str, ...] = ()
    mcp_servers: tuple[McpServerConfig, ...] = ()
    mcp_warnings: tuple[str, ...] = ()

    @property
    def active(self) -> ProviderConfig:
        return self.providers[self.active_provider]


def _read_yaml(path: Path, *, source: Literal["user", "project"] = "user") -> dict[str, Any]:
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
    agent = value.get("agent", {})
    if not isinstance(agent, dict):
        raise ConfigError(f"Invalid config {path}, field 'agent': expected a mapping.")
    mcp_servers = value.get("mcp_servers", {})
    if not isinstance(mcp_servers, dict):
        value = dict(value)
        value["mcp_servers"] = {}
        value["_mcp_warnings"] = [
            f"KCode ignored invalid MCP settings at {path}: 'mcp_servers' must be a mapping."
        ]
    memory = value.get("memory", {})
    if not isinstance(memory, dict):
        raise ConfigError(f"Invalid config {path}, field 'memory': expected a mapping.")
    subagents = value.get("subagents", {})
    if not isinstance(subagents, dict):
        raise ConfigError(f"Invalid config {path}, field 'subagents': expected a mapping.")
    teams = value.get("teams", {})
    if not isinstance(teams, dict):
        if source == "project":
            value = dict(value)
            value["teams"] = {}
            value["_team_warnings"] = [
                f"KCode ignored invalid project teams settings from {path}; "
                "configure Agent Teams in ~/.kcode/config.yaml."
            ]
        else:
            raise ConfigError(f"Invalid config {path}, field 'teams': expected a mapping.")
    return value


def _merge_configs(
    configs: list[tuple[Path, dict[str, Any], Literal["user", "project"]]],
) -> dict[str, Any]:
    active: str | None = None
    merged: dict[str, dict[str, Any]] = {}
    agent: dict[str, Any] = {}
    mcp_servers: dict[str, tuple[Literal["user", "project"], Any]] = {}
    mcp_warnings: list[str] = []
    memory: dict[str, Any] = {}
    memory_warnings: list[str] = []
    subagents: dict[str, Any] = {}
    subagent_warnings: list[str] = []
    teams: dict[str, Any] = {}
    team_warnings: list[str] = []
    for path, raw, source in configs:
        if "active_provider" in raw:
            active = raw["active_provider"]
        agent.update(raw.get("agent", {}))
        for provider in raw.get("providers", []):
            name = provider["name"]
            merged[name] = {**merged.get(name, {}), **provider}
        for name, server in raw.get("mcp_servers", {}).items():
            mcp_servers[name] = (source, server)
        mcp_warnings.extend(raw.get("_mcp_warnings", ()))
        raw_memory = raw.get("memory", {})
        if source == "user":
            memory.update(raw_memory)
        elif raw_memory.get("enabled") is False:
            memory["enabled"] = False
        elif raw_memory.get("enabled") is True and not memory.get("enabled", False):
            memory_warnings.append(
                f"KCode ignored memory.enabled: true from project config {path}; "
                "enable long-term memory in ~/.kcode/config.yaml after reviewing its cost "
                "and plaintext-storage notice."
            )
        raw_subagents = raw.get("subagents", {})
        if source == "user":
            subagents.update(raw_subagents)
        elif raw_subagents:
            subagent_warnings.append(
                f"KCode ignored project subagents settings from {path}; "
                "configure SubAgents in ~/.kcode/config.yaml."
            )
        raw_teams = raw.get("teams", {})
        team_warnings.extend(raw.get("_team_warnings", ()))
        if source == "user":
            teams.update(raw_teams)
        elif "teams" in raw:
            team_warnings.append(
                f"KCode ignored project teams settings from {path}; "
                "enable Agent Teams in ~/.kcode/config.yaml after reviewing parallel Token cost."
            )
    return {
        "active_provider": active,
        "providers": list(merged.values()),
        "agent": agent,
        "memory": memory,
        "memory_warnings": memory_warnings,
        "subagents": subagents,
        "subagent_warnings": subagent_warnings,
        "teams": teams,
        "team_warnings": team_warnings,
        "mcp_servers": mcp_servers,
        "mcp_warnings": mcp_warnings,
    }


def mcp_environment_variables(server: McpServerConfig) -> tuple[str, ...]:
    values = (
        server.env.values() if isinstance(server, StdioMcpServerConfig) else server.headers.values()
    )
    variables = {match.group(1) for value in values for match in ENV_INTERPOLATION.finditer(value)}
    return tuple(sorted(variables))


def expand_mcp_server(
    server: McpServerConfig,
    environ: Mapping[str, str],
) -> tuple[McpServerConfig, tuple[str, ...]]:
    variables = mcp_environment_variables(server)
    missing = tuple(variable for variable in variables if variable not in environ)
    if missing:
        raise MissingMcpEnvironment(missing)

    def expand(value: str) -> str:
        return ENV_INTERPOLATION.sub(lambda match: environ[match.group(1)], value)

    raw = server.model_dump(mode="python")
    field = "env" if isinstance(server, StdioMcpServerConfig) else "headers"
    raw[field] = {key: expand(value) for key, value in raw[field].items()}
    expanded_values = tuple(value for value in raw[field].values() if value)
    referenced_values = tuple(environ[variable] for variable in variables if environ[variable])
    sensitive = tuple(dict.fromkeys((*referenced_values, *expanded_values)))
    return MCP_SERVER_ADAPTER.validate_python(raw), sensitive


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
        raise ConfigError(f"No KCode config found. Create {expected} from config.example.yaml.")
    loaded: list[tuple[Path, dict[str, Any], Literal["user", "project"]]] = []
    for path in candidates:
        source: Literal["user", "project"] = "project" if path == project_path else "user"
        loaded.append((path, _read_yaml(path, source=source), source))
    merged = _merge_configs(loaded)
    source_label = " then ".join(str(path) for path in candidates)
    providers: dict[str, ProviderConfig] = {}
    try:
        for raw in merged["providers"]:
            raw = dict(raw)
            raw["api_key"] = _expand_key(raw.get("api_key"), path_label=source_label, environ=env)
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
        mcp_servers: list[McpServerConfig] = []
        mcp_warnings = list(merged["mcp_warnings"])
        for name, (source, raw_server) in merged["mcp_servers"].items():
            try:
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("server name must be a non-empty string")
                if not isinstance(raw_server, dict):
                    raise ValueError("server definition must be a mapping")
                mcp_servers.append(
                    MCP_SERVER_ADAPTER.validate_python(
                        {**raw_server, "name": name, "source": source}
                    )
                )
            except (ValidationError, ValueError) as exc:
                mcp_warnings.append(
                    f"KCode ignored invalid MCP server {name!r}: {str(exc).splitlines()[0]}."
                )
        return AppConfig(
            active_provider=active,
            providers=providers,
            agent=AgentConfig.model_validate(merged["agent"]),
            memory=MemoryConfig.model_validate(merged["memory"]),
            memory_warnings=tuple(merged["memory_warnings"]),
            subagents=merged["subagents"],
            subagent_warnings=tuple(merged["subagent_warnings"]),
            teams=TeamConfig.model_validate(merged["teams"]),
            team_warnings=tuple(merged["team_warnings"]),
            mcp_servers=tuple(mcp_servers),
            mcp_warnings=tuple(mcp_warnings),
        )
    except ValidationError as exc:
        first = exc.errors(include_url=False)[0]
        field = ".".join(str(part) for part in first["loc"])
        raise ConfigError(
            f"Invalid config {source_label}, field '{field}': {first['msg']}. Fix this value."
        ) from None


def default_config_paths(cwd: Path | None = None) -> tuple[Path, Path]:
    return Path.home() / ".kcode" / "config.yaml", (cwd or Path.cwd()) / ".kcode" / "config.yaml"
