from __future__ import annotations

from typing import Protocol

from kcode.skills.catalog import SkillCatalog
from kcode.skills.models import ActivationResult, SkillDefinition

MAX_ACTIVE_SKILLS = 5
MAX_ACTIVE_BYTES = 64 * 1024


class SkillSession(Protocol):
    active_skill_names: tuple[str, ...]

    async def record_skill_state(self, names: tuple[str, ...]) -> bool: ...


class SkillRuntime:
    def __init__(self, catalog: SkillCatalog | None = None) -> None:
        self.catalog = catalog or SkillCatalog()
        self._active: dict[str, SkillDefinition] = {}
        self._session: SkillSession | None = None

    @property
    def active_names(self) -> tuple[str, ...]:
        return tuple(self._active)

    def set_catalog(self, catalog: SkillCatalog) -> None:
        self.catalog = catalog

    def bind_session(self, session: SkillSession | None) -> tuple[str, ...]:
        self._session = session
        names = session.active_skill_names if session is not None else ()
        return self.restore(names)

    async def activate(self, name: str) -> ActivationResult:
        loaded = self.catalog.load(name)
        if loaded.definition is None:
            return ActivationResult(
                False,
                name,
                self.active_names,
                loaded.warnings,
                "unknown_skill",
                f"Unknown or unavailable Skill: {name}",
            )
        candidate = dict(self._active)
        candidate[name] = loaded.definition
        if name not in self._active and len(candidate) > MAX_ACTIVE_SKILLS:
            return self._failure(name, "active_limit", "At most 5 Skills can be active")
        total = sum(len(item.body.encode("utf-8")) for item in candidate.values())
        if total > MAX_ACTIVE_BYTES:
            return self._failure(name, "active_size", "Active Skill bodies exceed 64 KiB")
        self._active = candidate
        warnings = list(loaded.warnings)
        if self._session is not None:
            self._session.active_skill_names = self.active_names
            if not await self._session.record_skill_state(self.active_names):
                warnings.append("Skill is active in memory, but its session state was not saved.")
        return ActivationResult(True, name, self.active_names, tuple(warnings))

    def restore(self, names: tuple[str, ...]) -> tuple[str, ...]:
        restored: dict[str, SkillDefinition] = {}
        warnings: list[str] = []
        for name in names:
            loaded = self.catalog.load(name)
            if loaded.definition is None:
                warnings.append(f"Active Skill '{name}' is unavailable and was skipped.")
                continue
            candidate = {**restored, name: loaded.definition}
            size = sum(len(item.body.encode("utf-8")) for item in candidate.values())
            if len(candidate) > MAX_ACTIVE_SKILLS or size > MAX_ACTIVE_BYTES:
                warnings.append(f"Active Skill '{name}' exceeds restore limits and was skipped.")
                continue
            restored = candidate
            warnings.extend(loaded.warnings)
        self._active = restored
        if self._session is not None:
            self._session.active_skill_names = self.active_names
        return tuple(warnings)

    def active_prompt(self) -> str:
        if not self._active:
            return ""
        sections = ["## Active Skills"]
        for definition in self._active.values():
            tools = ", ".join(definition.meta.allowed_tools) or "current permission tools"
            sections.append(
                f"### {definition.meta.name}\nSuggested tools: {tools}\n\n{definition.body}"
            )
        return "\n\n".join(sections)

    def _failure(self, name: str, code: str, message: str) -> ActivationResult:
        return ActivationResult(False, name, self.active_names, (), code, message)
