from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SHA = re.compile(r"^[0-9a-f]{40,64}$")


class WorktreeError(RuntimeError):
    """A safe, user-facing Worktree failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class GitWorktreeError(WorktreeError):
    pass


class WorktreeStoreError(WorktreeError):
    pass


class WorktreeKind(StrEnum):
    MANUAL = "manual"
    AGENT = "agent"


def validate_slug(value: str) -> str:
    if not _SLUG.fullmatch(value) or value in {".", ".."}:
        raise WorktreeError(
            "invalid_worktree_name",
            "Worktree 名称必须是最长 64 字符的单段小写 slug，只能包含字母、数字和连字符。",
        )
    return value


def validate_sha(value: str) -> str:
    if not _SHA.fullmatch(value):
        raise WorktreeError("invalid_git_output", "Git 返回了无效 commit 标识。")
    return value


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    root: Path


@dataclass(frozen=True, slots=True)
class GitWorktreeEntry:
    path: Path
    head_commit: str | None
    branch: str | None
    detached: bool = False
    bare: bool = False
    prunable: bool = False


@dataclass(frozen=True, slots=True)
class GitWorktreeState:
    head_commit: str
    dirty: bool


@dataclass(frozen=True, slots=True)
class WorktreeRecord:
    name: str
    path: Path
    branch: str
    base_commit: str
    kind: WorktreeKind
    owner_id: str | None
    created_at: float


@dataclass(frozen=True, slots=True)
class WorktreeStatus:
    path: Path
    branch: str | None
    head_commit: str | None
    dirty: bool | None
    head_changed: bool | None
    managed: bool
    removable: bool
    record: WorktreeRecord | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorktreeFinalizationReport:
    name: str
    path: Path
    branch: str
    base_commit: str
    head_commit: str | None
    dirty: bool | None
    head_changed: bool | None
    kept: bool
    reason: str
    warnings: tuple[str, ...] = ()

    def render(self) -> str:
        def known(value: object | None) -> str:
            if value is None:
                return "unknown"
            if isinstance(value, bool):
                return str(value).lower()
            return str(value)

        lines = (
            "<worktree-result>",
            f"Name: {self.name}",
            f"Path: {self.path}",
            f"Branch: {self.branch}",
            f"Base: {self.base_commit}",
            f"HEAD: {known(self.head_commit)}",
            f"Dirty: {known(self.dirty)}",
            f"HEAD changed: {known(self.head_changed)}",
            f"Kept: {known(self.kept)}",
            f"Reason: {self.reason}",
            *(f"Warning: {item}" for item in self.warnings),
            "</worktree-result>",
        )
        return "\n".join(lines)
