from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kcode.context.artifacts import ArtifactStore
from kcode.context.compaction import (
    CompactionEngine,
    select_recent_messages,
    serialize_messages,
)
from kcode.context.ledger import OffloadLedger
from kcode.context.models import (
    CompactionReason,
    CompactionResult,
    CompactionState,
    ContextSnapshot,
    FileSnapshot,
    NormalizedUsage,
    OffloadDecision,
)
from kcode.context.usage import UsageEstimator, normalize_usage, resolve_context_window
from kcode.conversation import ConversationMessage, SystemMessage
from kcode.events import TokenUsage
from kcode.history.ids import create_session_id
from kcode.providers.base import ChatProvider
from kcode.tools.base import ToolDefinition

SUMMARY_OUTPUT_RESERVE = 20_000
AUTOMATIC_SAFETY_MARGIN = 13_000
MANUAL_SAFETY_MARGIN = 3_000
RECENT_TOKENS = 10_000
RECENT_MESSAGES = 5
AUTOMATIC_FAILURE_LIMIT = 3
FILE_SNAPSHOT_LIMIT = 5
FILE_SNAPSHOT_CHARACTERS = 17_500


def _fingerprint(messages: Sequence[ConversationMessage]) -> str:
    return hashlib.sha256(serialize_messages(messages).encode("utf-8")).hexdigest()


