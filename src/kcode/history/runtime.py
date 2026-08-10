from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, Sequence

from kcode.context import ContextManager
from kcode.conversation import ChatTurn, Conversation, SystemReminderMessage
from kcode.history.ids import create_session_id, session_path
from kcode.history.journal import SessionJournal, SessionLease
from kcode.history.models import LoadedSession, SessionMetadata, SessionSummary
from kcode.history.store import SessionStore
from kcode.prompting import build_session_resume_reminder
from kcode.providers.base import ChatProvider
from kcode.tools.base import ToolDefinition


class SessionResumeError(RuntimeError):
    pass


class SessionCloseListener(Protocol):
    async def session_closed(self, session_id: str, reason: str) -> tuple[str, ...]: ...


@dataclass(slots=True)
class SessionRuntime:
    metadata: SessionMetadata
    conversation: Conversation
    context_manager: ContextManager
    journal: SessionJournal
    resume_reminder: SystemReminderMessage | None = None
    active_skill_names: tuple[str, ...] = ()

    @property
    def session_id(self) -> str:
        return self.metadata.session_id

    def consume_resume_reminder(self) -> None:
        self.resume_reminder = None

    async def record_skill_state(self, names: tuple[str, ...]) -> bool:
        self.active_skill_names = names
        return await self.journal.append_skill_state(names)


@dataclass(frozen=True, slots=True)
class ResumeResult:
    runtime: SessionRuntime
    turns: tuple[ChatTurn, ...]
    warnings: tuple[str, ...]


class SessionCoordinator:
    def __init__(
        self,
        workspace_root: Path,
        provider: ChatProvider,
        *,
        sensitive_values: Sequence[str] = (),
        conversation: Conversation | None = None,
        close_listeners: Sequence[SessionCloseListener] = (),
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.provider = provider
        self.sensitive_values = tuple(sensitive_values)
        self.close_listeners = tuple(close_listeners)
        self._notified_sessions: set[str] = set()
        self.store = SessionStore(self.workspace_root)
        self.current = self._fresh_runtime(conversation=conversation)

    def list_sessions(self) -> tuple[SessionSummary, ...]:
        return self.store.list_sessions(exclude_session_id=self.current.session_id)

    async def new_session(self) -> SessionRuntime:
        self.current = self._fresh_runtime()
        return self.current

    async def clear(self) -> tuple[SessionRuntime, tuple[str, ...]]:
        warnings: list[str] = []
        if not await self.current.journal.close("clear"):
            warnings.append(
                "The previous session could not be fully saved: "
                f"{self.current.journal.failure_reason or 'unknown error'}"
            )
        warnings.extend(await self._notify_closed(self.current.session_id, "clear"))
        runtime = await self.new_session()
        return runtime, tuple(warnings)

    async def resume(
        self,
        session_id: str,
        *,
        tools: Sequence[ToolDefinition] = (),
    ) -> ResumeResult:
        if session_id == self.current.session_id:
            raise SessionResumeError("The selected session is already active.")
        directory = session_path(self.store.sessions_root, session_id)
        lease = SessionLease(directory)
        try:
            await asyncio.to_thread(lease.acquire)
            loaded = await asyncio.to_thread(self.store.load, session_id)
            candidate = self._runtime_from_loaded(loaded, lease)
            snapshot = await candidate.context_manager.build_snapshot(
                loaded.messages,
                tools,
                apply_offload=False,
            )
            result = snapshot.compaction_result
            if result is not None and not result.success:
                raise SessionResumeError(
                    f"Restored history could not be compacted: {result.failure_reason}"
                )
            if not snapshot.budget.fits_after_emergency:
                raise SessionResumeError("Restored history still exceeds the current model window.")
        except Exception:
            lease.release()
            raise

        warnings = list(loaded.warnings)
        if (
            loaded.metadata.provider != self.provider.display_name
            or loaded.metadata.model != self.provider.model_name
        ):
            warnings.append(
                "Resumed with current provider/model "
                f"{self.provider.display_name}/{self.provider.model_name}; the session was "
                f"created with {loaded.metadata.provider}/{loaded.metadata.model}."
            )
        if not await self.current.journal.close("resume"):
            warnings.append(
                "The previous session could not be fully saved: "
                f"{self.current.journal.failure_reason or 'unknown error'}"
            )
        warnings.extend(await self._notify_closed(self.current.session_id, "resume"))
        self.current = candidate
        return ResumeResult(candidate, loaded.turns, tuple(warnings))

    async def close(self) -> tuple[str, ...]:
        warnings: list[str] = []
        if not await self.current.journal.close("exit"):
            warnings.append(
                "The current session could not be fully saved: "
                f"{self.current.journal.failure_reason or 'unknown error'}"
            )
        warnings.extend(await self._notify_closed(self.current.session_id, "exit"))
        return tuple(warnings)

    def update_sensitive_values(self, values: Sequence[str]) -> None:
        self.sensitive_values = tuple(values)
        self.current.context_manager.update_sensitive_values(values)
        self.current.journal.update_sensitive_values(values)

    def _fresh_runtime(self, *, conversation: Conversation | None = None) -> SessionRuntime:
        session_id = create_session_id()
        metadata = SessionMetadata(
            schema=1,
            session_id=session_id,
            created_at=time.time(),
            provider=self.provider.display_name,
            model=self.provider.model_name,
        )
        context_manager = self._context_manager(session_id)
        journal = SessionJournal(
            self.workspace_root,
            metadata,
            sensitive_values=self.sensitive_values,
        )
        return SessionRuntime(metadata, conversation or Conversation(), context_manager, journal)

    def _runtime_from_loaded(
        self,
        loaded: LoadedSession,
        lease: SessionLease,
    ) -> SessionRuntime:
        conversation = Conversation()
        conversation.restore(loaded.messages)
        context_manager = self._context_manager(loaded.metadata.session_id)
        journal = SessionJournal(
            self.workspace_root,
            loaded.metadata,
            sensitive_values=self.sensitive_values,
            resume=True,
            lease=lease,
        )
        return SessionRuntime(
            replace(loaded.metadata),
            conversation,
            context_manager,
            journal,
            build_session_resume_reminder(loaded.last_active_at),
            loaded.active_skill_names,
        )

    def _context_manager(self, session_id: str) -> ContextManager:
        provider_config = getattr(self.provider, "config", None)
        configured_window = getattr(provider_config, "context_window", None)
        return ContextManager(
            self.workspace_root,
            session_id=session_id,
            provider=self.provider,
            context_window=configured_window,
            sensitive_values=self.sensitive_values,
        )

    async def _notify_closed(self, session_id: str, reason: str) -> tuple[str, ...]:
        if session_id in self._notified_sessions:
            return ()
        self._notified_sessions.add(session_id)
        warnings: list[str] = []
        for listener in self.close_listeners:
            try:
                warnings.extend(await listener.session_closed(session_id, reason))
            except Exception as exc:
                warnings.append(f"A session close listener failed: {exc}")
        return tuple(warnings)
