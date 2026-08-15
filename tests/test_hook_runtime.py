from __future__ import annotations

import asyncio

from kcode.hooks.runtime import HookRuntime, InMemoryHookSession


def test_prompt_queue_is_atomic_and_consumed_once() -> None:
    runtime = HookRuntime()
    session = InMemoryHookSession()
    assert runtime.enqueue_prompts(session, ("first",)) == ()
    warnings = runtime.enqueue_prompts(session, ("x" * (64 * 1024),))
    assert warnings
    assert [item.content for item in runtime.take_prompts(session)] == ["first"]
    assert runtime.take_prompts(session) == ()


async def test_async_limit_and_close_cleanup() -> None:
    runtime = HookRuntime()
    gate = asyncio.Event()

    async def operation():
        await gate.wait()
        return None

    assert all(runtime.spawn(operation()) for _ in range(8))
    assert not runtime.spawn(operation())
    await runtime.close()
