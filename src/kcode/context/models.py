from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from kcode.conversation import ConversationMessage
from kcode.tools.base import ToolDefinition

TokenConfidence = Literal["high", "medium", "low"]
CompactionReason = Literal["automatic", "manual", "emergency"]


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    tool_use_id: str
    tool_name: str
    path: str
    byte_count: int
    status: str
    created_at: datetime
    redacted: bool = True


@dataclass(frozen=True, slots=True)
class OffloadDecision:
    tool_use_id: str
    tool_name: str
    decision: Literal["keep", "offload"]
    byte_count: int
    original_index: int
    replacement_text: str | None = None
    artifact: ArtifactRef | None = None

    @property
    def offloaded(self) -> bool:
        return self.decision == "offload"


@dataclass(frozen=True, slots=True)
class NormalizedUsage:
    context_input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    estimated_input_tokens: int | None = None
    output_reserve_tokens: int = 20_000
    is_exact: bool = False
    confidence: TokenConfidence = "low"
    source: str | None = None

    @property
    def effective_input_tokens(self) -> int | None:
        return (
            self.estimated_input_tokens
            if self.estimated_input_tokens is not None
            else self.context_input_tokens
        )

    @property
    def input_tokens(self) -> int | None:
        return self.context_input_tokens

    @property
    def cache_read_input_tokens(self) -> int | None:
        return self.cache_read_tokens

    @property
    def cache_creation_input_tokens(self) -> int | None:
        return self.cache_write_tokens


@dataclass(frozen=True, slots=True)
class ContextBudget:
    context_window: int
    estimated_input_tokens: int
    summary_output_reserve: int = 20_000
    automatic_safety_margin: int = 13_000
    manual_safety_margin: int = 3_000
    confidence: TokenConfidence = "low"

    @property
    def automatic_threshold(self) -> int:
        return max(
            0,
            self.context_window - self.summary_output_reserve - self.automatic_safety_margin,
        )

    @property
    def emergency_threshold(self) -> int:
        return max(0, self.context_window - self.summary_output_reserve - self.manual_safety_margin)

    @property
    def should_compact(self) -> bool:
        return self.estimated_input_tokens >= self.automatic_threshold

    @property
    def fits_after_emergency(self) -> bool:
        return self.estimated_input_tokens < self.emergency_threshold


@dataclass(frozen=True, slots=True)
class StructuredSummary:
    goal: str
    confirmed_facts: tuple[str, ...]
    inferences: tuple[str, ...]
    unknowns: tuple[str, ...]
    decisions: tuple[str, ...]
    files: tuple[str, ...]
    errors: tuple[str, ...]
    current_state: str
    pending_tasks: tuple[str, ...]
    next_steps: tuple[str, ...]
    artifact_references: tuple[str, ...]
    history_incomplete: bool


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    path: str
    content: str
    read_at: datetime
    truncated: bool


@dataclass(frozen=True, slots=True)
class CompactionState:
    summary: StructuredSummary
    rendered_summary: str
    covered_start: int
    covered_end: int
    recent_start: int
    history_incomplete: bool
    source_fingerprint: str


@dataclass(frozen=True, slots=True)
class CompactionResult:
    success: bool
    summary: StructuredSummary | None
    rendered_summary: str | None
    covered_start: int
    covered_end: int
    history_incomplete: bool
    before_tokens: int
    after_tokens: int | None = None
    failure_reason: str | None = None
    retry_count: int = 0
    dropped_messages: int = 0


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    messages: tuple[ConversationMessage, ...]
    tools: tuple[ToolDefinition, ...]
    budget: ContextBudget
    reason: CompactionReason | None = None
    used_compaction: bool = False
    offloaded_count: int = 0
    history_incomplete: bool = False
    compaction_result: CompactionResult | None = None
