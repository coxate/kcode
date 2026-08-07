import asyncio
import json
import sys
from pathlib import Path

from mcp import types

from kcode.mcp.tool import McpTool
from kcode.permissions import (
    ApprovalChoice,
    LocalPermissionStore,
    PermissionEngine,
    PermissionLayer,
    PermissionMode,
    PermissionSettings,
    empty_permission_settings,
)
from kcode.permissions.rules import parse_rule
from kcode.session import AgentMode
from kcode.tools.base import ToolCall, ToolContext, ToolEffect
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import create_default_registry


def make_executor(root: Path) -> ToolExecutor:
    settings = empty_permission_settings(root)
    return ToolExecutor(
        create_default_registry(),
        PermissionEngine(settings),
        LocalPermissionStore(settings.layers[0].path),
    )


class McpHandle:
    async def call_tool(self, name, arguments):
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"{name}:{arguments}")]
        )


def make_mcp_executor(root: Path, effect: ToolEffect) -> ToolExecutor:
    settings = empty_permission_settings(root)
    registry = create_default_registry()
    registry.register(
        McpTool(
            name="mcp__demo__action",
            remote_name="action",
            description="Demo action",
            parameters={"type": "object"},
            effect=effect,
            handle=McpHandle(),
            server_name="demo",
        )
    )
    return ToolExecutor(
        registry,
        PermissionEngine(settings),
        LocalPermissionStore(settings.layers[0].path),
    )


async def allow(_request):
    return True


async def deny(_request):
    return False


async def allow_always(_request):
    return ApprovalChoice.ALLOW_ALWAYS


