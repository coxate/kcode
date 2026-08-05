from __future__ import annotations

import asyncio
import fnmatch
import os
import re
from pathlib import Path, PurePosixPath
from typing import cast

from kcode.tools.base import (
    FindFilesArgs,
    SearchCodeArgs,
    ToolArguments,
    ToolContext,
    ToolExecutionError,
    ToolResult,
    ToolSpec,
)
from kcode.tools.paths import resolve_tool_path


def _walk(root: Path, context: ToolContext):
    pending = [root]
    while pending:
        directory = pending.pop()
        if context.cancel_event and context.cancel_event.is_set():
            raise ToolExecutionError("cancelled", "Search was cancelled.")
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            yield None, f"Cannot read {directory}: {exc.__class__.__name__}"
            continue
        directories: list[Path] = []
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                else:
                    yield path, None
            except OSError as exc:
                yield None, f"Cannot inspect {path}: {exc.__class__.__name__}"
        pending.extend(reversed(directories))


def _matches_glob(relative: str, pattern: str) -> bool:
    pure = PurePosixPath(relative)
    return pure.match(pattern) or fnmatch.fnmatchcase(relative, pattern)


def _find(arguments: FindFilesArgs, context: ToolContext) -> ToolResult:
    root = resolve_tool_path(arguments.root, context, existing=True)
    if not root.is_dir():
        raise ToolExecutionError(
            "not_a_directory", "Search root is not a directory.", path=str(root)
        )
    results: list[str] = []
    warnings: list[str] = []
    used = 0
    truncated = False
    for path, warning in _walk(root, context):
        if warning:
            warnings.append(warning)
            continue
        assert path is not None
        relative = path.relative_to(root).as_posix()
        if _matches_glob(relative, arguments.pattern):
            size = len(relative.encode("utf-8"))
            if len(results) >= context.limits.max_items or used + size > context.limits.max_bytes:
                truncated = True
                break
            results.append(str(path))
            used += size
    return ToolResult.success(
        {"root": str(root), "matches": results}, truncated=truncated, warnings=tuple(warnings)
    )


def _search(arguments: SearchCodeArgs, context: ToolContext) -> ToolResult:
    root = resolve_tool_path(arguments.root, context, existing=True)
    if not root.is_dir():
        raise ToolExecutionError(
            "not_a_directory", "Search root is not a directory.", path=str(root)
        )
    flags = 0 if arguments.case_sensitive else re.IGNORECASE
    try:
        expression = re.compile(arguments.pattern, flags)
    except re.error as exc:
        raise ToolExecutionError("invalid_arguments", f"Invalid regular expression: {exc}") from exc
    matches: list[dict[str, object]] = []
    warnings: list[str] = []
    used = 0
    truncated = False
    for path, warning in _walk(root, context):
        if warning:
            warnings.append(warning)
            continue
        assert path is not None
        relative = path.relative_to(root).as_posix()
        if arguments.file_pattern and not _matches_glob(relative, arguments.file_pattern):
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, 1):
                    if expression.search(line):
                        text = line.rstrip("\r\n")
                        item = {"path": str(path), "line": line_number, "text": text}
                        size = len(str(item).encode("utf-8"))
                        if (
                            len(matches) >= context.limits.max_items
                            or used + size > context.limits.max_bytes
                        ):
                            truncated = True
                            break
                        matches.append(item)
                        used += size
                if truncated:
                    break
        except (UnicodeDecodeError, OSError) as exc:
            warnings.append(f"Skipped {path}: {exc.__class__.__name__}")
    return ToolResult.success(
        {"root": str(root), "matches": matches}, truncated=truncated, warnings=tuple(warnings)
    )


class FindFilesTool:
    spec = ToolSpec(
        "find_files",
        "Find files below a directory using a glob pattern. Prefer this purpose-built "
        "tool over run_command for file discovery.",
        FindFilesArgs,
    )

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return await asyncio.to_thread(_find, cast(FindFilesArgs, arguments), context)


class SearchCodeTool:
    spec = ToolSpec(
        "search_code",
        "Search UTF-8 files line by line using a regular expression. Prefer this "
        "purpose-built tool over shell search commands.",
        SearchCodeArgs,
    )

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return await asyncio.to_thread(_search, cast(SearchCodeArgs, arguments), context)
