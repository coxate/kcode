from __future__ import annotations

import json
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from kcode.permissions.models import ApprovalChoice

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
ToolStatus = Literal["success", "error", "denied", "timeout", "cancelled"]


class ToolEffect(StrEnum):
    READ_ONLY = "read_only"
    SIDE_EFFECT = "side_effect"


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadFileArgs(ToolArguments):
    path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=2000, ge=1, le=2000)


class WriteFileArgs(ToolArguments):
    path: str = Field(min_length=1)
    content: str


class EditFileArgs(ToolArguments):
    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str


class RunCommandArgs(ToolArguments):
    command: str = Field(min_length=1)
    cwd: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=120)


class FindFilesArgs(ToolArguments):
    root: str = "."
    pattern: str = Field(min_length=1)


class SearchCodeArgs(ToolArguments):
    root: str = "."
    pattern: str = Field(min_length=1)
    file_pattern: str | None = None
    case_sensitive: bool = True


@dataclass(frozen=True, slots=True)
class ToolCall:
    index: int
    id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolError:
    code: str
    message: str
    details: Mapping[str, JSONValue]


@dataclass(frozen=True, slots=True)
class ToolResult:
    status: ToolStatus
    data: Mapping[str, JSONValue] | None = None
    error: ToolError | None = None
    duration_ms: int = 0
    truncated: bool = False
    warnings: tuple[str, ...] = ()

    @classmethod
    def success(
        cls,
        data: Mapping[str, JSONValue],
        *,
        duration_ms: int = 0,
        truncated: bool = False,
        warnings: tuple[str, ...] = (),
    ) -> "ToolResult":
        return cls("success", data, None, duration_ms, truncated, warnings)

    @classmethod
    def failure(
        cls,
        code: str,
        message: str,
        *,
        status: ToolStatus = "error",
        details: Mapping[str, JSONValue] | None = None,
        duration_ms: int = 0,
    ) -> "ToolResult":
        return cls(status, None, ToolError(code, message, details or {}), duration_ms)

    def to_dict(self) -> dict[str, JSONValue]:
        value: dict[str, JSONValue] = {
            "status": self.status,
            "duration_ms": self.duration_ms,
            "truncated": self.truncated,
        }
        if self.data is not None:
            value["data"] = dict(self.data)
        if self.error is not None:
            value["error"] = {
                "code": self.error.code,
                "message": self.error.message,
                "details": dict(self.error.details),
            }
        if self.warnings:
            value["warnings"] = list(self.warnings)
        return value

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ToolLimits:
    file_timeout_seconds: int = 10
    command_timeout_seconds: int = 30
    max_bytes: int = 256 * 1024
    max_items: int = 500


@dataclass(frozen=True, slots=True)
class ToolContext:
    workspace_root: Path
    limits: ToolLimits = ToolLimits()
    sensitive_values: tuple[str, ...] = ()
    cancel_event: threading.Event | None = None
    use_shell: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    tool_call_id: str
    tool_name: str
    preview: str
    reason: str
    permanent_rule: str
    source_label: str | None = None
    task_id: str | None = None


ApprovalHandler: TypeAlias = Callable[[ApprovalRequest], Awaitable["ApprovalChoice"]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    arguments_model: type[ToolArguments]
    effect: ToolEffect | None = ToolEffect.READ_ONLY
    parameters: Mapping[str, Any] | None = None
    always_visible: bool = False
    self_managed_timeout: bool = False


class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class PreparedToolCall:
    call: ToolCall
    tool: Tool | None
    arguments: ToolArguments | None
    effect: ToolEffect
    approval: ApprovalRequest | None = None
    error: ToolResult | None = None


@dataclass(frozen=True, slots=True)
class ValidatedToolCall:
    call: ToolCall
    tool: Tool | None
    arguments: ToolArguments | None
    declared_effect: ToolEffect
    error: ToolResult | None = None


class ToolExecutionError(Exception):
    def __init__(self, code: str, message: str, **details: JSONValue) -> None:
        super().__init__(message)
        self.code = code
        self.details = details