async def test_executor_validates_unknown_and_bad_arguments(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    context = ToolContext(tmp_path)
    unknown = await executor.execute(ToolCall(0, "1", "nope", "{}"), context, allow)
    invalid = await executor.execute(ToolCall(0, "2", "read_file", "{"), context, allow)
    assert unknown.error.code == "unknown_tool"
    assert invalid.error.code == "invalid_arguments"


async def test_mcp_permissions_follow_declared_effect_and_mode(tmp_path: Path) -> None:
    approvals = 0

    async def count_approval(_request):
        nonlocal approvals
        approvals += 1
        return ApprovalChoice.ALLOW_ONCE

    call = ToolCall(0, "mcp", "mcp__demo__action", '{"value":"x"}')
    read_result = await make_mcp_executor(tmp_path, ToolEffect.READ_ONLY).execute(
        call, ToolContext(tmp_path), count_approval
    )
    assert read_result.status == "success"
    assert approvals == 0

    side_executor = make_mcp_executor(tmp_path, ToolEffect.SIDE_EFFECT)
    side_result = await side_executor.execute(call, ToolContext(tmp_path), count_approval)
    assert side_result.status == "success"
    assert approvals == 1

    bypass = await side_executor.execute(
        call,
        ToolContext(tmp_path),
        count_approval,
        PermissionMode.BYPASS_PERMISSIONS,
    )
    assert bypass.status == "success"
    assert approvals == 1

    planned = side_executor.prepare(call, ToolContext(tmp_path), PermissionMode.PLAN)
    assert planned.error.error.code == "plan_mode_denied"


async def test_mcp_permanent_allow_uses_exact_tool_name(tmp_path: Path) -> None:
    executor = make_mcp_executor(tmp_path, ToolEffect.SIDE_EFFECT)
    call = ToolCall(0, "mcp", "mcp__demo__action", "{}")
    result = await executor.execute(call, ToolContext(tmp_path), allow_always)
    assert result.status == "success"
    rules = (tmp_path / ".kcode" / "permissions.local.yaml").read_text(encoding="utf-8")
    assert "mcp__demo__action" in rules
    assert executor.prepare(call, ToolContext(tmp_path)).approval is None


async def test_external_write_is_denied_by_sandbox(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    executor = make_executor(workspace)
    context = ToolContext(workspace, sensitive_values=("super-secret",))
    call = ToolCall(
        0,
        "write-1",
        "write_file",
        '{"path":"%s","content":"super-secret"}' % outside,
    )
    rejected = await executor.execute(call, context, deny)
    assert rejected.status == "denied"
    assert not outside.exists()
    accepted = await executor.execute(call, context, allow)
    assert accepted.status == "denied"
    assert "super-secret" not in accepted.to_json()


async def test_symlink_escape_is_treated_as_external(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)
    executor = make_executor(workspace)
    call = ToolCall(
        0,
        "write-link",
        "write_file",
        json.dumps({"path": str(workspace / "link" / "escaped.txt"), "content": "x"}),
    )
    result = await executor.execute(call, ToolContext(workspace), deny)
    assert result.status == "denied"
    assert not (outside / "escaped.txt").exists()


async def test_command_timeout_returns_structured_result(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    command = f'{sys.executable} -c "import time; time.sleep(3)"'
    call = ToolCall(
        0,
        "slow-command",
        "run_command",
        json.dumps({"command": command, "cwd": str(tmp_path), "timeout_seconds": 1}),
    )
    result = await executor.execute(call, ToolContext(tmp_path), allow)
    assert result.status == "timeout"
    assert result.error.code == "timeout"


async def test_command_cancel_returns_structured_result_and_finishes_quickly(
    tmp_path: Path,
) -> None:
    executor = make_executor(tmp_path)
    command = f'{sys.executable} -c "import time; time.sleep(10)"'
    call = ToolCall(0, "cancel-command", "run_command", json.dumps({"command": command}))
    cancel = asyncio.Event()

    async def trigger_cancel() -> None:
        await asyncio.sleep(0.05)
        cancel.set()

    trigger = asyncio.create_task(trigger_cancel())
    result = await asyncio.wait_for(
        executor.execute_prepared(
            executor.prepare(call, ToolContext(tmp_path)),
            ToolContext(tmp_path),
            allow,
            cancel,
        ),
        2,
    )
    await trigger
    assert result.status == "cancelled"
    assert result.error.code == "cancelled"


async def test_cancel_during_approval_does_not_write(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    executor = make_executor(workspace)
    prepared = executor.prepare(
        ToolCall(
            0,
            "outside",
            "write_file",
            json.dumps({"path": "inside.txt", "content": "blocked"}),
        ),
        ToolContext(workspace),
    )
    cancel = asyncio.Event()

    async def waiting_approval(_request):
        await asyncio.sleep(10)
        return True

    task = asyncio.create_task(
        executor.execute_prepared(prepared, ToolContext(workspace), waiting_approval, cancel)
    )
    await asyncio.sleep(0.02)
    cancel.set()
    result = await asyncio.wait_for(task, 1)
    assert result.status == "cancelled"
    assert not outside.exists()


def test_prepare_classifies_commands_and_blocks_side_effects_in_plan(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    context = ToolContext(tmp_path)
    read_only = executor.prepare(
        ToolCall(0, "read", "run_command", '{"command":"pwd"}'), context, AgentMode.PLAN
    )
    blocked = executor.prepare(
        ToolCall(0, "write", "run_command", '{"command":"touch nope"}'),
        context,
        AgentMode.PLAN,
    )
    assert read_only.effect.value == "read_only"
    assert read_only.error is None
    assert blocked.effect.value == "side_effect"
    assert blocked.error is not None
    assert blocked.error.error.code == "plan_mode_denied"


async def test_permanent_allow_is_persisted_and_immediately_reused(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    context = ToolContext(tmp_path)
    call = ToolCall(
        0,
        "write",
        "write_file",
        '{"path":"allowed.txt","content":"ok"}',
    )
    result = await executor.execute(call, context, allow_always)
    assert result.status == "success"
    assert "Write(allowed.txt)" in (tmp_path / ".kcode" / "permissions.local.yaml").read_text(
        encoding="utf-8"
    )
    prepared = executor.prepare(call, context)
    assert prepared.approval is None
    assert prepared.error is None


async def test_permanent_allow_refuses_sensitive_command_rule(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    context = ToolContext(tmp_path, sensitive_values=("top-secret",))
    call = ToolCall(
        0,
        "bash",
        "run_command",
        '{"command":"echo top-secret"}',
    )
    result = await executor.execute(call, context, allow_always)
    assert result.error.code == "permission_persist_failed"
    assert "top-secret" not in result.to_json()
    assert not (tmp_path / ".kcode" / "permissions.local.yaml").exists()


async def test_blacklisted_command_never_requests_approval_or_executes(tmp_path: Path) -> None:
    executor = make_executor(tmp_path)
    called = False

    async def approval(_request):
        nonlocal called
        called = True
        return ApprovalChoice.ALLOW_ONCE

    result = await executor.execute(
        ToolCall(0, "danger", "run_command", '{"command":"rm -rf /"}'),
        ToolContext(tmp_path),
        approval,
    )
    assert result.error.code == "dangerous_command"
    assert called is False


async def test_shell_semantics_are_identical_for_rule_mode_and_human_allow(tmp_path: Path) -> None:
    command = "printf left | tr a-z A-Z"
    call = ToolCall(0, "shell", "run_command", json.dumps({"command": command}))

    human = await make_executor(tmp_path).execute(call, ToolContext(tmp_path), allow)
    bypass = await make_executor(tmp_path).execute(
        call,
        ToolContext(tmp_path),
        allow,
        PermissionMode.BYPASS_PERMISSIONS,
    )

    settings = PermissionSettings(
        (
            PermissionLayer(
                "local",
                tmp_path / "local",
                allow=(parse_rule(f"Bash({command})"),),
            ),
        ),
        PermissionMode.DEFAULT,
    )
    registry = create_default_registry()
    by_rule = await ToolExecutor(
        registry,
        PermissionEngine(settings),
        LocalPermissionStore(tmp_path / "local"),
    ).execute(call, ToolContext(tmp_path), deny)

    assert [result.data["stdout"] for result in (human, bypass, by_rule)] == [
        "LEFT",
        "LEFT",
        "LEFT",
    ]


async def test_invalid_local_file_makes_permanent_choice_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / ".kcode" / "permissions.local.yaml"
    path.parent.mkdir()
    path.write_text("allow: nope\n", encoding="utf-8")
    settings = empty_permission_settings(tmp_path)
    registry = create_default_registry()
    executor = ToolExecutor(
        registry,
        PermissionEngine(settings),
        LocalPermissionStore(path),
    )
    result = await executor.execute(
        ToolCall(0, "write", "write_file", '{"path":"blocked.txt","content":"x"}'),
        ToolContext(tmp_path),
        allow_always,
    )
    assert result.error.code == "permission_persist_failed"
    assert not (tmp_path / "blocked.txt").exists()
    assert path.read_text(encoding="utf-8") == "allow: nope\n"
