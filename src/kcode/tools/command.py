from __future__ import annotations

import asyncio
import os
import shlex
import signal
from pathlib import Path
from typing import cast

from kcode.tools.base import RunCommandArgs, ToolArguments, ToolContext, ToolExecutionError, ToolResult, ToolSpec
from kcode.tools.policy import resolve_tool_path


def _truncate(value: bytes, limit: int) -> tuple[str, bool, int]:
    omitted = max(0, len(value) - limit)
    if not omitted:
        return value.decode("utf-8", errors="replace"), False, 0
    half = limit // 2
    combined = value[:half] + b"\n... output truncated ...\n" + value[-half:]
    return combined.decode("utf-8", errors="replace"), True, omitted


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        await asyncio.wait_for(process.wait(), 0.5)
    except (ProcessLookupError, asyncio.TimeoutError):
        if process.returncode is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            await process.wait()


class RunCommandTool:
    spec = ToolSpec(
        "run_command",
        "Run a command and return its exit code, stdout, and stderr.",
        RunCommandArgs,
        None,
    )

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        args = cast(RunCommandArgs, arguments)
        cwd = resolve_tool_path(args.cwd or ".", context, existing=True)
        if not cwd.is_dir():
            raise ToolExecutionError("not_a_directory", "Command cwd is not a directory.", path=str(cwd))
        creationflags = 0
        kwargs: dict[str, object] = {}
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            creationflags = getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0)
            kwargs["creationflags"] = creationflags
        process_options = {
            "cwd": cwd,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            **kwargs,
        }
        if context.use_shell:
            process = await asyncio.create_subprocess_shell(args.command, **process_options)
        else:
            argv = shlex.split(args.command, posix=os.name != "nt")
            process = await asyncio.create_subprocess_exec(*argv, **process_options)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=args.timeout_seconds)
        except asyncio.TimeoutError:
            await _terminate(process)
            raise ToolExecutionError("timeout", "Command exceeded its timeout.", timeout_seconds=args.timeout_seconds)
        except asyncio.CancelledError:
            await _terminate(process)
            raise
        out, out_cut, out_omitted = _truncate(stdout, context.limits.max_bytes)
        err, err_cut, err_omitted = _truncate(stderr, context.limits.max_bytes)
        return ToolResult.success(
            {
                "cwd": str(cwd),
                "command": args.command,
                "exit_code": process.returncode,
                "stdout": out,
                "stderr": err,
                "stdout_omitted_bytes": out_omitted,
                "stderr_omitted_bytes": err_omitted,
            },
            truncated=out_cut or err_cut,
        )
