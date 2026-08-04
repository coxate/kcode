from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from pathlib import Path
from typing import cast

from kcode.tools.base import (
    EditFileArgs,
    ReadFileArgs,
    ToolArguments,
    ToolContext,
    ToolExecutionError,
    ToolResult,
    ToolSpec,
    WriteFileArgs,
)
from kcode.tools.policy import resolve_tool_path


def _ensure_regular(path: Path) -> os.stat_result:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ToolExecutionError("not_a_file", f"'{path}' is not a regular file.", path=str(path))
    return info


def _read(arguments: ReadFileArgs, context: ToolContext) -> ToolResult:
    path = resolve_tool_path(arguments.path, context, existing=True)
    _ensure_regular(path)
    selected: list[str] = []
    used = 0
    current = 0
    truncated = False
    next_line: int | None = None
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line in handle:
                current += 1
                if current < arguments.start_line:
                    continue
                encoded = line.encode("utf-8")
                if len(encoded) > context.limits.max_bytes:
                    raise ToolExecutionError(
                        "line_too_large",
                        "A single line exceeds the read limit.",
                        line=current,
                        limit_bytes=context.limits.max_bytes,
                    )
                if len(selected) >= arguments.max_lines or used + len(encoded) > context.limits.max_bytes:
                    truncated = True
                    next_line = current
                    break
                selected.append(line)
                used += len(encoded)
                if context.cancel_event and context.cancel_event.is_set():
                    raise ToolExecutionError("cancelled", "File reading was cancelled.")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError("decode_error", "File is not valid UTF-8 text.", path=str(path)) from exc
    end_line = arguments.start_line + len(selected) - 1 if selected else arguments.start_line - 1
    return ToolResult.success(
        {
            "path": str(path),
            "content": "".join(selected),
            "start_line": arguments.start_line,
            "end_line": end_line,
            "next_start_line": next_line,
        },
        truncated=truncated,
    )


def _temp_path(parent: Path) -> tuple[int, Path]:
    fd, raw = tempfile.mkstemp(prefix=".kcode-", dir=parent)
    return fd, Path(raw)


def _write(arguments: WriteFileArgs, context: ToolContext) -> ToolResult:
    target = resolve_tool_path(arguments.path, context, existing=False)
    if target.exists() or target.is_symlink():
        raise ToolExecutionError("already_exists", "write_file never overwrites an existing path.", path=str(target))
    fd, temporary = _temp_path(target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(arguments.content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            raise ToolExecutionError("already_exists", "The target was created concurrently.", path=str(target)) from exc
        return ToolResult.success({"path": str(target), "bytes_written": len(arguments.content.encode("utf-8"))})
    finally:
        temporary.unlink(missing_ok=True)


def _edit(arguments: EditFileArgs, context: ToolContext) -> ToolResult:
    target = resolve_tool_path(arguments.path, context, existing=True)
    before = _ensure_regular(target)
    try:
        source = target.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError("decode_error", "File is not valid UTF-8 text.", path=str(target)) from exc
    count = source.count(arguments.old_text)
    if count != 1:
        raise ToolExecutionError(
            "no_unique_match",
            "old_text must occur exactly once; adjust it and retry.",
            matches=count,
            path=str(target),
        )
    updated = source.replace(arguments.old_text, arguments.new_text, 1)
    fd, temporary = _temp_path(target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, stat.S_IMODE(before.st_mode))
        current = target.stat()
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_now = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns)
        if identity_before != identity_now:
            raise ToolExecutionError("file_changed", "The file changed while it was being edited.", path=str(target))
        os.replace(temporary, target)
        return ToolResult.success({"path": str(target), "replacements": 1})
    finally:
        temporary.unlink(missing_ok=True)


class ReadFileTool:
    spec = ToolSpec("read_file", "Read a UTF-8 text file, optionally by line range.", ReadFileArgs)

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return await asyncio.to_thread(_read, cast(ReadFileArgs, arguments), context)


class WriteFileTool:
    spec = ToolSpec("write_file", "Create a new UTF-8 text file. Never overwrites.", WriteFileArgs)

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return await asyncio.to_thread(_write, cast(WriteFileArgs, arguments), context)


class EditFileTool:
    spec = ToolSpec("edit_file", "Replace old_text only when it occurs exactly once.", EditFileArgs)

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return await asyncio.to_thread(_edit, cast(EditFileArgs, arguments), context)
