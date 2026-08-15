from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from kcode.conversation import SystemReminderMessage
from kcode.hooks.models import Hook, HookWarning

MAX_PROMPT_BYTES = 64 * 1024
MAX_ASYNC_HOOKS = 8


class HookSession(Protocol):
    executed_hook_ids: set[str]
    pending_hook_prompts: list[str]


@dataclass(slots=True)
class InMemoryHookSession:
    executed_hook_ids: set[str] = field(default_factory=set)
    pending_hook_prompts: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class HookTaskMetadata:
    hook_id: str
    event: str


class HookRuntime:
    def __init__(self) -> None:
        self.fallback_session = InMemoryHookSession()
        self._tasks: set[asyncio.Task[HookWarning | None]] = set()
        self._warnings: list[HookWarning] = []
        self._closed = False

    def session(self, value: HookSession | None) -> HookSession:
        return value or self.fallback_session

    def should_run(self, session: HookSession | None, hook: Hook) -> bool:
        return not hook.once or hook.id not in self.session(session).executed_hook_ids

    def mark_executed(self, session: HookSession | None, hook: Hook) -> None:
        if hook.once:
            self.session(session).executed_hook_ids.add(hook.id)

    def enqueue_prompts(
        self, session: HookSession | None, prompts: Sequence[str]
    ) -> tuple[HookWarning, ...]:
        target = self.session(session)
        candidate = [*target.pending_hook_prompts, *prompts]
        size = sum(len(item.encode("utf-8")) for item in candidate)
        if size > MAX_PROMPT_BYTES:
            return (HookWarning("prompt_limit", "Hook prompts exceed 64 KiB"),)
        target.pending_hook_prompts = candidate
        return ()

    def take_prompts(self, session: HookSession | None) -> tuple[SystemReminderMessage, ...]:
        target = self.session(session)
        prompts = tuple(SystemReminderMessage("hook", item) for item in target.pending_hook_prompts)
        target.pending_hook_prompts.clear()
        return prompts

    def spawn(self, operation: Awaitable[HookWarning | None]) -> bool:
        if self._closed or len(self._tasks) >= MAX_ASYNC_HOOKS:
            close = getattr(operation, "close", None)
            if close is not None:
                close()
            return False
        task = asyncio.create_task(operation)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return True

    def _task_done(self, task: asyncio.Task[HookWarning | None]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        try:
            warning = task.result()
        except Exception as exc:
            warning = HookWarning(
                "async_failed", f"background action failed ({type(exc).__name__})"
            )
        if warning is not None:
            self._warnings.append(warning)

    def add_warning(self, warning: HookWarning) -> None:
        self._warnings.append(warning)

    def drain_warnings(self) -> tuple[HookWarning, ...]:
        warnings = tuple(self._warnings)
        self._warnings.clear()
        return warnings

    async def close(self) -> tuple[HookWarning, ...]:
        if self._closed:
            return self.drain_warnings()
        self._closed = True
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=0.25)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        return self.drain_warnings()
