from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

from kcode.permissions.models import PermissionMode
from kcode.tools.base import JSONValue


class HookEvent(StrEnum):
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_SEND = "pre_send"
    POST_RECEIVE = "post_receive"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    COMPACT = "compact"
    PERMISSION_REQUEST = "permission_request"
    FILE_CHANGE = "file_change"
    COMMAND_EXECUTE = "command_execute"


class HookSource(StrEnum):
    USER = "user"
    PROJECT = "project"


class ConditionOperator(StrEnum):
    EXACT = "=="
    NOT_EQUAL = "!="
    REGEX = "=~"
    GLOB = "~="


class ConditionJoin(StrEnum):
    ALL = "and"
    ANY = "or"


class CompiledMatcher(Protocol):
    def matches(self, actual: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Condition:
    field: str
    operator: ConditionOperator
    expected: str
    matcher: CompiledMatcher


@dataclass(frozen=True, slots=True)
class ConditionGroup:
    join: ConditionJoin
    conditions: tuple[Condition, ...]

    def evaluate(self, context: HookContext) -> bool:
        values = (item.matcher.matches(context.field_value(item.field)) for item in self.conditions)
        return all(values) if self.join is ConditionJoin.ALL else any(values)


class _ActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CommandAction(_ActionModel):
    type: Literal["command"]
    command: str = Field(min_length=1, max_length=32 * 1024)
    timeout: float = Field(default=30.0, ge=0.1, le=300.0, strict=True)


class PromptAction(_ActionModel):
    type: Literal["prompt"]
    message: str = Field(min_length=1, max_length=32 * 1024)


class HttpAction(_ActionModel):
    type: Literal["http"]
    url: str = Field(min_length=1, max_length=32 * 1024)
    method: str = Field(default="POST", min_length=1, max_length=16)
    headers: dict[str, str] = Field(default_factory=dict)
    body: str | None = Field(default=None, max_length=32 * 1024)
    timeout: float = Field(default=30.0, ge=0.1, le=300.0, strict=True)


class AgentAction(_ActionModel):
    type: Literal["agent"]
    prompt: str = Field(min_length=1, max_length=32 * 1024)
    subagent_type: str = Field(
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
        max_length=64,
    )
    name: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def name_is_single_line(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or "\n" in value or "\r" in value):
            raise ValueError("name must be a trimmed single line")
        return value


HookAction: TypeAlias = Annotated[
    CommandAction | PromptAction | HttpAction | AgentAction,
    Field(discriminator="type"),
]


@dataclass(frozen=True, slots=True)
class Hook:
    id: str
    event: HookEvent
    condition: ConditionGroup | None
    action: CommandAction | PromptAction | HttpAction | AgentAction | None
    reject: bool
    reason: str | None
    once: bool
    run_async: bool
    source: HookSource
    source_path: Path
    order: int


@dataclass(frozen=True, slots=True)
class HookContext:
    event: HookEvent
    session_id: str
    cwd: Path
    mode: PermissionMode
    tool_name: str = ""
    tool_args: Mapping[str, JSONValue] = field(default_factory=dict)
    file_path: str = ""
    message: str = ""
    error: str = ""
    command: str = ""
    tool_status: str = ""
    iteration: int = 0
    is_subagent: bool = False

    def field_value(self, path: str) -> str:
        values = {
            "event": self.event.value,
            "tool": self.tool_name,
            "file_path": self.file_path,
            "message": self.message,
            "error": self.error,
            "command": self.command,
        }
        if path in values:
            return values[path]
        if not path.startswith("args."):
            return ""
        current: object = self.tool_args
        for part in path[5:].split("."):
            if not isinstance(current, Mapping) or part not in current:
                return ""
            current = current[part]
        if isinstance(current, str):
            return current
        if current is None:
            return ""
        if isinstance(current, (dict, list)):
            return json.dumps(current, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return str(current).lower() if isinstance(current, bool) else str(current)

    def to_payload(self) -> dict[str, JSONValue]:
        return {
            "command": self.command,
            "cwd": str(self.cwd),
            "error": self.error,
            "event": self.event.value,
            "file_path": self.file_path,
            "iteration": self.iteration,
            "message": self.message,
            "mode": self.mode.value,
            "session_id": self.session_id,
            "tool_args": dict(self.tool_args),
            "tool_name": self.tool_name,
            "tool_status": self.tool_status,
        }


@dataclass(frozen=True, slots=True)
class HookWarning:
    code: str
    message: str
    hook_id: str | None = None
    event: HookEvent | None = None

    def render(self) -> str:
        prefix = f"Hook '{self.hook_id}'" if self.hook_id else "Hook"
        event = f" ({self.event.value})" if self.event is not None else ""
        return f"{prefix}{event}: {self.message}"


@dataclass(frozen=True, slots=True)
class HookDispatchResult:
    executed_ids: tuple[str, ...] = ()
    injected_prompts: tuple[str, ...] = ()
    warnings: tuple[HookWarning, ...] = ()


class ToolRejectedError(Exception):
    def __init__(self, hook_id: str, tool_name: str, reason: str) -> None:
        super().__init__(reason)
        self.hook_id = hook_id
        self.tool_name = tool_name
        self.reason = reason


@dataclass(frozen=True, slots=True)
class HookSummary:
    id: str
    event: HookEvent
    action_type: str
    once: bool
    run_async: bool
    reject: bool
    source: HookSource


@dataclass(frozen=True, slots=True)
class HookCatalog:
    hooks: tuple[Hook, ...] = ()
    sources: tuple[Path, ...] = ()
    warnings: tuple[HookWarning, ...] = ()

    def for_event(self, event: HookEvent) -> tuple[Hook, ...]:
        return tuple(item for item in self.hooks if item.event is event)

    def summaries(self) -> tuple[HookSummary, ...]:
        return tuple(
            HookSummary(
                item.id,
                item.event,
                item.action.type if item.action is not None else "reject",
                item.once,
                item.run_async,
                item.reject,
                item.source,
            )
            for item in self.hooks
        )
