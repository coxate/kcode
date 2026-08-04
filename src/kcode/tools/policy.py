from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from kcode.tools.base import ApprovalRequest, ToolArguments, ToolCall, ToolContext


def resolve_tool_path(raw: str, context: ToolContext, *, existing: bool) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = context.workspace_root / candidate
    if existing:
        return candidate.resolve(strict=True)
    parent = candidate.parent.resolve(strict=True)
    return parent / candidate.name


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    approval: ApprovalRequest | None


class ToolPolicy:
    _simple_readers = {"pwd", "ls", "head", "tail", "wc", "stat", "file", "which", "dir", "type", "where"}
    _shell_markers = ("|", ";", ">", "<", "&&", "||", "`", "$(", "\n", "\r")

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def decision(
        self, call: ToolCall, arguments: ToolArguments, context: ToolContext
    ) -> PolicyDecision:
        if call.name in {"write_file", "edit_file"}:
            target = resolve_tool_path(str(getattr(arguments, "path")), context, existing=call.name == "edit_file")
            if not is_within(target, self.workspace_root):
                return PolicyDecision(
                    ApprovalRequest(
                        call.id,
                        call.name,
                        str(target),
                        "目标位于 KCode 启动工作区之外。",
                    )
                )
        if call.name == "run_command":
            command = str(getattr(arguments, "command"))
            if not self.is_read_only_command(command):
                return PolicyDecision(
                    ApprovalRequest(call.id, call.name, command, "该命令不属于严格只读白名单。")
                )
        return PolicyDecision(None)

    def is_read_only_command(self, command: str) -> bool:
        if any(marker in command for marker in self._shell_markers):
            return False
        try:
            parts = shlex.split(command, posix=os.name != "nt")
        except ValueError:
            return False
        if not parts:
            return False
        executable = Path(parts[0]).name.lower()
        if executable in self._simple_readers:
            return True
        if executable in {"rg", "rg.exe"}:
            forbidden = {"--pre", "--hostname-bin"}
            return not any(arg.split("=", 1)[0] in forbidden for arg in parts[1:])
        if executable == "git" and len(parts) >= 2:
            return parts[1] in {"status", "diff", "log", "show"}
        return False
