from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from kcode.permissions.models import FriendlyToolName, ToolCategory
from kcode.tools.base import ToolArguments


@dataclass(frozen=True, slots=True)
class ToolPermissionInfo:
    friendly_name: FriendlyToolName
    category: ToolCategory
    raw_value: str


TOOL_INFO: dict[str, tuple[FriendlyToolName, ToolCategory, str]] = {
    "read_file": ("Read", ToolCategory.READ, "path"),
    "write_file": ("Write", ToolCategory.WRITE, "path"),
    "edit_file": ("Edit", ToolCategory.WRITE, "path"),
    "find_files": ("Glob", ToolCategory.READ, "root"),
    "search_code": ("Grep", ToolCategory.READ, "root"),
    "run_command": ("Bash", ToolCategory.COMMAND, "command"),
}

SIMPLE_READERS = {
    "pwd",
    "ls",
    "head",
    "tail",
    "wc",
    "stat",
    "file",
    "which",
    "dir",
    "type",
    "where",
}
SHELL_MARKERS = ("|", ";", ">", "<", "&&", "||", "`", "$(", "\n", "\r")


def tool_permission_info(tool_name: str, arguments: ToolArguments) -> ToolPermissionInfo:
    friendly_name, category, field = TOOL_INFO[tool_name]
    raw_value = str(getattr(arguments, field))
    value = raw_value.strip() if field == "command" else raw_value
    return ToolPermissionInfo(friendly_name, category, value)


def is_read_only_command(command: str) -> bool:
    if any(marker in command for marker in SHELL_MARKERS):
        return False
    try:
        parts = shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return False
    if not parts:
        return False
    executable = Path(parts[0]).name.lower()
    if executable in SIMPLE_READERS:
        return True
    if executable in {"rg", "rg.exe"}:
        forbidden = {"--pre", "--hostname-bin"}
        return not any(argument.split("=", 1)[0] in forbidden for argument in parts[1:])
    if executable == "git" and len(parts) >= 2:
        return parts[1] in {"status", "diff", "log", "show"}
    return False


def redact_preview(value: str, secrets: tuple[str, ...], limit: int = 500) -> str:
    preview = value
    for secret in secrets:
        if secret:
            preview = preview.replace(secret, "[REDACTED]")
    if len(preview) > limit:
        return preview[: limit - 1] + "…"
    return preview
