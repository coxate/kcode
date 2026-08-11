import asyncio

from kcode.config import SubAgentConfig
from kcode.conversation import Conversation
from kcode.events import AgentStopped, AgentStopReason, TokenUsage, TokenUsageUpdated
from kcode.permissions.models import ApprovalChoice, PermissionMode
from kcode.session import AgentSession
from kcode.subagents.approval import ApprovalBroker
from kcode.subagents.factory import ChildAgent
from kcode.subagents.manager import TaskManager
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
