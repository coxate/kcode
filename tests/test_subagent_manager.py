import asyncio

import pytest

from kcode.config import SubAgentConfig
from kcode.conversation import Conversation
from kcode.events import AgentStopped, AgentStopReason, TokenUsage, TokenUsageUpdated
from kcode.permissions.models import ApprovalChoice, PermissionMode
from kcode.session import AgentSession
from kcode.subagents.approval import ApprovalBroker
from kcode.subagents.factory import ChildAgent
from kcode.subagents.manager import TaskFinalization, TaskManager
from kcode.subagents.models import TaskKind
from kcode.tools.base import ApprovalRequest


async def allow(_request):
    return ApprovalChoice.ALLOW_ONCE


class StubRunner:
    def __init__(self, conversation: Conversation, gate: asyncio.Event | None = None) -> None:
        self.conversation = conversation
        self.gate = gate
        self.approve = allow
        self.cancelled = False

    async def run(self, prompt, session):
        if self.gate is not None:
            await self.gate.wait()
        if self.cancelled:
            yield AgentStopped(AgentStopReason.CANCELLED, 1, "cancelled")
            return
        self.conversation.commit(prompt, "done")
        usage = TokenUsage(3, 2, 5)
        yield TokenUsageUpdated(1, usage, usage)
        yield AgentStopped(AgentStopReason.COMPLETED, 1)

    def cancel(self):
        self.cancelled = True
        if self.gate is not None:
            self.gate.set()


def child(gate: asyncio.Event | None = None) -> ChildAgent:
    conversation = Conversation()
    return ChildAgent(
        StubRunner(conversation, gate),
        conversation,
        AgentSession(),
        PermissionMode.DEFAULT,
    )


async def test_manager_foreground_background_and_notifications() -> None:
    manager = TaskManager(
        SubAgentConfig(auto_background_seconds=1, max_running=2, max_retained=3),
        ApprovalBroker(allow),
    )
    foreground = await manager.launch(child(), "work", "worker", background=False)
    assert foreground.status == "completed"
    assert foreground.result == "done"
    assert manager.summaries() == ()

    background = await manager.launch(child(), "work", "worker", background=True)
    record = manager.get(background.task_id)
    await record.task
    assert record.status.value == "completed"
    notifications = manager.take_notifications()
    assert background.task_id in notifications[0]
    assert manager.take_notifications() == ()
    await manager.close()


async def test_manager_manual_detach_and_stop() -> None:
    gate = asyncio.Event()
    manager = TaskManager(SubAgentConfig(max_running=1), ApprovalBroker(allow))
    launch_task = asyncio.create_task(
        manager.launch(child(gate), "wait", "worker", background=False)
    )
    await asyncio.sleep(0)
    assert manager.detach_foreground()
    launched = await launch_task
    assert launched.status == "moved_to_background"
    assert manager.stop(launched.task_id)
    record = manager.get(launched.task_id)
    await record.task
    assert record.status.value == "cancelled"
    await manager.close()


async def test_background_approval_is_queued() -> None:
    broker = ApprovalBroker(allow)
    request = ApprovalRequest("call", "write_file", "x", "confirm", "Write(x)")
    waiting = asyncio.create_task(broker.request_background("task-000000000001", "worker", request))
    ticket = await broker.next_ticket()
    assert ticket.request.task_id == "task-000000000001"
    assert broker.resolve(ticket.id, ApprovalChoice.DENY)
    assert await waiting is ApprovalChoice.DENY
    await broker.close()


async def test_foreground_timeout_detaches_without_cancelling() -> None:
    gate = asyncio.Event()
    manager = TaskManager(
        SubAgentConfig(auto_background_seconds=0.1, max_running=1),
        ApprovalBroker(allow),
    )
    launched = await manager.launch(child(gate), "wait", "worker", background=False)
    assert launched.status == "timed_out_to_background"
    record = manager.get(launched.task_id)
    assert not record.child.runner.cancelled
    gate.set()
    await record.task
    assert record.status.value == "completed"
    await manager.close()


async def test_background_result_is_redacted_and_truncated() -> None:
    item = child()

    async def long_run(prompt, session):
        item.conversation.commit(prompt, "secret-value" + "x" * (33 * 1024))
        yield AgentStopped(AgentStopReason.COMPLETED, 1)

    item.runner.run = long_run
    manager = TaskManager(
        SubAgentConfig(),
        ApprovalBroker(allow),
        sensitive_values=("secret-value",),
    )
    launched = await manager.launch(item, "work", "worker", background=True)
    record = manager.get(launched.task_id)
    await record.task
    assert "secret-value" not in record.result
    assert "[REDACTED]" in record.result
    assert len(record.result.encode("utf-8")) <= 32 * 1024
    await manager.close()


async def test_finalizer_runs_once_and_appends_redacted_report() -> None:
    calls = 0

    async def finalize(_record):
        nonlocal calls
        calls += 1
        return TaskFinalization("report secret-value", ("kept",))

    manager = TaskManager(
        SubAgentConfig(),
        ApprovalBroker(allow),
        sensitive_values=("secret-value",),
    )
    launched = await manager.launch(child(), "work", "worker", background=True, finalizer=finalize)
    record = manager.get(launched.task_id)
    await record.task
    assert calls == 1
    assert "report [REDACTED]" in record.result
    assert record.warnings == ("kept",)
    with pytest.raises(ValueError, match="finalized isolated"):
        await manager.send_message(record.id, "again")
    await manager.close()


