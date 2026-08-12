from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kcode.conversation import StableSystemMessage, UserMessage
from kcode.events import StreamCompleted, TextDelta
from kcode.memory.extraction import ExtractionError, contains_secret, redact_text
from kcode.memory.models import (
    GOVERNANCE_INTERVAL_SECONDS,
    GOVERNANCE_MIN_RECORDS,
    GOVERNANCE_MIN_SESSIONS,
    GOVERNANCE_RETRY_SECONDS,
    MemoryAction,
    MemoryProposal,
    MemoryRecord,
    MemoryState,
    now_timestamp,
    proposal_id,
)
from kcode.providers.base import ChatProvider

GOVERNANCE_PROMPT = """Review confirmed KCode memories from exactly one scope.
Return one JSON object only: {"candidates": [...]}.
Allowed actions are update, merge, and inactivate. Never propose delete or create.
Every target_id must exist in the supplied records. Do not merge across scopes.
Suggest only clear duplicates, direct conflicts, or likely stale content.
A human must approve every suggestion."""


class _GovernanceCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: MemoryAction
    target_ids: tuple[str, ...]
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=800)
    application: str = Field(min_length=1, max_length=600)
    body: str = Field(default="", max_length=4000)
    reason: str = Field(min_length=1, max_length=800)
    evidence: str = Field(min_length=1, max_length=800)


class _GovernanceEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: tuple[_GovernanceCandidate, ...] = Field(max_length=10)


class MemoryGovernor:
    def __init__(self, provider: ChatProvider, sensitive_values: Sequence[str] = ()) -> None:
        self.provider = provider
        self.sensitive_values = tuple(sensitive_values)

    def update_sensitive_values(self, values: Sequence[str]) -> None:
        self.sensitive_values = tuple(values)

    def due(self, records: Sequence[MemoryRecord], state: MemoryState, now: float) -> bool:
        if len(records) < GOVERNANCE_MIN_RECORDS:
            return False
        if len(state.completed_session_ids) < GOVERNANCE_MIN_SESSIONS:
            return False
        if state.last_governed_at is not None and (
            now - state.last_governed_at < GOVERNANCE_INTERVAL_SECONDS
        ):
            return False
        if state.last_governance_failure_at is not None and (
            now - state.last_governance_failure_at < GOVERNANCE_RETRY_SECONDS
        ):
            return False
        return True

    async def propose(self, records: Sequence[MemoryRecord]) -> tuple[MemoryProposal, ...]:
        if not records:
            return ()
        scope = records[0].scope
        if any(record.scope != scope for record in records):
            raise ValueError("Governance records must use one scope.")
        payload = [
            {
                "id": record.id,
                "type": record.type.value,
                "title": record.title,
                "summary": record.summary,
                "application": record.application,
                "updated_at": record.updated_at,
            }
            for record in records
        ]
        request = redact_text(json.dumps(payload, ensure_ascii=False), self.sensitive_values)
        text = ""
        completed = False
        async for event in self.provider.stream(
            (StableSystemMessage(GOVERNANCE_PROMPT), UserMessage(request)),
            (),
            tool_choice="none",
        ):
            if isinstance(event, TextDelta):
                text += event.text
            elif isinstance(event, StreamCompleted):
                completed = True
        if not completed:
            raise ExtractionError("Memory governance stream ended without completion.")
        try:
            envelope = _GovernanceEnvelope.model_validate(json.loads(text.strip()))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ExtractionError(f"Invalid memory governance JSON: {exc}") from exc
        known = {record.id: record for record in records}
        proposals: list[MemoryProposal] = []
        for item in envelope.candidates:
            if item.action not in {
                MemoryAction.UPDATE,
                MemoryAction.MERGE,
                MemoryAction.INACTIVATE,
            }:
                continue
            targets = [known.get(target_id) for target_id in item.target_ids]
            if any(target is None or target.scope != scope for target in targets):
                continue
            primary = targets[0]
            assert primary is not None
            content = "\n".join(
                (item.title, item.summary, item.application, item.body, item.evidence)
            )
            if contains_secret(redact_text(content, self.sensitive_values)):
                continue
            values = {
                "action": item.action.value,
                "target_ids": list(item.target_ids),
                "title": item.title,
                "summary": item.summary,
                "application": item.application,
                "scope": scope.value,
            }
            try:
                proposals.append(
                    MemoryProposal(
                        id=proposal_id(values),
                        action=item.action,
                        type=primary.type,
                        scope=scope,
                        target_ids=item.target_ids,
                        title=item.title,
                        summary=item.summary,
                        application=item.application,
                        body=item.body,
                        reason=item.reason,
                        evidence=item.evidence,
                        source_session_id="governance",
                        source_turn_hash=__import__("hashlib")
                        .sha256(json.dumps(values, sort_keys=True).encode())
                        .hexdigest(),
                        created_at=now_timestamp(),
                    )
                )
            except ValidationError:
                continue
        return tuple(proposals)
