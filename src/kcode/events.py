from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from kcode.conversation import ProviderContinuationState
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


ProviderEvent: TypeAlias = TextDelta | ThinkingDelta | ToolCallDelta | StreamCompleted
TurnEvent: TypeAlias = ProviderEvent | ToolCallReady | ApprovalPending | ToolStarted | ToolFinished | TurnNotice | TurnCompleted
