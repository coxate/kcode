from __future__ import annotations

import json
import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kcode.conversation import StableSystemMessage, UserMessage
from kcode.events import StreamCompleted, TextDelta
from kcode.memory.models import (
    MAX_CANDIDATES_PER_TURN,
    CompletedTurn,
    MemoryAction,
    MemoryProposal,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    now_timestamp,
    proposal_id,
)
from kcode.providers.base import ChatProvider

EXTRACTION_PROMPT = """You extract durable long-term-memory proposals for KCode.
Return one JSON object only: {"candidates": [...]} with at most 3 candidates.
Candidate fields: action, type, scope, target_ids, title, summary, application,
body, reason, evidence. Allowed actions: create, update, merge, inactivate.
Allowed types: user_preference, feedback, project_fact, reference.
Allowed scopes: user, project. Project facts and references must be project scoped.
Feedback is project scoped unless the user explicitly says it applies to every project.
Prefer no candidate over temporary, speculative, obvious, sensitive, or low-value facts.
Never output credentials, private keys, tokens, passwords, or raw conversation history.
For update/merge/inactivate, target_ids must name records from the supplied active index.
You only propose changes. A human must approve every proposal."""

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(?:password|passwd|api[_-]?key|token|secret)\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
)
GLOBAL_FEEDBACK = re.compile(
    r"\b(?:all|every) projects?\b|所有项目|每个项目|全局(?:都|适用)", re.IGNORECASE
)


class ExtractionError(RuntimeError):
    pass


class _RawCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: MemoryAction
    type: MemoryType
    scope: MemoryScope
    target_ids: tuple[str, ...] = ()
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=800)
    application: str = Field(min_length=1, max_length=600)
    body: str = Field(default="", max_length=4000)
    reason: str = Field(min_length=1, max_length=800)
    evidence: str = Field(min_length=1, max_length=800)


class _ExtractionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: tuple[_RawCandidate, ...] = Field(max_length=MAX_CANDIDATES_PER_TURN)


def redact_text(content: str, sensitive_values: Sequence[str]) -> str:
    for value in sorted((value for value in sensitive_values if value), key=len, reverse=True):
        content = content.replace(value, "[REDACTED]")
    return content


def contains_secret(content: str) -> bool:
    return "[REDACTED]" in content or any(pattern.search(content) for pattern in SECRET_PATTERNS)


class MemoryExtractor:
    def __init__(self, provider: ChatProvider, sensitive_values: Sequence[str] = ()) -> None:
        self.provider = provider
        self.sensitive_values = tuple(sensitive_values)

    def update_sensitive_values(self, values: Sequence[str]) -> None:
        self.sensitive_values = tuple(values)

    async def extract(
        self,
        turn: CompletedTurn,
        active_index: str,
        active_records: Sequence[MemoryRecord] = (),
    ) -> tuple[MemoryProposal, ...]:
        user_text = redact_text(turn.user_text, self.sensitive_values)
        final_text = redact_text(turn.final_text, self.sensitive_values)
        index = redact_text(active_index, self.sensitive_values)
        request = json.dumps(
            {"user": user_text, "assistant": final_text, "active_memory_index": index},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        text = ""
        completed = False
        async for event in self.provider.stream(
            (StableSystemMessage(EXTRACTION_PROMPT), UserMessage(request)),
            (),
            tool_choice="none",
        ):
            if isinstance(event, TextDelta):
                text += event.text
            elif isinstance(event, StreamCompleted):
                completed = True
        if not completed:
            raise ExtractionError("Memory extraction stream ended without completion.")
        try:
            raw = json.loads(text.strip())
            envelope = _ExtractionEnvelope.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ExtractionError(f"Invalid memory extraction JSON: {exc}") from exc

        known = {record.id: record for record in active_records}
        proposals: list[MemoryProposal] = []
        for item in envelope.candidates:
            scope = item.scope
            if (
                item.type == MemoryType.FEEDBACK
                and scope == MemoryScope.USER
                and not (GLOBAL_FEEDBACK.search(turn.user_text))
            ):
                scope = MemoryScope.PROJECT
            if item.action != MemoryAction.CREATE:
                targets = [known.get(target) for target in item.target_ids]
                if any(target is None or target.scope != scope for target in targets):
                    continue
            sensitive_content = "\n".join(
                (item.title, item.summary, item.application, item.body, item.evidence)
            )
            if contains_secret(redact_text(sensitive_content, self.sensitive_values)):
                continue
            values = {
                "action": item.action.value,
                "type": item.type.value,
                "scope": scope.value,
                "target_ids": list(item.target_ids),
                "title": item.title.strip(),
                "summary": item.summary.strip(),
                "application": item.application.strip(),
                "body": item.body.strip(),
                "source_turn_hash": turn.turn_hash,
            }
            try:
                proposals.append(
                    MemoryProposal(
                        id=proposal_id(values),
                        action=item.action,
                        type=item.type,
                        scope=scope,
                        target_ids=item.target_ids,
                        title=item.title.strip(),
                        summary=item.summary.strip(),
                        application=item.application.strip(),
                        body=item.body.strip(),
                        reason=item.reason.strip(),
                        evidence=item.evidence.strip(),
                        source_session_id=turn.session_id,
                        source_turn_hash=turn.turn_hash,
                        created_at=now_timestamp(),
                    )
                )
            except ValidationError:
                continue
        return tuple(proposals)
