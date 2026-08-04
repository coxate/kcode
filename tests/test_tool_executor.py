from pathlib import Path
import json
import sys

from kcode.tools.base import ToolCall, ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.policy import ToolPolicy
from kcode.tools.registry import create_default_registry


async def allow(_request):
    return True


async def deny(_request):
    return False


async def test_executor_validates_unknown_and_bad_arguments(tmp_path: Path) -> None:
    executor = ToolExecutor(create_default_registry(), ToolPolicy(tmp_path))
    context = ToolContext(tmp_path)
    unknown = await executor.execute(ToolCall(0, "1", "nope", "{}"), context, allow)
    invalid = await executor.execute(ToolCall(0, "2", "read_file", "{"), context, allow)
    assert unknown.error.code == "unknown_tool"
    assert invalid.error.code == "invalid_arguments"


async def test_external_write_needs_approval_and_secrets_are_redacted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    executor = ToolExecutor(create_default_registry(), ToolPolicy(workspace))
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
    assert accepted.status == "success"
    assert "super-secret" not in accepted.to_json()


async def test_symlink_escape_is_treated_as_external(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / "link").symlink_to(outside, target_is_directory=True)
    executor = ToolExecutor(create_default_registry(), ToolPolicy(workspace))
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
    executor = ToolExecutor(create_default_registry(), ToolPolicy(tmp_path))
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
