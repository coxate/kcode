import asyncio

from kcode.permissions.models import ApprovalChoice
from kcode.subagents.approval import ApprovalBroker
from kcode.tools.base import ApprovalRequest


def request() -> ApprovalRequest:
    return ApprovalRequest("call", "write_file", "x", "confirm", "Write(x)")


async def test_background_approvals_are_fifo_and_decorated() -> None:
    async def foreground(value):
        assert value.source_label == "SubAgent worker (task-000000000001)"
        return ApprovalChoice.ALLOW_ONCE

    broker = ApprovalBroker(foreground)
    assert (
        await broker.request_foreground("task-000000000001", "worker", request())
        is ApprovalChoice.ALLOW_ONCE
    )
    first = asyncio.create_task(broker.request_background("task-000000000001", "one", request()))
    second = asyncio.create_task(broker.request_background("task-000000000002", "two", request()))
    one = await broker.next_ticket()
    two = await broker.next_ticket()
    assert (one.task_id, two.task_id) == ("task-000000000001", "task-000000000002")
    broker.resolve(one.id, ApprovalChoice.ALLOW_ONCE)
    broker.resolve(two.id, ApprovalChoice.DENY)
    assert await first is ApprovalChoice.ALLOW_ONCE
    assert await second is ApprovalChoice.DENY
    await broker.close()


async def test_cancelled_task_removes_queued_approval() -> None:
    async def foreground(_request):
        return ApprovalChoice.DENY

    broker = ApprovalBroker(foreground)
    pending = asyncio.create_task(
        broker.request_background("task-000000000001", "worker", request())
    )
    await asyncio.sleep(0)
    broker.cancel_task("task-000000000001")
    assert await pending is ApprovalChoice.DENY
    await broker.close()
