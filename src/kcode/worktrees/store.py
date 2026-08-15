from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from kcode.worktrees.models import (
    WorktreeKind,
    WorktreeRecord,
    WorktreeStoreError,
    validate_sha,
    validate_slug,
)

STORE_VERSION = 1


class WorktreeStore:
    def __init__(self, repo_root: Path, worktree_root: Path) -> None:
        self.repo_root = repo_root.resolve(strict=True)
        self.worktree_root = worktree_root.absolute()
        self.path = self.worktree_root / ".metadata.json"
        self._assert_root()

    def _assert_root(self) -> None:
        if self.worktree_root.resolve(strict=False) != self.worktree_root:
            raise WorktreeStoreError(
                "unsafe_worktree_root",
                "Worktree 管理根目录包含符号链接，已停止自动管理。",
            )

    def _safe_path(self, raw: str | Path) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            raise WorktreeStoreError(
                "invalid_worktree_metadata",
                "Worktree 元数据路径不是绝对路径。",
            )
        resolved = path.resolve(strict=False)
        if resolved.parent != self.worktree_root or resolved == self.worktree_root:
            raise WorktreeStoreError(
                "invalid_worktree_metadata",
                "Worktree 元数据路径越过管理根目录。",
            )
        return resolved

    def load(self) -> dict[str, WorktreeRecord]:
        self._assert_root()
        if not os.path.lexists(self.path):
            return {}
        if self.path.is_symlink():
            raise WorktreeStoreError(
                "invalid_worktree_metadata",
                "Worktree 元数据文件不能是符号链接。",
            )
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise WorktreeStoreError(
                "invalid_worktree_metadata",
                "Worktree 元数据损坏，已停止自动管理。",
            ) from exc
        if not isinstance(raw, dict) or set(raw) != {"version", "repo_root", "records"}:
            raise WorktreeStoreError("invalid_worktree_metadata", "Worktree 元数据结构无效。")
        if raw["version"] != STORE_VERSION:
            raise WorktreeStoreError(
                "unsupported_worktree_metadata",
                "Worktree 元数据版本不受支持。",
            )
        try:
            stored_repo = Path(raw["repo_root"]).resolve(strict=False)
        except (TypeError, OSError) as exc:
            raise WorktreeStoreError(
                "invalid_worktree_metadata",
                "Worktree 仓库绑定无效。",
            ) from exc
        if stored_repo != self.repo_root:
            raise WorktreeStoreError("worktree_repo_mismatch", "Worktree 元数据属于另一个仓库。")
        if not isinstance(raw["records"], list):
            raise WorktreeStoreError("invalid_worktree_metadata", "Worktree 记录列表无效。")
        result: dict[str, WorktreeRecord] = {}
        required = {"name", "path", "branch", "base_commit", "kind", "owner_id", "created_at"}
        try:
            for item in raw["records"]:
                if not isinstance(item, dict) or set(item) != required:
                    raise ValueError
                name = validate_slug(item["name"])
                path = self._safe_path(item["path"])
                branch = item["branch"]
                if branch != f"kcode-worktree/{name}":
                    raise ValueError
                base = validate_sha(item["base_commit"])
                kind = WorktreeKind(item["kind"])
                owner = item["owner_id"]
                created = item["created_at"]
                invalid_owner = owner is not None and (not isinstance(owner, str) or not owner)
                if invalid_owner or not isinstance(created, (int, float)):
                    raise ValueError
                if kind is WorktreeKind.AGENT and owner is None:
                    raise ValueError
                if kind is WorktreeKind.MANUAL and owner is not None:
                    raise ValueError
                if name in result:
                    raise ValueError
                result[name] = WorktreeRecord(name, path, branch, base, kind, owner, float(created))
        except (KeyError, TypeError, ValueError, WorktreeStoreError) as exc:
            if isinstance(exc, WorktreeStoreError):
                raise
            raise WorktreeStoreError(
                "invalid_worktree_metadata",
                "Worktree 记录内容无效。",
            ) from exc
        return result

    def save(self, records: dict[str, WorktreeRecord]) -> None:
        self._assert_root()
        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._assert_root()
        if self.path.is_symlink():
            raise WorktreeStoreError(
                "invalid_worktree_metadata",
                "Worktree 元数据文件不能是符号链接。",
            )
        payload = {
            "version": STORE_VERSION,
            "repo_root": str(self.repo_root),
            "records": [
                {
                    "name": record.name,
                    "path": str(record.path),
                    "branch": record.branch,
                    "base_commit": record.base_commit,
                    "kind": record.kind.value,
                    "owner_id": record.owner_id,
                    "created_at": record.created_at,
                }
                for record in sorted(records.values(), key=lambda value: value.name)
            ],
        }
        temporary: Path | None = None
        try:
            descriptor, raw_path = tempfile.mkstemp(prefix=".metadata-", dir=self.worktree_root)
            temporary = Path(raw_path)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.worktree_root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            raise WorktreeStoreError(
                "worktree_metadata_write_failed",
                "无法安全保存 Worktree 元数据。",
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass
