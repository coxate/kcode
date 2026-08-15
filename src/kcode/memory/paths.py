from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kcode.memory.models import MemoryScope


class MemoryPathError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MemoryPaths:
    root: Path
    entries: Path
    proposals: Path
    index: Path
    state: Path
    lock: Path


def memory_root(scope: MemoryScope, workspace: Path, home: Path | None = None) -> Path:
    if scope == MemoryScope.USER:
        base = (home or Path.home()).resolve()
    else:
        base = workspace.resolve()
    root = base / ".kcode" / "memory"
    if root.is_symlink():
        raise MemoryPathError(f"Memory root cannot be a symbolic link: {root}")
    resolved = root.resolve(strict=False)
    if base != resolved and base not in resolved.parents:
        raise MemoryPathError(f"Memory root escapes its allowed boundary: {root}")
    return root


def build_paths(scope: MemoryScope, workspace: Path, home: Path | None = None) -> MemoryPaths:
    root = memory_root(scope, workspace, home)
    return MemoryPaths(
        root=root,
        entries=root / "entries",
        proposals=root / "proposals",
        index=root / "MEMORY.md",
        state=root / "state.json",
        lock=root / ".memory.lock",
    )


def validate_child(root: Path, path: Path, *, allow_missing: bool = True) -> None:
    if path.is_symlink():
        raise MemoryPathError(f"Memory path cannot be a symbolic link: {path}")
    try:
        resolved_root = root.resolve(strict=not allow_missing)
    except FileNotFoundError:
        resolved_root = root.resolve()
    resolved = path.resolve(strict=False)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise MemoryPathError(f"Memory path escapes its root: {path}")
