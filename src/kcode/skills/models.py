from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SkillSource(StrEnum):
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"


class SkillMode(StrEnum):
    INLINE = "inline"
    FORK = "fork"


class ForkContext(StrEnum):
    NONE = "none"
    RECENT = "recent"


class SkillMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$", max_length=32)
    description: str = Field(min_length=1, max_length=200)
    allowed_tools: tuple[str, ...] = ()
    mode: SkillMode = SkillMode.INLINE
    fork_context: ForkContext = ForkContext.NONE

    @field_validator("description")
    @classmethod
    def description_is_single_line(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("description must be a trimmed single line")
        return value

    @field_validator("allowed_tools")
    @classmethod
    def tools_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or item != item.strip() for item in value):
            raise ValueError("allowed_tools entries must be non-empty and trimmed")
        if len(set(value)) != len(value):
            raise ValueError("allowed_tools entries must be unique")
        return value

    @model_validator(mode="after")
    def context_matches_mode(self) -> SkillMeta:
        if self.mode is SkillMode.INLINE and self.fork_context is not ForkContext.NONE:
            raise ValueError("inline skills cannot select fork context")
        return self


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    meta: SkillMeta
    body: str
    source: SkillSource
    path: Path
    root: Path
    raw_digest: str


@dataclass(frozen=True, slots=True)
class SkillWarning:
    code: str
    source: SkillSource
    skill: str
    detail: str

    def render(self) -> str:
        return f"Skill warning [{self.code}] {self.source.value}/{self.skill}: {self.detail}"


@dataclass(frozen=True, slots=True)
class SkillLoad:
    definition: SkillDefinition | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillSummary:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class ActivationResult:
    ok: bool
    name: str
    active_names: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
