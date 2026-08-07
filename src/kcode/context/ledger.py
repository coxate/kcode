from __future__ import annotations

import asyncio
from collections.abc import Sequence

from kcode.context.artifacts import ArtifactStore
from kcode.context.models import OffloadDecision
from kcode.conversation import ConversationMessage, ToolResultMessage
from kcode.tools.base import ToolResult

SINGLE_RESULT_LIMIT_BYTES = 50_000
AGGREGATE_RESULT_LIMIT_BYTES = 200_000


def _result_bytes(message: ToolResultMessage) -> bytes:
    return message.result.to_json().encode("utf-8")


def _replacement_result(message: ToolResultMessage, replacement: str) -> ToolResult:
    return ToolResult(
        status=message.result.status,
        data={"content": replacement},
        duration_ms=message.result.duration_ms,
        truncated=True,
        warnings=message.result.warnings,
    )


def _result_groups(
    messages: Sequence[ConversationMessage],
) -> tuple[tuple[tuple[int, ToolResultMessage], ...], ...]:
    groups: list[tuple[tuple[int, ToolResultMessage], ...]] = []
    current: list[tuple[int, ToolResultMessage]] = []
    for index, message in enumerate(messages):
        if isinstance(message, ToolResultMessage):
            current.append((index, message))
        elif current:
            groups.append(tuple(current))
            current = []
    if current:
        groups.append(tuple(current))
    return tuple(groups)


class OffloadLedger:
    def __init__(
        self,
        *,
        single_limit_bytes: int = SINGLE_RESULT_LIMIT_BYTES,
        aggregate_limit_bytes: int = AGGREGATE_RESULT_LIMIT_BYTES,
    ) -> None:
        self.single_limit_bytes = single_limit_bytes
        self.aggregate_limit_bytes = aggregate_limit_bytes
        self._decisions: dict[str, OffloadDecision] = {}
        self._lock = asyncio.Lock()

    def decision_for(self, tool_use_id: str) -> OffloadDecision | None:
        return self._decisions.get(tool_use_id)

    def decisions(self) -> tuple[OffloadDecision, ...]:
        return tuple(self._decisions.values())

    async def apply(
        self,
        messages: Sequence[ConversationMessage],
    ) -> tuple[tuple[ConversationMessage, ...], tuple[OffloadDecision, ...]]:
        async with self._lock:
            return self._apply_locked(messages)

    async def clear(self) -> None:
        async with self._lock:
            self._decisions.clear()

    async def process(
        self,
        messages: Sequence[ConversationMessage],
        store: ArtifactStore,
    ) -> tuple[tuple[ConversationMessage, ...], tuple[OffloadDecision, ...]]:
        async with self._lock:
            indexed = {
                index: (message, len(_result_bytes(message)))
                for index, message in enumerate(messages)
                if isinstance(message, ToolResultMessage)
            }
            selected: set[int] = set()
            failed: set[int] = set()
            artifact_reads = {
                index
                for index, (message, _) in indexed.items()
                if message.tool_name == "read_file"
                and message.result.data is not None
                and isinstance(message.result.data.get("path"), str)
                and store.contains_path(message.result.data["path"])
            }

            for index, (message, byte_count) in indexed.items():
                decision = self._decisions.get(message.tool_call_id)
                if decision is not None:
                    if decision.offloaded:
                        selected.add(index)
                    continue
                if index in artifact_reads:
                    continue
                if byte_count > self.single_limit_bytes:
                    selected.add(index)

            for group in _result_groups(messages):
                visible = 0
                candidates: list[tuple[int, int]] = []
                for index, message in group:
                    byte_count = indexed[index][1]
                    decision = self._decisions.get(message.tool_call_id)
                    if index in selected or (decision is not None and decision.offloaded):
                        replacement = decision.replacement_text if decision is not None else ""
                        visible += len((replacement or "").encode("utf-8"))
                    else:
                        visible += byte_count
                        if decision is None and index not in artifact_reads:
                            candidates.append((index, byte_count))
                if visible > self.aggregate_limit_bytes:
                    ordered = sorted(candidates, key=lambda item: (-item[1], item[0]))
                    for index, byte_count in ordered:
                        if visible <= self.aggregate_limit_bytes:
                            break
                        selected.add(index)
                        visible -= byte_count

            async def offload(index: int) -> bool:
                message, byte_count = indexed[index]
                if message.tool_call_id in self._decisions:
                    return self._decisions[message.tool_call_id].offloaded
                content = _result_bytes(message).decode("utf-8")
                try:
                    artifact = await store.store(
                        message.tool_call_id,
                        message.tool_name,
                        content,
                        status=message.result.status,
                    )
                    replacement = store.build_preview(artifact, content)
                except (OSError, RuntimeError):
                    failed.add(index)
                    return False
                self._decisions[message.tool_call_id] = OffloadDecision(
                    tool_use_id=message.tool_call_id,
                    tool_name=message.tool_name,
                    decision="offload",
                    byte_count=byte_count,
                    original_index=index,
                    replacement_text=replacement,
                    artifact=artifact,
                )
                return True

            for index in sorted(selected):
                await offload(index)

            def visible_bytes(index: int) -> int:
                message, byte_count = indexed[index]
                decision = self._decisions.get(message.tool_call_id)
                if decision is None or not decision.offloaded or decision.replacement_text is None:
                    return byte_count
                replacement = ToolResultMessage(
                    message.tool_call_id,
                    message.tool_name,
                    _replacement_result(message, decision.replacement_text),
                )
                return len(_result_bytes(replacement))

            for group in _result_groups(messages):
                while sum(visible_bytes(index) for index, _ in group) > self.aggregate_limit_bytes:
                    candidates = sorted(
                        (
                            (index, indexed[index][1])
                            for index, message in group
                            if index not in artifact_reads
                            and index not in failed
                            and message.tool_call_id not in self._decisions
                        ),
                        key=lambda item: (-item[1], item[0]),
                    )
                    if not candidates:
                        break
                    await offload(candidates[0][0])

            for index, (message, byte_count) in indexed.items():
                if message.tool_call_id in self._decisions or index in failed:
                    continue
                self._decisions[message.tool_call_id] = OffloadDecision(
                    tool_use_id=message.tool_call_id,
                    tool_name=message.tool_name,
                    decision="keep",
                    byte_count=byte_count,
                    original_index=index,
                )

            return self._apply_locked(messages)

    def _apply_locked(
        self,
        messages: Sequence[ConversationMessage],
    ) -> tuple[tuple[ConversationMessage, ...], tuple[OffloadDecision, ...]]:
        output: list[ConversationMessage] = []
        applied: list[OffloadDecision] = []
        for message in messages:
            if not isinstance(message, ToolResultMessage):
                output.append(message)
                continue
            decision = self._decisions.get(message.tool_call_id)
            if decision is None or not decision.offloaded or decision.replacement_text is None:
                output.append(message)
                if decision is not None:
                    applied.append(decision)
                continue
            output.append(
                ToolResultMessage(
                    message.tool_call_id,
                    message.tool_name,
                    _replacement_result(message, decision.replacement_text),
                )
            )
            applied.append(decision)
        return tuple(output), tuple(applied)
