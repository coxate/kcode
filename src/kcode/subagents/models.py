from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kcode.events import TokenUsage
from kcode.permissions.models import PermissionMode


class AgentSource(StrEnum):
    PLUGIN = "plugin"
    BUILTIN = "builtin"
    USER = "user"
    PROJECT = "project"


class AgentMeta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64)
    description: str = Field(min_length=1, max_length=200)
    tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    model: str = Field(default="inherit", min_length=1, max_length=128)
    max_turns: int | None = Field(default=None, ge=1, le=100)
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    background: bool = False
    isolation: Literal["shared", "worktree"] = "shared"

    @field_validator("description")
    @classmethod
    def description_is_single_line(cls, value: str) -> str:
        if value != value.strip() or "\n" in value or "\r" in value:
            raise ValueError("description must be a trimmed single line")
        return value

    @field_validator("tools", "disallowed_tools")
    @classmethod
    def tools_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or item != item.strip() for item in value):
            raise ValueError("tool names must be non-empty and trimmed")
        if len(set(value)) != len(value):
            raise ValueError("tool names must be unique")
        return value


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    meta: AgentMeta
    body: str
    source: AgentSource
    path: Path
    root: Path
    raw_digest: str


@dataclass(frozen=True, slots=True)
class AgentWarning:
    code: str
    source: AgentSource
    agent: str
    detail: str

    def render(self) -> str:
        return f"Agent warning [{self.code}] {self.source.value}/{self.agent}: {self.detail}"


@dataclass(frozen=True, slots=True)
class AgentSummary:
    name: str
    description: str


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskKind(StrEnum):
    SUBAGENT = "subagent"
    TEAM_MEMBER = "team_member"


@dataclass(frozen=True, slots=True)
class TaskNotification:
    task_id: str
    name: str
    status: TaskStatus
    result: str
    usage: TokenUsage

    def render(self) -> str:
        total = self.usage.total_tokens
        return (
            "<task-notification>\n"
            f"Task ID: {self.task_id}\n"
            f"Name: {self.name}\n"
            f"Status: {self.status.value}\n"
            f"Token: {total if total is not None else '?'}\n"
            f"Result:\n{self.result}\n"
            "</task-notification>"
        )


_MODE_RANK = {
    PermissionMode.PLAN: 0,
    PermissionMode.DEFAULT: 1,
    PermissionMode.ACCEPT_EDITS: 2,
    PermissionMode.BYPASS_PERMISSIONS: 3,
}


def restricted_mode(parent: PermissionMode, requested: PermissionMode) -> PermissionMode:
    return parent if _MODE_RANK[parent] <= _MODE_RANK[requested] else requested
