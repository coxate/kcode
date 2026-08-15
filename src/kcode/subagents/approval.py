from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from kcode.permissions.models import ApprovalChoice
from kcode.tools.base import ApprovalHandler, ApprovalRequest


@dataclass(frozen=True, slots=True)
class ApprovalTicket:
    id: int
    task_id: str
    request: ApprovalRequest


class ApprovalBroker:
    def __init__(self, foreground: ApprovalHandler) -> None:
        self.foreground = foreground
        self._queue: asyncio.Queue[ApprovalTicket] = asyncio.Queue()
        self._pending: dict[int, asyncio.Future[ApprovalChoice]] = {}
        self._tickets: dict[int, ApprovalTicket] = {}
        self._next_id = 1
        self._closed = False

    @staticmethod
    def decorate(
        task_id: str,
        name: str,
        request: ApprovalRequest,
    ) -> ApprovalRequest:
        return replace(
            request,
            source_label=f"SubAgent {name} ({task_id})",
            task_id=task_id,
        )

    async def request_foreground(
        self,
        task_id: str,
        name: str,
        request: ApprovalRequest,
    ) -> ApprovalChoice:
        return await self.foreground(self.decorate(task_id, name, request))

    async def request_background(
        self,
        task_id: str,
        name: str,
        request: ApprovalRequest,
    ) -> ApprovalChoice:
        if self._closed:
            return ApprovalChoice.DENY
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalChoice] = loop.create_future()
        ticket_id = self._next_id
        self._next_id += 1
        ticket = ApprovalTicket(
            ticket_id,
            task_id,
            self.decorate(task_id, name, request),
        )
        self._pending[ticket_id] = future
        self._tickets[ticket_id] = ticket
        await self._queue.put(ticket)
        try:
            return await future
        except asyncio.CancelledError:
            self._pending.pop(ticket_id, None)
            self._tickets.pop(ticket_id, None)
            raise

    async def next_ticket(self) -> ApprovalTicket:
        while True:
            ticket = await self._queue.get()
            if ticket.id in self._pending:
                return ticket

    def resolve(self, ticket_id: int, choice: ApprovalChoice) -> bool:
        future = self._pending.pop(ticket_id, None)
        self._tickets.pop(ticket_id, None)
        if future is None or future.done():
            return False
        future.set_result(choice)
        return True

    def is_pending(self, ticket_id: int) -> bool:
        future = self._pending.get(ticket_id)
        return future is not None and not future.done()

    def cancel_task(self, task_id: str) -> None:
        for ticket_id, future in tuple(self._pending.items()):
            ticket = self._tickets.get(ticket_id)
            if ticket is not None and ticket.task_id == task_id and not future.done():
                future.set_result(ApprovalChoice.DENY)
                self._pending.pop(ticket_id, None)
                self._tickets.pop(ticket_id, None)

    async def close(self) -> None:
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_result(ApprovalChoice.DENY)
        self._pending.clear()
        self._tickets.clear()
