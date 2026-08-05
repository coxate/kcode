from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SandboxedPath:
    absolute: Path
    relative: str


class SandboxViolation(Exception):
    pass


def resolve_sandboxed_path(raw: str, workspace_root: Path) -> SandboxedPath:
    try:
        root = workspace_root.resolve(strict=True)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise SandboxViolation("The path cannot be proven to be inside the project root.") from exc
    return SandboxedPath(resolved, relative.as_posix() or ".")
