from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ToolCallPayload(StrictRecord):
    index: int = Field(ge=0)
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    arguments_json: str


class ToolErrorPayload(StrictRecord):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResultPayload(StrictRecord):
    status: Literal["success", "error", "denied", "timeout", "cancelled"]
    data: dict[str, Any] | None = None
    error: ToolErrorPayload | None = None
    duration_ms: int = Field(default=0, ge=0)
    truncated: bool = False
    warnings: tuple[str, ...] = ()


class UserMessagePayload(StrictRecord):
    kind: Literal["user"]
    content: str


class AssistantMessagePayload(StrictRecord):
    kind: Literal["assistant"]
    content: str
    tool_calls: tuple[ToolCallPayload, ...] = ()


class ToolResultMessagePayload(StrictRecord):
    kind: Literal["tool_result"]
    tool_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    result: ToolResultPayload


MessagePayload = Annotated[
    UserMessagePayload | AssistantMessagePayload | ToolResultMessagePayload,
    Field(discriminator="kind"),
]


class SessionRecord(StrictRecord):
    type: Literal["session"]
    schema_version: Literal[1] = Field(alias="schema")
    session_id: str
    created_at: float
    provider: str
    model: str


class MessageRecord(StrictRecord):
    type: Literal["message"]
    ts: float
    message: MessagePayload


class SessionEndRecord(StrictRecord):
    type: Literal["session_end"]
    ts: float
    reason: str


class SkillStateRecord(StrictRecord):
    type: Literal["skill_state"]
    ts: float
    names: tuple[str, ...]


JournalRecord = Annotated[
    SessionRecord | MessageRecord | SessionEndRecord | SkillStateRecord,
    Field(discriminator="type"),
]


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    schema: int
    session_id: str
    created_at: float
    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class SessionSummary:
    session_id: str
    title: str
    last_active_at: float
    provider: str
    model: str
    size_bytes: int
    message_count: int
    busy: bool = False


@dataclass(frozen=True, slots=True)
class LoadedSession:
    metadata: SessionMetadata
    messages: tuple[Any, ...]
    turns: tuple[Any, ...]
    warnings: tuple[str, ...]
    last_active_at: float
    skipped_lines: int = 0
    active_skill_names: tuple[str, ...] = ()


class PersistenceState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CLOSED = "closed"