class ContextManager:
    def __init__(
        self,
        workspace_root: Path,
        session_id: str | None = None,
        provider: ChatProvider | None = None,
        *,
        context_window: int | None = None,
        model_metadata: Mapping[str, int] | None = None,
        provider_default_window: int = 64_000,
        sensitive_values: Sequence[str] = (),
        artifact_store: ArtifactStore | None = None,
        ledger: OffloadLedger | None = None,
        usage_estimator: UsageEstimator | None = None,
        compaction_engine: CompactionEngine | None = None,
    ) -> None:
        self.provider = provider
        self.session_id = session_id or create_session_id()
        self.artifact_store = artifact_store or ArtifactStore(
            workspace_root,
            self.session_id,
            sensitive_values=sensitive_values,
        )
        self.ledger = ledger or OffloadLedger()
        self.usage_estimator = usage_estimator or UsageEstimator()
        self.compaction_engine = compaction_engine or (
            CompactionEngine(provider) if provider is not None else None
        )
        model_name = provider.model_name if provider is not None else None
        self.context_window, self.window_confidence = resolve_context_window(
            explicit=context_window,
            model=model_name,
            model_metadata=model_metadata,
            provider_default=provider_default_window,
        )
        self._lock = asyncio.Lock()
        self._state: CompactionState | None = None
        self._file_snapshots: dict[str, FileSnapshot] = {}
        self._automatic_failures = 0
        self._last_snapshot: ContextSnapshot | None = None

    @property
    def compaction_state(self) -> CompactionState | None:
        return self._state

    @property
    def automatic_failure_count(self) -> int:
        return self._automatic_failures

    @property
    def automatic_compaction_disabled(self) -> bool:
        return self._automatic_failures >= AUTOMATIC_FAILURE_LIMIT

    @property
    def last_snapshot(self) -> ContextSnapshot | None:
        return self._last_snapshot

    async def build_snapshot(
        self,
        canonical_messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition] = (),
        *,
        prefix_messages: Sequence[ConversationMessage] = (),
        force_compaction: bool = False,
        reason: CompactionReason = "automatic",
        apply_offload: bool = True,
        focus: str | None = None,
    ) -> ContextSnapshot:
        async with self._lock:
            canonical = tuple(canonical_messages)
            tool_tuple = tuple(tools)
            prefix = tuple(prefix_messages)
            if apply_offload:
                model_messages, decisions = await self.ledger.process(
                    canonical,
                    self.artifact_store,
                )
            else:
                model_messages, decisions = await self.ledger.apply(canonical)

            if self._state is not None and not self._state_matches(model_messages):
                self._state = None
            current_view = (*prefix, *self._model_view(model_messages, tool_tuple))
            budget = self.usage_estimator.budget(
                current_view,
                tool_tuple,
                context_window=self.context_window,
                summary_output_reserve=SUMMARY_OUTPUT_RESERVE,
                automatic_safety_margin=AUTOMATIC_SAFETY_MARGIN,
                manual_safety_margin=MANUAL_SAFETY_MARGIN,
            )
            should_attempt = force_compaction or (
                budget.should_compact and not self.automatic_compaction_disabled
            )
            result: CompactionResult | None = None
            if should_attempt:
                result = await self._compact_locked(model_messages, tool_tuple, reason, focus=focus)
                if result.success:
                    if reason == "automatic":
                        self._automatic_failures = 0
                elif reason == "automatic":
                    self._automatic_failures += 1

            final_view = (*prefix, *self._model_view(model_messages, tool_tuple))
            final_budget = self.usage_estimator.budget(
                final_view,
                tool_tuple,
                context_window=self.context_window,
                summary_output_reserve=SUMMARY_OUTPUT_RESERVE,
                automatic_safety_margin=AUTOMATIC_SAFETY_MARGIN,
                manual_safety_margin=MANUAL_SAFETY_MARGIN,
            )
            snapshot = ContextSnapshot(
                messages=final_view,
                tools=tool_tuple,
                budget=final_budget,
                reason=reason if should_attempt else None,
                used_compaction=result.success if result is not None else False,
                offloaded_count=sum(decision.offloaded for decision in decisions),
                history_incomplete=(
                    self._state.history_incomplete if self._state is not None else False
                ),
                compaction_result=result,
            )
            self._last_snapshot = snapshot
            return snapshot

    async def compact(
        self,
        canonical_messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition] = (),
        *,
        prefix_messages: Sequence[ConversationMessage] = (),
        focus: str | None = None,
    ) -> ContextSnapshot:
        return await self.build_snapshot(
            canonical_messages,
            tools,
            prefix_messages=prefix_messages,
            force_compaction=True,
            reason="manual",
            apply_offload=False,
            focus=focus,
        )

    async def emergency_snapshot(
        self,
        canonical_messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition] = (),
        *,
        prefix_messages: Sequence[ConversationMessage] = (),
    ) -> ContextSnapshot:
        return await self.build_snapshot(
            canonical_messages,
            tools,
            prefix_messages=prefix_messages,
            force_compaction=True,
            reason="emergency",
            apply_offload=True,
        )

    def update_sensitive_values(self, values: Sequence[str]) -> None:
        self.artifact_store.update_sensitive_values(values)

    async def record_tool_results(
        self,
        messages: Sequence[ConversationMessage],
    ) -> tuple[OffloadDecision, ...]:
        async with self._lock:
            _, decisions = await self.ledger.process(messages, self.artifact_store)
            return decisions

    def record_usage(
        self,
        usage: NormalizedUsage | TokenUsage | Any,
        messages: Sequence[ConversationMessage] | None = None,
        tools: Sequence[ToolDefinition] | None = None,
        *,
        provider: str | None = None,
    ) -> NormalizedUsage:
        normalized = (
            usage
            if isinstance(usage, NormalizedUsage)
            else normalize_usage(usage, provider or self._provider_name())
        )
        request_messages = (
            tuple(messages)
            if messages is not None
            else (self._last_snapshot.messages if self._last_snapshot is not None else ())
        )
        request_tools = (
            tuple(tools)
            if tools is not None
            else (self._last_snapshot.tools if self._last_snapshot is not None else ())
        )
        self.usage_estimator.record(normalized, request_messages, request_tools)
        return normalized

    async def record_file_snapshot(
        self,
        path: str,
        content: str,
        *,
        read_at: datetime | None = None,
    ) -> FileSnapshot:
        async with self._lock:
            redacted = self.artifact_store.redact(content)
            truncated = len(redacted) > FILE_SNAPSHOT_CHARACTERS
            snapshot = FileSnapshot(
                path=self.artifact_store.redact(path),
                content=redacted[:FILE_SNAPSHOT_CHARACTERS],
                read_at=read_at or datetime.now(timezone.utc),
                truncated=truncated,
            )
            self._file_snapshots[path] = snapshot
            return snapshot

    async def clear(self) -> None:
        async with self._lock:
            self._state = None
            self._file_snapshots.clear()
            self._automatic_failures = 0
            self._last_snapshot = None
            self.usage_estimator.clear()
            await self.ledger.clear()

    async def _compact_locked(
        self,
        messages: tuple[ConversationMessage, ...],
        tools: tuple[ToolDefinition, ...],
        reason: CompactionReason,
        *,
        focus: str | None = None,
    ) -> CompactionResult:
        if self.compaction_engine is None:
            return CompactionResult(
                success=False,
                summary=None,
                rendered_summary=None,
                covered_start=0,
                covered_end=len(messages),
                history_incomplete=False,
                before_tokens=self.usage_estimator.budget(
                    messages, tools, context_window=self.context_window
                ).estimated_input_tokens,
                failure_reason="No compaction engine is configured.",
            )

        recent_start, _ = select_recent_messages(
            messages,
            minimum_tokens=RECENT_TOKENS,
            minimum_messages=RECENT_MESSAGES,
        )
        summary_end = recent_start
        summary_messages = messages[:summary_end]
        if not summary_messages:
            summary_messages = messages
            summary_end = len(messages)
        result = await self.compaction_engine.compact(
            summary_messages,
            source_start=0,
            focus=focus,
        )
        if not result.success or result.summary is None or result.rendered_summary is None:
            return result
        self._state = CompactionState(
            summary=result.summary,
            rendered_summary=result.rendered_summary,
            covered_start=result.covered_start,
            covered_end=summary_end,
            recent_start=recent_start,
            history_incomplete=result.history_incomplete,
            source_fingerprint=_fingerprint(messages[:summary_end]),
        )
        return result

    def _state_matches(self, messages: Sequence[ConversationMessage]) -> bool:
        state = self._state
        if state is None or len(messages) < state.covered_end:
            return False
        return _fingerprint(messages[: state.covered_end]) == state.source_fingerprint

    def _model_view(
        self,
        messages: tuple[ConversationMessage, ...],
        tools: tuple[ToolDefinition, ...],
    ) -> tuple[ConversationMessage, ...]:
        if self._state is None:
            return messages
        summary = SystemMessage(
            "[KCode structured working memory]\n" + self._state.rendered_summary
        )
        recovery = SystemMessage(self._recovery_text(tools))
        return (summary, recovery, *messages[self._state.recent_start :])

    def _recovery_text(self, tools: Sequence[ToolDefinition]) -> str:
        snapshots = sorted(
            self._file_snapshots.values(),
            key=lambda item: item.read_at,
            reverse=True,
        )[:FILE_SNAPSHOT_LIMIT]
        lines = ["[KCode recovery segment]", "Historical file snapshots:"]
        if snapshots:
            for snapshot in snapshots:
                lines.extend(
                    (
                        f"- path: {snapshot.path}",
                        f"  read_at: {snapshot.read_at.isoformat()}",
                        f"  truncated: {str(snapshot.truncated).lower()}",
                        "  historical_content:",
                        snapshot.content,
                    )
                )
        else:
            lines.append("- none")
        tool_index = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            }
            for tool in tools
        ]
        lines.extend(
            (
                "Tools available for the next request:",
                json.dumps(
                    tool_index,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=repr,
                ),
                "Artifact references:",
            )
        )
        artifacts = tuple(
            decision.artifact
            for decision in self.ledger.decisions()
            if decision.offloaded and decision.artifact is not None
        )
        if artifacts:
            lines.extend(f"- {artifact.path}" for artifact in artifacts)
        else:
            lines.append("- none")
        lines.append(
            "Boundary: summaries, previews, and snapshots may be incomplete or stale. "
            "Re-read the original user message, file, error, or Artifact for exact content; "
            "do not invent code from this recovery segment."
        )
        return "\n".join(lines)

    def _provider_name(self) -> str:
        if self.provider is None:
            return "generic"
        return f"{self.provider.display_name} {self.provider.model_name}"
