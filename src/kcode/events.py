from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from kcode.conversation import ProviderContinuationState
from kcode.permissions.models import PermissionMode
from kcode.tools.base import ApprovalRequest, ToolCall, ToolResult


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    index: int
    id_fragment: str = ""
    name_fragment: str = ""
    arguments_fragment: str = ""


@dataclass(frozen=True, slots=True)
class StreamCompleted:
    stop_reason: str | None = None
    continuation_state: ProviderContinuationState | None = None


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None

    def plus(self, other: "TokenUsage") -> "TokenUsage":
        def add(left: int | None, right: int | None) -> int | None:
            return left + right if left is not None and right is not None else None

        return TokenUsage(
            add(self.input_tokens, other.input_tokens),
            add(self.output_tokens, other.output_tokens),
            add(self.total_tokens, other.total_tokens),
            add(self.cache_creation_input_tokens, other.cache_creation_input_tokens),
            add(self.cache_read_input_tokens, other.cache_read_input_tokens),
        )


@dataclass(frozen=True, slots=True)
class UsageReported:
    usage: TokenUsage


class AgentPhase(StrEnum):
    MODEL = "model"
    TOOLS = "tools"
    APPROVAL = "approval"
    COMPLETE = "complete"


class AgentStopReason(StrEnum):
    COMPLETED = "completed"
    ITERATION_LIMIT = "iteration_limit"
    CANCELLED = "cancelled"
    UNKNOWN_TOOL_LIMIT = "unknown_tool_limit"
    STREAM_ERROR = "stream_error"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True, slots=True)
class AgentProgress:
    mode: PermissionMode
    iteration: int
    max_iterations: int
    phase: AgentPhase
    batch: int | None = None


@dataclass(frozen=True, slots=True)
class TokenUsageUpdated:
    iteration: int
    request: TokenUsage
    cumulative: TokenUsage


@dataclass(frozen=True, slots=True)
class AgentStopped:
    reason: AgentStopReason
    iteration: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ToolCallReady:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ApprovalPending:
    request: ApprovalRequest


@dataclass(frozen=True, slots=True)
class ToolStarted:
    call: ToolCall


@dataclass(frozen=True, slots=True)
class ToolFinished:
    call: ToolCall
    result: ToolResult


@dataclass(frozen=True, slots=True)
class TurnNotice:
    message: str


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    committed: bool


ProviderEvent: TypeAlias = (
    TextDelta | ThinkingDelta | ToolCallDelta | UsageReported | StreamCompleted
)
AgentEvent: TypeAlias = (
    ProviderEvent
    | ToolCallReady
    | ApprovalPending
    | ToolStarted
    | ToolFinished
    | AgentProgress
    | TokenUsageUpdated
    | AgentStopped
    | TurnNotice
)
TurnEvent: TypeAlias = AgentEvent | TurnCompleted
