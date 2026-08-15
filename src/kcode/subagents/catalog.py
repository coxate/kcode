from __future__ import annotations

import hashlib
import re
from pathlib import Path

from kcode.permissions.models import PermissionMode
from kcode.subagents.models import AgentDefinition, AgentSource, AgentSummary
from kcode.subagents.parser import parse_agent, read_agent_bytes
from kcode.subagents.trust import AgentTrustRequest, project_fingerprint

MAX_CATALOG_AGENTS = 30
_PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _agent_files(root: Path) -> tuple[Path, ...]:
    try:
        if root.is_symlink() or not root.is_dir():
            return ()
        return tuple(
            child
            for child in sorted(root.iterdir(), key=lambda item: item.name)
            if child.suffix == ".md" and child.is_file() and not child.is_symlink()
        )
    except OSError:
        return ()


def _plugin_files(root: Path) -> tuple[Path, ...]:
    try:
        if root.is_symlink() or not root.is_dir():
            return ()
        files: list[Path] = []
        for plugin in sorted(root.iterdir(), key=lambda item: item.name):
            if not _PLUGIN_ID.fullmatch(plugin.name) or plugin.is_symlink() or not plugin.is_dir():
                continue
            files.extend(_agent_files(plugin / "agents"))
        return tuple(files)
    except OSError:
        return ()


class AgentCatalog:
    def __init__(
        self,
        definitions: tuple[AgentDefinition, ...] = (),
        warnings: tuple[str, ...] = (),
    ) -> None:
        self._definitions = {item.meta.name: item for item in definitions}
        self.warnings = warnings

    def __len__(self) -> int:
        return len(self._definitions)

    def get(self, name: str) -> AgentDefinition | None:
        return self._definitions.get(name)

    def summaries(self) -> tuple[AgentSummary, ...]:
        return tuple(
            AgentSummary(item.meta.name, item.meta.description)
            for item in sorted(self._definitions.values(), key=lambda value: value.meta.name)
        )

    def available_prompt(self) -> str:
        summaries = self.summaries()
        if not summaries:
            return ""
        lines = [
            "## Available Agents",
            "Delegate independent subtasks with the agent tool when isolation helps.",
        ]
        lines.extend(f"- {item.name}: {item.description}" for item in summaries)
        return "\n".join(lines)

    def resolve(self, name: str) -> tuple[AgentDefinition | None, tuple[str, ...]]:
        definition = self.get(name)
        if definition is None:
            return None, (f"Unknown or unavailable Agent: {name}",)
        if definition.source is not AgentSource.PROJECT:
            return definition, ()
        raw, warning = read_agent_bytes(definition.path, definition.root, definition.source)
        if raw is None:
            detail = warning.render() if warning is not None else "project Agent read failed"
            return definition, (detail,)
        if hashlib.sha256(raw).hexdigest() != definition.raw_digest:
            return definition, (f"Project Agent '{name}' changed; restart Kcode to review trust.",)
        return definition, ()


class AgentCatalogBuilder:
    def __init__(
        self,
        project_root: Path,
        *,
        builtin_root: Path | None = None,
        user_root: Path | None = None,
        plugin_root: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.builtin_root = builtin_root or Path(__file__).parent / "builtin"
        self.user_root = user_root or Path.home() / ".kcode" / "agents"
        self.plugin_root = plugin_root or Path.home() / ".kcode" / "plugins"
        self.project_agents_root = self.project_root / ".kcode" / "agents"
        self._project_files = _agent_files(self.project_agents_root)
        self._project_cache: tuple[AgentDefinition, ...] | None = None
        self._project_cache_warnings: tuple[str, ...] = ()

    def _capture_project(self) -> None:
        definitions: list[AgentDefinition] = []
        warnings: list[str] = []
        for path in self._project_files:
            definition, warning = parse_agent(path, self.project_agents_root, AgentSource.PROJECT)
            if definition is not None:
                definitions.append(definition)
            elif warning is not None:
                warnings.append(warning.render())
        self._project_cache = tuple(definitions)
        self._project_cache_warnings = tuple(warnings)

    def trust_request(self) -> tuple[AgentTrustRequest | None, tuple[str, ...]]:
        request, warnings = project_fingerprint(self.project_root, self._project_files)
        self._capture_project()
        return request, warnings

    def build(
        self,
        *,
        project_trusted: bool,
        tool_names: set[str],
        provider_names: set[str],
    ) -> AgentCatalog:
        warnings: list[str] = []
        merged: dict[str, AgentDefinition] = {}

        def accept(item: AgentDefinition) -> bool:
            meta = item.meta
            unknown_tools = sorted((set(meta.tools) | set(meta.disallowed_tools)) - tool_names)
            problem = ""
            code = ""
            if unknown_tools:
                code = "unknown_tool"
                problem = f"unknown tools: {', '.join(unknown_tools)}"
            elif meta.model != "inherit" and meta.model not in provider_names:
                code = "unknown_provider"
                problem = "configured Provider does not exist"
            elif (
                item.source is AgentSource.PROJECT
                and meta.permission_mode is PermissionMode.BYPASS_PERMISSIONS
            ):
                code = "project_bypass"
                problem = "project Agents cannot use bypassPermissions"
            if problem:
                if item.source is AgentSource.BUILTIN:
                    raise RuntimeError(
                        f"Invalid built-in Agent definition: {item.path.name} ({code})"
                    )
                warnings.append(
                    f"Agent warning [{code}] {item.source.value}/{meta.name}: {problem}"
                )
                return False
            if (
                item.source in {AgentSource.USER, AgentSource.PLUGIN}
                and meta.permission_mode is PermissionMode.BYPASS_PERMISSIONS
            ):
                warnings.append(
                    f"Agent warning [bypass_enabled] {item.source.value}/{meta.name}: "
                    "this Agent can bypass ordinary permission prompts"
                )
            return True

        sources = (
            (AgentSource.PLUGIN, _plugin_files(self.plugin_root)),
            (AgentSource.BUILTIN, _agent_files(self.builtin_root)),
            (AgentSource.USER, _agent_files(self.user_root)),
        )
        for source, files in sources:
            for path in files:
                definition, warning = parse_agent(path, path.parent, source)
                if definition is None:
                    if source is AgentSource.BUILTIN:
                        raise RuntimeError(
                            f"Invalid built-in Agent definition: {path.name}"
                        ) from None
                    if warning is not None:
                        warnings.append(warning.render())
                    continue
                if accept(definition):
                    merged[definition.meta.name] = definition
        if project_trusted:
            if self._project_cache is None:
                self._project_files = _agent_files(self.project_agents_root)
                self._capture_project()
            warnings.extend(self._project_cache_warnings)
            for definition in self._project_cache or ():
                if accept(definition):
                    merged[definition.meta.name] = definition
        selected = sorted(merged.values(), key=lambda item: item.meta.name)
        for item in selected[MAX_CATALOG_AGENTS:]:
            warnings.append(f"Agent warning [catalog_limit] {item.meta.name}: Catalog limit is 30")
        return AgentCatalog(tuple(selected[:MAX_CATALOG_AGENTS]), tuple(warnings))
