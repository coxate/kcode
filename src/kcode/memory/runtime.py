from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from kcode.memory.extraction import MemoryExtractor, contains_secret, redact_text
from kcode.memory.governance import MemoryGovernor
from kcode.memory.models import (
    CLOSE_TIMEOUT_SECONDS,
    CompletedTurn,
    DecisionKind,
    MemoryAction,
    MemoryApplyResult,
    MemoryDecision,
    MemoryProposal,
    MemoryRecord,
    MemoryScope,
    MemorySnapshot,
    MemoryState,
    MemoryStatus,
    PromptMemoryResult,
    new_memory_id,
    now_timestamp,
)
from kcode.memory.prompting import render_index, render_prompt
from kcode.memory.signals import MemorySignalDetector
from kcode.memory.store import MemoryStore
from kcode.providers.base import ChatProvider


class MemoryCoordinator:
    def __init__(
        self,
        workspace: Path,
        provider: ChatProvider,
        *,
        home: Path | None = None,
        sensitive_values: Sequence[str] = (),
    ) -> None:
        self.workspace = workspace.resolve()
        self.provider = provider
        self._warnings: list[str] = []
        try:
            self.user_store: MemoryStore | None = MemoryStore(
                MemoryScope.USER,
                self.workspace,
                home=home,
                sensitive_values=sensitive_values,
            )
        except Exception as exc:
            self.user_store = None
            self._warnings.append(f"user long-term memory is unavailable: {exc}")
        try:
            self.project_store: MemoryStore | None = MemoryStore(
                MemoryScope.PROJECT,
                self.workspace,
                home=home,
                sensitive_values=sensitive_values,
            )
        except Exception as exc:
            self.project_store = None
            self._warnings.append(f"project long-term memory is unavailable: {exc}")
        self.extractor = MemoryExtractor(provider, sensitive_values)
        self.governor = MemoryGovernor(provider, sensitive_values)
        self.detector = MemorySignalDetector()
        self._snapshots: dict[MemoryScope, MemorySnapshot] = {
            MemoryScope.USER: MemorySnapshot(),
            MemoryScope.PROJECT: MemorySnapshot(),
        }
        self._queue: asyncio.Queue[CompletedTurn | None] = asyncio.Queue()
        self._notifications: asyncio.Queue[MemoryProposal] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._governance_tasks: set[asyncio.Task[None]] = set()
        self._accepting = True
        self._started = False
        self._successful_sessions: set[str] = set()

    def start(self) -> PromptMemoryResult:
        if self._started:
            return self.render_prompt()
        for scope, store in self._stores():
            try:
                snapshot = store.load()
                self._snapshots[scope] = snapshot
                self._warnings.extend(snapshot.warnings)
            except Exception as exc:
                self._warnings.append(f"{scope.value} long-term memory is unavailable: {exc}")
        self._started = True
        return self.render_prompt()

    def submit_turn(self, turn: CompletedTurn) -> bool:
        if not self._accepting:
            return False
        if not self._started:
            self.start()
        self._successful_sessions.add(turn.session_id)
        signal = self.detector.detect(turn)
        if not signal.matched:
            return False
        self._ensure_worker()
        self._queue.put_nowait(turn)
        return True

    def pending(self) -> tuple[MemoryProposal, ...]:
        return tuple(
            sorted(
                (
                    *self._snapshots[MemoryScope.USER].proposals,
                    *self._snapshots[MemoryScope.PROJECT].proposals,
                ),
                key=lambda proposal: (proposal.created_at, proposal.id),
            )
        )

    def records(self) -> tuple[MemoryRecord, ...]:
        return (
            *self._snapshots[MemoryScope.USER].records,
            *self._snapshots[MemoryScope.PROJECT].records,
        )

    @property
    def warnings(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self._warnings))

    async def next_proposal(self) -> MemoryProposal:
        return await self._notifications.get()

    async def apply(self, decision: MemoryDecision) -> MemoryApplyResult:
        proposal = next((item for item in self.pending() if item.id == decision.proposal_id), None)
        if proposal is None:
            return MemoryApplyResult(
                changed=False,
                prompt=self.render_prompt(),
                warnings=("The memory proposal no longer exists.",),
            )
        store = self._store(proposal.scope)
        warnings: list[str] = []
        try:
            if decision.kind == DecisionKind.REJECT:
                await asyncio.to_thread(store.resolve_proposal, proposal.id)
            else:
                approved = self._edited(proposal, decision)
                await self._apply_approved(store, approved)
                await asyncio.to_thread(store.resolve_proposal, proposal.id)
            self._reload(proposal.scope)
            return MemoryApplyResult(
                changed=decision.kind != DecisionKind.REJECT,
                prompt=self.render_prompt(),
                warnings=tuple(warnings),
            )
        except Exception as exc:
            warning = f"Could not apply {proposal.scope.value} memory proposal: {exc}"
            self._warnings.append(warning)
            return MemoryApplyResult(
                changed=False,
                prompt=self.render_prompt(),
                warnings=(warning,),
            )

    async def set_status(
        self,
        scope: MemoryScope,
        memory_id: str,
        status: MemoryStatus,
    ) -> MemoryApplyResult:
        try:
            await asyncio.to_thread(self._store(scope).set_status, memory_id, status)
            self._reload(scope)
            return MemoryApplyResult(changed=True, prompt=self.render_prompt())
        except Exception as exc:
            warning = f"Could not change memory status: {exc}"
            self._warnings.append(warning)
            return MemoryApplyResult(
                changed=False,
                prompt=self.render_prompt(),
                warnings=(warning,),
            )

    async def edit_record(
        self,
        scope: MemoryScope,
        memory_id: str,
        *,
        title: str,
        summary: str,
        application: str,
        body: str,
    ) -> MemoryApplyResult:
        try:
            store = self._store(scope)
            record = await asyncio.to_thread(store.get, memory_id)
            self._validate_content(title, summary, application, body)
            updated = record.model_copy(
                update={
                    "title": title.strip(),
                    "summary": summary.strip(),
                    "application": application.strip(),
                    "body": body.strip(),
                    "updated_at": now_timestamp(),
                }
            )
            MemoryRecord.model_validate(updated.model_dump())
            await asyncio.to_thread(store.save, updated)
            self._reload(scope)
            return MemoryApplyResult(changed=True, prompt=self.render_prompt())
        except Exception as exc:
            warning = f"Could not edit memory: {exc}"
            self._warnings.append(warning)
            return MemoryApplyResult(
                changed=False,
                prompt=self.render_prompt(),
                warnings=(warning,),
            )

    async def delete(self, scope: MemoryScope, memory_id: str) -> MemoryApplyResult:
        try:
            await asyncio.to_thread(self._store(scope).delete, memory_id)
            self._reload(scope)
            return MemoryApplyResult(changed=True, prompt=self.render_prompt())
        except Exception as exc:
            warning = f"Could not permanently delete memory: {exc}"
            self._warnings.append(warning)
            return MemoryApplyResult(
                changed=False,
                prompt=self.render_prompt(),
                warnings=(warning,),
            )

    def render_prompt(self) -> PromptMemoryResult:
        result = render_prompt(
            self._snapshots[MemoryScope.USER].records,
            self._snapshots[MemoryScope.PROJECT].records,
        )
        if result.warnings:
            self._warnings.extend(result.warnings)
        return result

    async def session_closed(self, session_id: str, reason: str) -> tuple[str, ...]:
        del reason
        if session_id not in self._successful_sessions:
            return ()
        self._successful_sessions.discard(session_id)
        warnings: list[str] = []
        for scope, store in self._stores():
            try:
                state = store.load_state()
                if session_id not in state.completed_session_ids:
                    state = state.model_copy(
                        update={
                            "completed_session_ids": (*state.completed_session_ids, session_id)
                        }
                    )
                    await asyncio.to_thread(store.save_state, state)
                    self._reload(scope)
                records = tuple(
                    record
                    for record in self._snapshots[scope].records
                    if record.status == MemoryStatus.ACTIVE
                )
                current_state = self._snapshots[scope].state
                if self.governor.due(records, current_state, time.time()):
                    task = asyncio.create_task(self._run_governance(scope, records, current_state))
                    self._governance_tasks.add(task)
                    task.add_done_callback(self._governance_tasks.discard)
            except Exception as exc:
                warnings.append(f"Could not update {scope.value} memory governance state: {exc}")
        self._warnings.extend(warnings)
        return tuple(warnings)

    def update_sensitive_values(self, values: Sequence[str]) -> None:
        self.extractor.update_sensitive_values(values)
        self.governor.update_sensitive_values(values)
        if self.user_store is not None:
            self.user_store.update_sensitive_values(values)
        if self.project_store is not None:
            self.project_store.update_sensitive_values(values)

    async def close(self) -> tuple[str, ...]:
        self._accepting = False
        warnings: list[str] = []
        if self._worker is not None:
            await self._queue.put(None)
            try:
                await asyncio.wait_for(self._worker, timeout=CLOSE_TIMEOUT_SECONDS)
            except TimeoutError:
                self._worker.cancel()
                with suppress(asyncio.CancelledError):
                    await self._worker
                warnings.append("Cancelled unfinished long-term memory extraction during exit.")
        for task in tuple(self._governance_tasks):
            task.cancel()
        if self._governance_tasks:
            await asyncio.gather(*self._governance_tasks, return_exceptions=True)
        self._warnings.extend(warnings)
        return tuple(warnings)

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        while True:
            turn = await self._queue.get()
            if turn is None:
                return
            try:
                active_records = tuple(
                    record
                    for record in self.records()
                    if record.status == MemoryStatus.ACTIVE
                )
                active_index = render_index(active_records)
                proposals = await self.extractor.extract(turn, active_index, active_records)
                for proposal in proposals:
                    try:
                        store = self._store(proposal.scope)
                        saved = await asyncio.to_thread(store.save_proposal, proposal)
                        if saved:
                            self._reload(proposal.scope)
                            await self._notifications.put(proposal)
                    except Exception as exc:
                        self._warnings.append(
                            f"Could not save {proposal.scope.value} memory proposal: {exc}"
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._warnings.append(f"Long-term memory extraction failed: {exc}")
            finally:
                self._queue.task_done()

    async def _run_governance(
        self,
        scope: MemoryScope,
        records: Sequence[MemoryRecord],
        state: MemoryState,
    ) -> None:
        store = self._store(scope)
        try:
            proposals = await self.governor.propose(records)
            for proposal in proposals:
                saved = await asyncio.to_thread(store.save_proposal, proposal)
                if saved:
                    await self._notifications.put(proposal)
            updated = state.model_copy(
                update={
                    "last_governed_at": time.time(),
                    "last_governance_failure_at": None,
                    "completed_session_ids": (),
                }
            )
            await asyncio.to_thread(store.save_state, updated)
            self._reload(scope)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failed = state.model_copy(update={"last_governance_failure_at": time.time()})
            with suppress(Exception):
                await asyncio.to_thread(store.save_state, failed)
            self._warnings.append(f"{scope.value} memory governance failed: {exc}")

    async def _apply_approved(self, store: MemoryStore, proposal: MemoryProposal) -> None:
        timestamp = now_timestamp()
        if proposal.action == MemoryAction.CREATE:
            record = MemoryRecord(
                id=new_memory_id(),
                type=proposal.type,
                scope=proposal.scope,
                title=proposal.title,
                summary=proposal.summary,
                application=proposal.application,
                body=proposal.body,
                source_session_id=proposal.source_session_id,
                source_turn_hash=proposal.source_turn_hash,
                created_at=timestamp,
                updated_at=timestamp,
            )
            await asyncio.to_thread(store.save, record)
            return
        if proposal.action == MemoryAction.INACTIVATE:
            await asyncio.to_thread(
                store.set_status,
                proposal.target_ids[0],
                MemoryStatus.INACTIVE,
            )
            return
        primary = await asyncio.to_thread(store.get, proposal.target_ids[0])
        updated = primary.model_copy(
            update={
                "type": proposal.type,
                "title": proposal.title,
                "summary": proposal.summary,
                "application": proposal.application,
                "body": proposal.body,
                "source_session_id": proposal.source_session_id,
                "source_turn_hash": proposal.source_turn_hash,
                "status": MemoryStatus.ACTIVE,
                "updated_at": timestamp,
            }
        )
        await asyncio.to_thread(store.save, updated)
        if proposal.action == MemoryAction.MERGE:
            for memory_id in proposal.target_ids[1:]:
                await asyncio.to_thread(store.set_status, memory_id, MemoryStatus.INACTIVE)

    @staticmethod
    def _edited_values(proposal: MemoryProposal, decision: MemoryDecision) -> dict[str, str]:
        return {
            "title": decision.title or proposal.title,
            "summary": decision.summary or proposal.summary,
            "application": decision.application or proposal.application,
            "body": proposal.body if decision.body is None else decision.body,
        }

    def _edited(self, proposal: MemoryProposal, decision: MemoryDecision) -> MemoryProposal:
        if decision.kind != DecisionKind.EDIT:
            self._validate_content(
                proposal.title,
                proposal.summary,
                proposal.application,
                proposal.body,
            )
            return proposal
        values = self._edited_values(proposal, decision)
        self._validate_content(*values.values())
        return proposal.model_copy(update=values)

    def _validate_content(self, *values: str) -> None:
        content = "\n".join(values)
        redacted = redact_text(content, self.extractor.sensitive_values)
        if contains_secret(redacted):
            raise ValueError("Memory content contains a credential or redacted secret.")

    def _reload(self, scope: MemoryScope) -> None:
        snapshot = self._store(scope).load()
        self._snapshots[scope] = snapshot
        self._warnings.extend(snapshot.warnings)

    def _store(self, scope: MemoryScope) -> MemoryStore:
        store = self.user_store if scope == MemoryScope.USER else self.project_store
        if store is None:
            raise RuntimeError(f"{scope.value} long-term memory is unavailable")
        return store

    def _stores(self):
        return tuple(
            (scope, store)
            for scope, store in (
                (MemoryScope.USER, self.user_store),
                (MemoryScope.PROJECT, self.project_store),
            )
            if store is not None
        )
