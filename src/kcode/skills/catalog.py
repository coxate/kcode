from __future__ import annotations

import hashlib
from pathlib import Path

from kcode.skills.models import (
    SkillDefinition,
    SkillLoad,
    SkillSource,
    SkillSummary,
)
from kcode.skills.parser import parse_skill, read_skill_bytes
from kcode.skills.trust import SkillTrustRequest, project_fingerprint

MAX_CATALOG_SKILLS = 30


def _skill_files(root: Path) -> tuple[Path, ...]:
    try:
        if root.is_symlink() or not root.is_dir():
            return ()
        return tuple(
            child / "SKILL.md"
            for child in sorted(root.iterdir(), key=lambda item: item.name)
            if child.is_dir() and not child.is_symlink()
        )
    except OSError:
        return ()


class SkillCatalog:
    def __init__(
        self,
        definitions: tuple[SkillDefinition, ...] = (),
        warnings: tuple[str, ...] = (),
    ) -> None:
        self._definitions = {item.meta.name: item for item in definitions}
        self.warnings = warnings

    def __len__(self) -> int:
        return len(self._definitions)

    def get(self, name: str) -> SkillDefinition | None:
        return self._definitions.get(name)

    def summaries(self) -> tuple[SkillSummary, ...]:
        return tuple(
            SkillSummary(item.meta.name, item.meta.description)
            for item in sorted(self._definitions.values(), key=lambda value: value.meta.name)
        )

    def available_prompt(self) -> str:
        summaries = self.summaries()
        if not summaries:
            return ""
        lines = [
            "## Available Skills",
            "Use `load_skill` when a listed workflow applies. Load only the skills you need.",
        ]
        lines.extend(f"- `{item.name}`: {item.description}" for item in summaries)
        return "\n".join(lines)

    def load(self, name: str) -> SkillLoad:
        definition = self.get(name)
        if definition is None:
            return SkillLoad(None, (f"Unknown or unavailable Skill: {name}",))
        if definition.source is SkillSource.BUILTIN:
            return SkillLoad(definition)
        if definition.source is SkillSource.PROJECT:
            raw, warning = read_skill_bytes(definition.path, definition.root, definition.source)
            if raw is None:
                message = warning.render() if warning is not None else "project Skill read failed"
                return SkillLoad(definition, (message,))
            if hashlib.sha256(raw).hexdigest() != definition.raw_digest:
                return SkillLoad(
                    definition,
                    (f"Project Skill '{name}' changed; restart Kcode to review trust.",),
                )
            return SkillLoad(definition)
        refreshed, warning = parse_skill(definition.path, definition.root, definition.source)
        if refreshed is None:
            message = warning.render() if warning is not None else "user Skill refresh failed"
            return SkillLoad(definition, (message,))
        if refreshed.meta != definition.meta:
            return SkillLoad(
                definition,
                (f"User Skill '{name}' metadata changed; restart Kcode to register it.",),
            )
        return SkillLoad(refreshed)


class SkillCatalogBuilder:
    def __init__(
        self,
        project_root: Path,
        *,
        builtin_root: Path | None = None,
        user_root: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.builtin_root = builtin_root or Path(__file__).parent / "builtin"
        self.user_root = user_root or Path.home() / ".kcode" / "skills"
        self.project_skills_root = self.project_root / ".kcode" / "skills"
        self._project_files = _skill_files(self.project_skills_root)

    def trust_request(self) -> tuple[SkillTrustRequest | None, tuple[str, ...]]:
        return project_fingerprint(self.project_root, self._project_files)

    def build(
        self,
        *,
        project_trusted: bool,
        tool_names: set[str],
        command_names: set[str],
    ) -> SkillCatalog:
        warnings: list[str] = []
        merged: dict[str, SkillDefinition] = {}
        sources = [
            (SkillSource.BUILTIN, self.builtin_root),
            (SkillSource.USER, self.user_root),
        ]
        if project_trusted:
            sources.append((SkillSource.PROJECT, self.project_skills_root))
        for source, root in sources:
            for path in _skill_files(root):
                definition, warning = parse_skill(path, root, source)
                if definition is None:
                    if warning is not None:
                        warnings.append(warning.render())
                    continue
                merged[definition.meta.name] = definition
        selected = sorted(merged.values(), key=lambda item: item.meta.name)
        for item in selected[MAX_CATALOG_SKILLS:]:
            warnings.append(f"Skill warning [catalog_limit] {item.meta.name}: Catalog limit is 30")
        valid: list[SkillDefinition] = []
        for item in selected[:MAX_CATALOG_SKILLS]:
            unknown = sorted(set(item.meta.allowed_tools) - tool_names)
            if unknown:
                warnings.append(
                    f"Skill warning [unknown_tool] {item.meta.name}: "
                    f"unknown tools: {', '.join(unknown)}"
                )
                continue
            if item.meta.name in command_names:
                warnings.append(
                    f"Skill warning [command_conflict] {item.meta.name}: "
                    "name conflicts with a built-in command or alias"
                )
                continue
            valid.append(item)
        return SkillCatalog(tuple(valid), tuple(warnings))
