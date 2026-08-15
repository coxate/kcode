from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kcode.hooks.engine import HookEngine
from kcode.hooks.models import (
    HookCatalog,
    HookContext,
    HookEvent,
    HookSource,
    ToolRejectedError,
)
from kcode.hooks.parser import parse_hook
from kcode.hooks.runtime import InMemoryHookSession
from kcode.permissions.models import PermissionMode


def make_hook(value, order=0):
    hook, warning = parse_hook(value, HookSource.USER, Path("hooks.yaml"), order)
    assert warning is None and hook is not None
    return hook


def context(event=HookEvent.PRE_TOOL_USE, mode=PermissionMode.DEFAULT):
    return HookContext(
        event,
        "session",
        Path.cwd(),
        mode,
        tool_name="write_file",
        tool_args={"path": "protected.txt"},
        file_path="protected.txt",
    )


async def test_reject_only_returns_reason_and_stops_following_hooks() -> None:
    reject = make_hook(
        {
            "id": "protect",
            "event": "pre_tool_use",
            "if": 'args.path == "protected.txt"',
            "reject": True,
            "reason": "protected: $FILE_PATH",
            "once": True,
        }
    )
    later = make_hook(
        {
            "id": "later",
            "event": "pre_tool_use",
            "action": {"type": "prompt", "message": "later"},
        },
        1,
    )
    session = InMemoryHookSession()
    result = await HookEngine(HookCatalog((reject, later))).run_pre_tool_hooks(context(), session)
    assert isinstance(result, ToolRejectedError)
    assert result.reason == "protected: protected.txt"
    assert session.executed_hook_ids == {"protect"}
    assert session.pending_hook_prompts == []


async def test_plan_mode_skips_command_but_runs_prompt() -> None:
    command = make_hook(
        {"id": "cmd", "event": "startup", "action": {"type": "command", "command": "true"}}
    )
    prompt = make_hook(
        {"id": "prompt", "event": "startup", "action": {"type": "prompt", "message": "hi"}},
        1,
    )
    engine = HookEngine(HookCatalog((command, prompt)))
    session = InMemoryHookSession()
    result = await engine.run_hooks(context(HookEvent.STARTUP, PermissionMode.PLAN), session)
    assert result.executed_ids == ("prompt",)
    assert result.warnings[0].code == "plan_mode"
    assert session.pending_hook_prompts == ["hi"]


async def test_reject_stays_fail_closed_when_its_action_fails() -> None:
    reject = make_hook(
        {
            "id": "protect",
            "event": "pre_tool_use",
            "action": {"type": "command", "command": "exit 7"},
            "reject": True,
            "reason": "policy denied this tool",
        }
    )
    engine = HookEngine(HookCatalog((reject,)))
    result = await engine.run_pre_tool_hooks(
        context(mode=PermissionMode.BYPASS_PERMISSIONS),
        InMemoryHookSession(),
    )
    assert isinstance(result, ToolRejectedError)
    assert result.reason == "policy denied this tool"
    warnings = engine.runtime.drain_warnings()
    assert warnings and warnings[0].code == "command_failed"


async def test_unexpected_action_failure_is_warning_but_cancellation_propagates() -> None:
    class ExplodingExecutor:
        sensitive_values = ()

        def update_sensitive_values(self, _values):
            pass

        async def execute(self, _hook, _context):
            raise RuntimeError("private failure detail")

        async def close(self):
            pass

    prompt = make_hook(
        {"id": "broken", "event": "startup", "action": {"type": "prompt", "message": "x"}}
    )
    engine = HookEngine(HookCatalog((prompt,)), executor=ExplodingExecutor())
    result = await engine.run_hooks(context(HookEvent.STARTUP))
    assert result.executed_ids == ("broken",)
    assert result.warnings[0].code == "action_failed"
    assert "private failure detail" not in result.warnings[0].render()

    class CancellingExecutor(ExplodingExecutor):
        async def execute(self, _hook, _context):
            raise asyncio.CancelledError

    engine = HookEngine(HookCatalog((prompt,)), executor=CancellingExecutor())
    with pytest.raises(asyncio.CancelledError):
        await engine.run_hooks(context(HookEvent.STARTUP))
