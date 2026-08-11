from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

from kcode.hooks.executor import HookActionExecutor
from kcode.hooks.models import HookContext, HookEvent, HookSource
from kcode.hooks.parser import parse_hook
from kcode.permissions.models import PermissionMode


def make_hook(value):
    hook, warning = parse_hook(value, HookSource.USER, Path("hooks.yaml"), 0)
    assert warning is None and hook is not None
    return hook


def context(tmp_path: Path, event: HookEvent = HookEvent.FILE_CHANGE) -> HookContext:
    return HookContext(
        event,
        "session",
        tmp_path,
        PermissionMode.DEFAULT,
        tool_name="write_file",
        tool_args={"path": "source file.py"},
        file_path="source file.py",
    )


async def test_command_runs_in_workspace_and_shell_quotes_variables(tmp_path: Path) -> None:
    hook = make_hook(
        {
            "id": "command",
            "event": "file_change",
            "action": {
                "type": "command",
                "command": f'{sys.executable} -c "import pathlib,sys; '
                "pathlib.Path('result.txt').write_text(sys.argv[1])\" $FILE_PATH",
            },
        }
    )
    result = await HookActionExecutor().execute(hook, context(tmp_path))
    assert result.warning is None
    assert (tmp_path / "result.txt").read_text() == "source file.py"


async def test_command_timeout_is_warning_and_process_is_stopped(tmp_path: Path) -> None:
    hook = make_hook(
        {
            "id": "slow",
            "event": "shutdown",
            "action": {
                "type": "command",
                "command": f'{sys.executable} -c "import time; time.sleep(2)"',
                "timeout": 0.1,
            },
        }
    )
    result = await HookActionExecutor().execute(hook, context(tmp_path, HookEvent.SHUTDOWN))
    assert result.warning is not None
    assert result.warning.code == "command_timeout"


async def test_command_cancellation_propagates_and_stops_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist.txt"
    hook = make_hook(
        {
            "id": "cancelled",
            "event": "shutdown",
            "action": {
                "type": "command",
                "command": (
                    f'{sys.executable} -c "import pathlib,time; time.sleep(1); '
                    f"pathlib.Path({str(marker)!r}).write_text('late')\""
                ),
                "timeout": 5,
            },
        }
    )
    task = asyncio.create_task(
        HookActionExecutor().execute(hook, context(tmp_path, HookEvent.SHUTDOWN))
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.1)
    assert not marker.exists()


async def test_http_sends_rendered_body_to_local_server(tmp_path: Path) -> None:
    received: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    hook = make_hook(
        {
            "id": "notify",
            "event": "file_change",
            "action": {
                "type": "http",
                "url": "https://hooks.example.test/notify",
                "headers": {"x-event": "$EVENT"},
                "body": '{"path":"$FILE_PATH"}',
            },
        }
    )
    executor = HookActionExecutor(http_transport=httpx.MockTransport(handle))
    try:
        result = await executor.execute(hook, context(tmp_path))
    finally:
        await executor.close()
    assert result.warning is None
    assert len(received) == 1
    assert received[0].headers["x-event"] == "file_change"
    assert received[0].content == b'{"path":"source file.py"}'


async def test_http_failure_and_large_response_are_safe_warnings(tmp_path: Path) -> None:
    hook = make_hook(
        {
            "id": "notify",
            "event": "file_change",
            "action": {"type": "http", "url": "https://hooks.example.test/notify"},
        }
    )

    async def too_large(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (33 * 1024))

    executor = HookActionExecutor(http_transport=httpx.MockTransport(too_large))
    result = await executor.execute(hook, context(tmp_path))
    await executor.close()
    assert result.warning is not None
    assert result.warning.code == "http_output_limit"

    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret upstream detail", request=request)

    executor = HookActionExecutor(http_transport=httpx.MockTransport(timeout))
    result = await executor.execute(hook, context(tmp_path))
    await executor.close()
    assert result.warning is not None
    assert result.warning.code == "http_failed"
    assert "secret upstream detail" not in result.warning.render()
