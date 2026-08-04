from __future__ import annotations

import asyncio
from dataclasses import dataclass

from kcode.tools.base import (
    ApprovalHandler,
    PreparedToolCall,
    ToolContext,
    ToolEffect,
    ToolResult,
)
from kcode.tools.executor import ToolExecutor


@dataclass(frozen=True, slots=True)
class ToolBatch:
    calls: tuple[PreparedToolCall, ...]
    concurrent: bool


class ToolScheduler:
    def __init__(self, executor: ToolExecutor, max_parallel: int = 4) -> None:
        if max_parallel < 1:
            raise ValueError("max_parallel must be positive.")
        self.executor = executor
        self.max_parallel = max_parallel

    def batches(self, calls: tuple[PreparedToolCall, ...]) -> tuple[ToolBatch, ...]:
        batches: list[ToolBatch] = []
        readers: list[PreparedToolCall] = []
        for call in calls:
            if call.effect == ToolEffect.READ_ONLY:
                readers.append(call)
                continue
            if readers:
                batches.append(ToolBatch(tuple(readers), True))
                readers.clear()
            batches.append(ToolBatch((call,), False))
        if readers:
            batches.append(ToolBatch(tuple(readers), True))
        return tuple(batches)

    async def execute_batch(
        self,
        batch: ToolBatch,
        context: ToolContext,
        approve: ApprovalHandler,
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[ToolResult, ...]:
        if not batch.concurrent:
            return (
                await self.executor.execute_prepared(
                    batch.calls[0], context, approve, cancel_event
                ),
            )
        semaphore = asyncio.Semaphore(self.max_parallel)
        results: list[ToolResult | None] = [None] * len(batch.calls)

        async def run_one(index: int, call: PreparedToolCall) -> None:
            async with semaphore:
                results[index] = await self.executor.execute_prepared(
                    call, context, approve, cancel_event
                )

        async with asyncio.TaskGroup() as group:
            for index, call in enumerate(batch.calls):
                group.create_task(run_one(index, call))
        return tuple(result for result in results if result is not None)

    async def execute(
        self,
        calls: tuple[PreparedToolCall, ...],
        context: ToolContext,
        approve: ApprovalHandler,
        cancel_event: asyncio.Event | None = None,
    ) -> tuple[ToolResult, ...]:
        results: list[ToolResult] = []
        for batch in self.batches(calls):
            results.extend(
                await self.execute_batch(batch, context, approve, cancel_event)
            )
        return tuple(results)