async def test_finalizer_failure_keeps_original_status() -> None:
    async def finalize(_record):
        raise RuntimeError("private detail")

    manager = TaskManager(SubAgentConfig(), ApprovalBroker(allow))
    launched = await manager.launch(child(), "work", "worker", background=True, finalizer=finalize)
    record = manager.get(launched.task_id)
    await record.task
    assert record.status.value == "completed"
    assert "private detail" not in " ".join(record.warnings)
    assert record.finalizer_consumed
    await manager.close()


async def test_finalizer_runs_for_cancelled_task() -> None:
    calls = 0

    async def finalize(_record):
        nonlocal calls
        calls += 1
        return TaskFinalization("cancel report")

    gate = asyncio.Event()
    manager = TaskManager(SubAgentConfig(), ApprovalBroker(allow))
    launched = await manager.launch(
        child(gate),
        "wait",
        "worker",
        background=True,
        finalizer=finalize,
    )
    assert manager.stop(launched.task_id)
    record = manager.get(launched.task_id)
    await record.task
    assert record.status.value == "cancelled"
    assert "cancel report" in record.error
    assert calls == 1
    await manager.close()


async def test_cancelled_foreground_emits_finalized_notification() -> None:
    gate = asyncio.Event()

    async def finalize(_record):
        return TaskFinalization("cancelled worktree report")

    manager = TaskManager(SubAgentConfig(), ApprovalBroker(allow))
    launching = asyncio.create_task(
        manager.launch(
            child(gate),
            "wait",
            "worker",
            background=False,
            finalizer=finalize,
        )
    )
    await asyncio.sleep(0)
    launching.cancel()
    with pytest.raises(asyncio.CancelledError):
        await launching
    record = next(iter(manager._records.values()))
    await record.task
    notification = manager.take_notifications()
    assert len(notification) == 1
    assert "cancelled worktree report" in notification[0]
    await manager.close()


async def test_long_result_keeps_finalization_report_at_end() -> None:
    item = child()

    async def long_run(prompt, session):
        item.conversation.commit(prompt, "x" * (40 * 1024))
        yield AgentStopped(AgentStopReason.COMPLETED, 1)

    async def finalize(_record):
        return TaskFinalization("<worktree-result>\nKept: true\n</worktree-result>")

    item.runner.run = long_run
    manager = TaskManager(SubAgentConfig(), ApprovalBroker(allow))
    launched = await manager.launch(
        item,
        "work",
        "worker",
        background=True,
        finalizer=finalize,
    )
    record = manager.get(launched.task_id)
    await record.task
    assert len(record.result.encode("utf-8")) <= 32 * 1024
    assert record.result.endswith("Kept: true\n</worktree-result>")
    assert "[truncated]" in record.result
    await manager.close()


def test_tiny_truncation_budget_is_bounded() -> None:
    from kcode.subagents.manager import _truncate

    assert len(_truncate("long-value", 4).encode("utf-8")) <= 4


async def test_team_member_completion_is_retained_and_hidden() -> None:
    finalized = 0
    completed = 0

    async def finalize(_record):
        nonlocal finalized
        finalized += 1
        return TaskFinalization("final", details={"kept": True})

    async def on_complete(_record):
        nonlocal completed
        completed += 1

    manager = TaskManager(SubAgentConfig(), ApprovalBroker(allow))
    launched = await manager.launch(
        child(),
        "first",
        "alice",
        background=True,
        kind=TaskKind.TEAM_MEMBER,
        retain_on_success=True,
        pinned=True,
        finalizer=finalize,
        completion_callback=on_complete,
    )
    record = manager.get(launched.task_id, TaskKind.TEAM_MEMBER)
    assert record is not None
    await record.task
    assert record.status.value == "completed"
    assert completed == 1
    assert finalized == 0
    assert manager.get(record.id) is None
    assert manager.summaries() == ()
    assert not manager.stop(record.id)

    await manager.resume_retained(record.id, "second", expected_kind=TaskKind.TEAM_MEMBER)
    await record.task
    assert len(record.child.conversation.snapshot()) == 2
    assert record.usage.total_tokens == 10
    assert completed == 2
    assert finalized == 0

    finalized_record = await manager.finalize_retained(
        record.id, expected_kind=TaskKind.TEAM_MEMBER
    )
    assert finalized_record.finalization_details == {"kept": True}
    assert finalized == 1
    assert manager.release(record.id, expected_kind=TaskKind.TEAM_MEMBER)
    await manager.close()


async def test_pinned_terminal_record_is_not_evicted() -> None:
    manager = TaskManager(SubAgentConfig(max_running=2, max_retained=2), ApprovalBroker(allow))
    team = await manager.launch(
        child(),
        "team",
        "alice",
        background=True,
        kind=TaskKind.TEAM_MEMBER,
        retain_on_success=True,
        pinned=True,
    )
    team_record = manager.get(team.task_id, TaskKind.TEAM_MEMBER)
    await team_record.task
    normal = await manager.launch(child(), "normal", "worker", background=True)
    normal_record = manager.get(normal.task_id)
    await normal_record.task
    replacement = await manager.launch(child(), "new", "new-worker", background=True)
    assert manager.get(team.task_id, TaskKind.TEAM_MEMBER) is team_record
    assert manager.get(normal.task_id) is None
    await manager.get(replacement.task_id).task
    await manager.close()
