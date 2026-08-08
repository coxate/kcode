from __future__ import annotations

import hashlib
import json
import time
import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MEMORY_SCHEMA = 1
MAX_CANDIDATES_PER_TURN = 3
PROMPT_BUDGET_BYTES = 24 * 1024
PROMPT_BUDGET_LINES = 200
GOVERNANCE_MIN_RECORDS = 10
GOVERNANCE_MIN_SESSIONS = 5
GOVERNANCE_INTERVAL_SECONDS = 24 * 60 * 60
GOVERNANCE_RETRY_SECONDS = 10 * 60
PROCESSED_HASH_LIMIT = 500
CLOSE_TIMEOUT_SECONDS = 2.0


class MemoryType(StrEnum):
    USER_PREFERENCE = "user_preference"
    FEEDBACK = "feedback"
    PROJECT_FACT = "project_fact"
    REFERENCE = "reference"


class MemoryScope(StrEnum):
    USER = "user"
    PROJECT = "project"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class MemoryAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    MERGE = "merge"
    INACTIVATE = "inactivate"


class DecisionKind(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


class StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class MemoryRecord(StrictModel):
    schema_version: Literal[1] = Field(default=MEMORY_SCHEMA, alias="schema")
    id: str = Field(pattern=r"^mem_[0-9a-f]{32}$")
    type: MemoryType
    scope: MemoryScope
    status: MemoryStatus = MemoryStatus.ACTIVE
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=800)
    application: str = Field(min_length=1, max_length=600)
    body: str = Field(default="", max_length=4000)
    source_session_id: str = Field(min_length=1, max_length=128)
    source_turn_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: float = Field(gt=0)
    updated_at: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_scope(self) -> MemoryRecord:
        if self.type in {MemoryType.PROJECT_FACT, MemoryType.REFERENCE} and (
            self.scope != MemoryScope.PROJECT
        ):
            raise ValueError(f"{self.type.value} memories must use project scope")
        return self


class MemoryProposal(StrictModel):
    schema_version: Literal[1] = Field(default=MEMORY_SCHEMA, alias="schema")
    id: str = Field(pattern=r"^proposal_[0-9a-f]{64}$")
    action: MemoryAction
    type: MemoryType
    scope: MemoryScope
    target_ids: tuple[str, ...] = Field(default=(), max_length=8)
    title: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=800)
    application: str = Field(min_length=1, max_length=600)
    body: str = Field(default="", max_length=4000)
    reason: str = Field(min_length=1, max_length=800)
    evidence: str = Field(min_length=1, max_length=800)
    source_session_id: str = Field(min_length=1, max_length=128)
    source_turn_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_targets_and_scope(self) -> MemoryProposal:
        if self.action == MemoryAction.CREATE and self.target_ids:
            raise ValueError("create proposals cannot have targets")
        if self.action != MemoryAction.CREATE and not self.target_ids:
            raise ValueError(f"{self.action.value} proposals require targets")
        if self.action in {MemoryAction.UPDATE, MemoryAction.INACTIVATE} and len(
            self.target_ids
        ) != 1:
            raise ValueError(f"{self.action.value} proposals require exactly one target")
        if self.action == MemoryAction.MERGE and len(self.target_ids) < 2:
            raise ValueError("merge proposals require at least two targets")
        if self.type in {MemoryType.PROJECT_FACT, MemoryType.REFERENCE} and (
            self.scope != MemoryScope.PROJECT
        ):
            raise ValueError(f"{self.type.value} memories must use project scope")
        return self


class CompletedTurn(StrictModel):
    session_id: str = Field(min_length=1, max_length=128)
    user_text: str = Field(min_length=1, max_length=20_000)
    final_text: str = Field(min_length=1, max_length=40_000)
    permission_mode: str = Field(min_length=1, max_length=32)
    turn_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(
        cls,
        session_id: str,
        user_text: str,
        final_text: str,
        permission_mode: str,
    ) -> CompletedTurn:
        payload = json.dumps(
            [session_id, user_text, final_text],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        return cls(
            session_id=session_id,
            user_text=user_text,
            final_text=final_text,
            permission_mode=permission_mode,
            turn_hash=hashlib.sha256(payload).hexdigest(),
        )


class MemoryDecision(StrictModel):
    proposal_id: str = Field(pattern=r"^proposal_[0-9a-f]{64}$")
    kind: DecisionKind
    title: str | None = Field(default=None, min_length=1, max_length=100)
    summary: str | None = Field(default=None, min_length=1, max_length=800)
    application: str | None = Field(default=None, min_length=1, max_length=600)
    body: str | None = Field(default=None, max_length=4000)


class MemoryState(StrictModel):
    schema_version: Literal[1] = Field(default=MEMORY_SCHEMA, alias="schema")
    last_governed_at: float | None = Field(default=None, gt=0)
    last_governance_failure_at: float | None = Field(default=None, gt=0)
    completed_session_ids: tuple[str, ...] = ()
    processed_proposal_hashes: tuple[str, ...] = ()


class MemorySnapshot(StrictModel):
    records: tuple[MemoryRecord, ...] = ()
    proposals: tuple[MemoryProposal, ...] = ()
    state: MemoryState = MemoryState()
    warnings: tuple[str, ...] = ()


class PromptMemoryResult(StrictModel):
    content: str = ""
    excluded: int = 0
    warnings: tuple[str, ...] = ()


class MemoryApplyResult(StrictModel):
    changed: bool
    prompt: PromptMemoryResult
    warnings: tuple[str, ...] = ()


class SignalResult(StrictModel):
    matched: bool
    kinds: tuple[str, ...] = ()


def new_memory_id() -> str:
    return f"mem_{uuid.uuid4().hex}"


def proposal_id(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"proposal_{hashlib.sha256(canonical).hexdigest()}"


def now_timestamp() -> float:
    return time.time()
