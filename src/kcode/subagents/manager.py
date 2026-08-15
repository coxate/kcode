from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from kcode.config import SubAgentConfig
from kcode.events import (
    AgentStopped,
    AgentStopReason,
    TokenUsage,
    TokenUsageUpdated,
)
from kcode.subagents.approval import ApprovalBroker
from kcode.subagents.factory import ChildAgent
from kcode.subagents.models import TaskKind, TaskNotification, TaskStatus
from kcode.tools.base import ApprovalRequest, JSONValue

MAX_RESULT_BYTES = 32 * 1024
TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}


def _truncate(value: str, limit: int = MAX_RESULT_BYTES) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    suffix = "\n[truncated]"
    suffix_raw = suffix.encode("utf-8")
    if limit <= len(suffix_raw):
        return suffix_raw[:limit].decode("utf-8", errors="ignore")
    budget = limit - len(suffix_raw)
    return raw[:budget].decode("utf-8", errors="ignore") + suffix


@dataclass(slots=True)
class TaskRecord:
    id: str
    name: str
    child: ChildAgent
    prompt: str
    background: bool
    created_at: float = field(default_factory=time.monotonic)
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    error: str = ""
    usage: TokenUsage = TokenUsage(0, 0, 0, 0, 0)
    task: asyncio.Task[None] | None = None
    detach_event: asyncio.Event = field(default_factory=asyncio.Event)
    finalizer: TaskFinalizer | None = None
    finalizer_consumed: bool = False
    warnings: tuple[str, ...] = ()
    kind: TaskKind = TaskKind.SUBAGENT
    retain_on_success: bool = False
    pinned: bool = False
    completion_callback: TaskCompletionCallback | None = None
    finalization_details: Mapping[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LaunchResult:
    task_id: str
    status: str
    result: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskFinalization:
    suffix: str = ""
    warnings: tuple[str, ...] = ()
    details: Mapping[str, JSONValue] = field(default_factory=dict)


TaskFinalizer = Callable[[TaskRecord], Awaitable[TaskFinalization]]
TaskCompletionCallback = Callable[[TaskRecord], Awaitable[None]]


class TaskManager:
    def __init__(
        self,
        config: SubAgentConfig,
        broker: ApprovalBroker,
        *,
        sensitive_values: tuple[str, ...] = (),
        usage_callback: Callable[[TokenUsage], None] | None = None,
        notice_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.config = config
        self.broker = broker
        self.sensitive_values = sensitive_values
        self.usage_callback = usage_callback
        self.notice_callback = notice_callback
        self._records: dict[str, TaskRecord] = {}
        self._notifications: list[str] = []
        self._semaphore = asyncio.Semaphore(config.max_running)
        self._closed = False
        self._foreground_id: str | None = None

    def _redact(self, value: str) -> str:
        for secret in self.sensitive_values:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return _truncate(value)

    def _append_finalization(self, value: str, suffix: str) -> str:
        redacted_suffix = suffix
        for secret in self.sensitive_values:
            if secret:
                redacted_suffix = redacted_suffix.replace(secret, "[REDACTED]")
        suffix_bytes = redacted_suffix.encode("utf-8")
        if len(suffix_bytes) >= MAX_RESULT_BYTES:
            return _truncate(redacted_suffix)
        separator = "\n\n" if value else ""
        budget = MAX_RESULT_BYTES - len(suffix_bytes) - len(separator.encode("utf-8"))
        return f"{_truncate(value, budget)}{separator}{redacted_suffix}"

    def make_task_id(self) -> str:
        return f"task-{uuid.uuid4().hex[:12]}"

    def _evict_for_capacity(self) -> None:
        if len(self._records) < self.config.max_retained:
            return
        ended = sorted(
            (
                item
                for item in self._records.values()
                if item.status in TERMINAL_STATUSES and not item.pinned
            ),
            key=lambda item: item.created_at,
        )
        if not ended:
            raise RuntimeError("SubAgent task retention limit reached.")
        self._records.pop(ended[0].id, None)

    def _active_count(self) -> int:
        return sum(item.status not in TERMINAL_STATUSES for item in self._records.values())

    def _new_record(
        self,
        child: ChildAgent,
        prompt: str,
        name: str,
        *,
        background: bool,
        task_id: str | None = None,
        finalizer: TaskFinalizer | None = None,
        kind: TaskKind = TaskKind.SUBAGENT,
        retain_on_success: bool = False,
        pinned: bool = False,
        completion_callback: TaskCompletionCallback | None = None,
    ) -> TaskRecord:
        if self._closed:
            raise RuntimeError("SubAgent task manager is closed.")
        if self._active_count() >= self.config.max_running:
            raise RuntimeError("At most the configured number of SubAgents may run.")
        self._evict_for_capacity()
        task_id = task_id or self.make_task_id()
        if task_id in self._records:
            raise RuntimeError("SubAgent task ID is already in use.")
        record = TaskRecord(
            task_id,
            self._redact(name),
            child,
            prompt,
            background,
            finalizer=finalizer,
            kind=kind,
            retain_on_success=retain_on_success,
            pinned=pinned,
            completion_callback=completion_callback,
        )
        self._records[record.id] = record
        return record

    async def _finalize(self, record: TaskRecord) -> None:
        if record.finalizer is None or record.finalizer_consumed:
            return
        record.finalizer_consumed = True
        try:
            finalized = await record.finalizer(record)
        except asyncio.CancelledError:
            record.warnings = (
                *record.warnings,
                "Task finalizer was interrupted; resources were kept.",
            )
            return
        except Exception:
            record.warnings = (*record.warnings, "Task finalizer failed; resources were kept.")
            return
        record.warnings = (
            *record.warnings,
            *(self._redact(warning) for warning in finalized.warnings),
        )
        record.finalization_details = finalized.details
        if finalized.suffix:
            if record.result:
                record.result = self._append_finalization(record.result, finalized.suffix)
            else:
                record.error = self._append_finalization(record.error, finalized.suffix)

    def _approval(self, record: TaskRecord):
        async def approve(request: ApprovalRequest):
            if record.background or record.detach_event.is_set():
                record.status = TaskStatus.WAITING_APPROVAL
                try:
                    return await self.broker.request_background(
                        record.id,
                        record.name,
                        request,
                    )
                finally:
                    if record.status is TaskStatus.WAITING_APPROVAL:
                        record.status = TaskStatus.RUNNING
            return await self.broker.request_foreground(record.id, record.name, request)

        return approve

    async def _run(self, record: TaskRecord) -> None:
        stopped: AgentStopped | None = None
        try:
            async with self._semaphore:
                record.status = TaskStatus.RUNNING
                record.child.runner.approve = self._approval(record)
                async for event in record.child.runner.run(record.prompt, record.child.session):
                    if isinstance(event, TokenUsageUpdated):
                        record.usage = record.usage.plus(event.request)
                        if self.usage_callback is not None:
                            self.usage_callback(event.request)
                    elif isinstance(event, AgentStopped):
                        stopped = event
            turns = record.child.conversation.snapshot()
            if stopped is not None and stopped.reason is AgentStopReason.COMPLETED and turns:
                record.status = TaskStatus.COMPLETED
                record.result = self._redact(turns[-1].assistant)
            elif stopped is not None and stopped.reason is AgentStopReason.CANCELLED:
                record.status = TaskStatus.CANCELLED
                record.error = self._redact(stopped.detail or "Task cancelled.")
            else:
                record.status = TaskStatus.FAILED
                detail = stopped.detail if stopped is not None else "Task ended without a result."
                record.error = self._redact(detail or "SubAgent failed.")
        except asyncio.CancelledError:
            record.child.runner.cancel()
            record.status = TaskStatus.CANCELLED
            record.error = "Task cancelled."
        except Exception as exc:
            record.status = TaskStatus.FAILED
            record.error = self._redact(f"SubAgent failed ({type(exc).__name__}).")
        finally:
            if record.status is not TaskStatus.COMPLETED or not record.retain_on_success:
                await self._finalize(record)
            self.broker.cancel_task(record.id)
            if record.completion_callback is not None:
                try:
                    await record.completion_callback(record)
                except asyncio.CancelledError:
                    record.warnings = (
                        *record.warnings,
                        "Task completion callback was interrupted.",
                    )
                except Exception:
                    record.warnings = (*record.warnings, "Task completion callback failed.")
            if record.kind is TaskKind.SUBAGENT and (
                record.background or record.detach_event.is_set()
            ):
                result = record.result or record.error
                self._notifications.append(
                    TaskNotification(
                        record.id,
                        record.name,
                        record.status,
                        result,
                        record.usage,
                    ).render()
                )
                if self.notice_callback is not None:
                    self.notice_callback(
                        f"SubAgent {record.name} ({record.id}) {record.status.value}."
                    )

    async def launch(
        self,
        child: ChildAgent,
        prompt: str,
        name: str,
        *,
        background: bool,
        task_id: str | None = None,
        finalizer: TaskFinalizer | None = None,
        kind: TaskKind = TaskKind.SUBAGENT,
        retain_on_success: bool = False,
        pinned: bool = False,
        completion_callback: TaskCompletionCallback | None = None,
    ) -> LaunchResult:
        record = self._new_record(
            child,
            prompt,
            name,
            background=background,
            task_id=task_id,
            finalizer=finalizer,
            kind=kind,
            retain_on_success=retain_on_success,
            pinned=pinned,
            completion_callback=completion_callback,
        )
        record.task = asyncio.create_task(self._run(record))
        if background:
            return LaunchResult(record.id, "async_launched", warnings=record.warnings)
        self._foreground_id = record.id
        detach_task = asyncio.create_task(record.detach_event.wait())
        timer_task = (
            asyncio.create_task(asyncio.sleep(self.config.auto_background_seconds))
            if self.config.background_enabled
            else None
        )
        waiters = {record.task, detach_task}
        if timer_task is not None:
            waiters.add(timer_task)
        try:
            done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            if record.task in done:
                self._records.pop(record.id, None)
                return LaunchResult(
                    record.id,
                    record.status.value,
                    record.result or record.error,
                    record.warnings,
                )
            record.background = True
            reason = "moved_to_background" if detach_task in done else "timed_out_to_background"
            return LaunchResult(record.id, reason, warnings=record.warnings)
        except asyncio.CancelledError:
            if not record.detach_event.is_set():
                record.child.runner.cancel()
            record.background = True
            raise
        finally:
            self._foreground_id = None
            detach_task.cancel()
            if timer_task is not None:
                timer_task.cancel()

    def detach_foreground(self) -> bool:
        if self._foreground_id is None:
            return False
        record = self._records.get(self._foreground_id)
        if record is None or record.status in TERMINAL_STATUSES:
            return False
        record.detach_event.set()
        return True

    def summaries(self, kind: TaskKind = TaskKind.SUBAGENT) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "task_id": item.id,
                "name": item.name,
                "status": item.status.value,
                "tokens": item.usage.total_tokens,
            }
            for item in sorted(self._records.values(), key=lambda value: value.created_at)
            if item.kind is kind
        )

    def get(
        self,
        task_id: str,
        expected_kind: TaskKind = TaskKind.SUBAGENT,
    ) -> TaskRecord | None:
        record = self._records.get(task_id)
        return record if record is not None and record.kind is expected_kind else None

    def stop(self, task_id: str, expected_kind: TaskKind = TaskKind.SUBAGENT) -> bool:
        record = self.get(task_id, expected_kind)
        if record is None or record.status in TERMINAL_STATUSES:
            return False
        record.child.runner.cancel()
        self.broker.cancel_task(task_id)
        return True

    async def send_message(self, task_id: str, message: str) -> LaunchResult:
        return await self.resume_retained(task_id, message, expected_kind=TaskKind.SUBAGENT)

    async def resume_retained(
        self,
        task_id: str,
        message: str,
        *,
        expected_kind: TaskKind,
    ) -> LaunchResult:
        record = self.get(task_id, expected_kind)
        if record is None or record.status is not TaskStatus.COMPLETED:
            raise ValueError("Only a retained completed task can receive another message.")
        if record.finalizer_consumed:
            raise ValueError("A finalized isolated task cannot receive another message.")
        if self._active_count() >= self.config.max_running:
            raise RuntimeError("At most the configured number of SubAgents may run.")
        record.prompt = message
        record.background = True
        record.status = TaskStatus.PENDING
        record.result = ""
        record.error = ""
        record.task = asyncio.create_task(self._run(record))
        return LaunchResult(record.id, "async_launched", warnings=record.warnings)

    async def wait(
        self,
        task_id: str,
        timeout: float,
        *,
        expected_kind: TaskKind,
    ) -> TaskRecord | None:
        record = self.get(task_id, expected_kind)
        if record is None or record.task is None:
            return record
        try:
            await asyncio.wait_for(asyncio.shield(record.task), timeout)
        except TimeoutError:
            return record
        return record

    async def finalize_retained(
        self,
        task_id: str,
        *,
        expected_kind: TaskKind,
    ) -> TaskRecord:
        record = self.get(task_id, expected_kind)
        if record is None:
            raise ValueError("Task is missing or belongs to another task category.")
        if record.status not in TERMINAL_STATUSES:
            raise ValueError("Only a terminal retained task can be finalized.")
        await self._finalize(record)
        return record

    def release(self, task_id: str, *, expected_kind: TaskKind) -> bool:
        record = self.get(task_id, expected_kind)
        if record is None or record.status not in TERMINAL_STATUSES:
            return False
        if record.task is not None and not record.task.done():
            return False
        if record.finalizer is not None and not record.finalizer_consumed:
            return False
        self._records.pop(task_id, None)
        return True

    def can_launch(self) -> bool:
        if self._closed or self._active_count() >= self.config.max_running:
            return False
        if len(self._records) < self.config.max_retained:
            return True
        return any(
            item.status in TERMINAL_STATUSES and not item.pinned for item in self._records.values()
        )

    def take_notifications(self) -> tuple[str, ...]:
        notifications = tuple(self._notifications)
        self._notifications.clear()
        return notifications

    @property
    def running_count(self) -> int:
        return self._active_count()

    @property
    def waiting_approval_count(self) -> int:
        return sum(item.status is TaskStatus.WAITING_APPROVAL for item in self._records.values())

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for record in self._records.values():
            if record.status not in TERMINAL_STATUSES:
                record.child.runner.cancel()
        tasks = tuple(item.task for item in self._records.values() if item.task is not None)
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=4.5)
            for task in pending:
                task.cancel()
            if pending:
                _done, still_pending = await asyncio.wait(pending, timeout=0.5)
                for task in still_pending:
                    task.cancel()
                if still_pending:
                    await asyncio.gather(*still_pending, return_exceptions=True)
        for record in tuple(self._records.values()):
            if record.status in TERMINAL_STATUSES and not record.finalizer_consumed:
                await self._finalize(record)
        await self.broker.close()
