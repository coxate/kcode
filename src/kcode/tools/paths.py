from __future__ import annotations

from pathlib import Path

from kcode.tools.base import ToolContext


def resolve_tool_path(raw: str, context: ToolContext, *, existing: bool) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = context.workspace_root / candidate
    if existing:
        return candidate.resolve(strict=True)
    parent = candidate.parent.resolve(strict=True)
    return parent / candidate.name
