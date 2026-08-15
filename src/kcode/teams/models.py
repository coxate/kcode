from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from kcode.tools.base import JSONValue
from kcode.worktrees import WorktreeRecord

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class TeamError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_team_slug(value: str) -> str:
    if not _SLUG.fullmatch(value) or value in {".", ".."}:
        raise TeamError(
            "invalid_team_name",
            "Team 和成员名称必须是最长 64 字符的单段小写 slug，只能包含字母、数字和连字符。",
        )
    return value


class TeamMemberStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class TeamTaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class IsolationMode(StrEnum):
    SHARED = "shared"
    WORKTREE = "worktree"


@dataclass(frozen=True, slots=True)
class TeamCaller:
    role: Literal["lead", "member"]
    member_name: str | None = None
    team_id: str | None = None

    @classmethod
    def lead(cls) -> TeamCaller:
        return cls("lead")

    @classmethod
    def member(cls, name: str, team_id: str) -> TeamCaller:
        return cls("member", validate_team_slug(name), team_id)


@dataclass(frozen=True, slots=True)
class TeamMessage:
    sender: str
    recipient: str
    body: str
    created_at: float
    sequence: int


@dataclass(frozen=True, slots=True)
class TeamTask:
    id: str
    title: str
    description: str
    status: TeamTaskStatus
    assignee: str | None
    blocked_by: frozenset[str]
    created_by: str
    created_at: float
    updated_at: float

    @property
    def terminal(self) -> bool:
        return self.status in {TeamTaskStatus.COMPLETED, TeamTaskStatus.CANCELLED}


@dataclass(slots=True)
class TeamMember:
    name: str
    task_id: str
    subagent_type: str
    isolation: IsolationMode
    status: TeamMemberStatus = TeamMemberStatus.STARTING
    worktree: WorktreeRecord | None = None
    worktree_report: dict[str, JSONValue] | None = None
    last_result: str = ""
    last_error: str = ""
    total_tokens: int | None = 0
    wake_scheduled: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Team:
    name: str
    goal: str
    id: str = field(default_factory=lambda: f"team-{uuid.uuid4().hex[:12]}")
    members: dict[str, TeamMember] = field(default_factory=dict)
    tasks: dict[str, TeamTask] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class TeamOperationResult:
    data: dict[str, JSONValue]
    warnings: tuple[str, ...] = ()


def make_team_task_id() -> str:
    return f"team-task-{uuid.uuid4().hex[:12]}"
