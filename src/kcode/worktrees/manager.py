from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

from kcode.worktrees.git import GitWorktreeClient
from kcode.worktrees.models import (
    GitWorktreeEntry,
    WorktreeError,
    WorktreeFinalizationReport,
    WorktreeKind,
    WorktreeRecord,
    WorktreeStatus,
    WorktreeStoreError,
    validate_slug,
)
from kcode.worktrees.store import WorktreeStore


class WorktreeManager:
    def __init__(self, cwd: Path, client: GitWorktreeClient | None = None) -> None:
        self.cwd = cwd.resolve(strict=False)
        self.client = client or GitWorktreeClient()
        self._lock = asyncio.Lock()
        self._initialized = False
        self._repo_root: Path | None = None
        self._worktree_root: Path | None = None
        self._store: WorktreeStore | None = None
        self._unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        return self._initialized and self._repo_root is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    @property
    def repo_root(self) -> Path | None:
        return self._repo_root

    @property
    def worktree_root(self) -> Path | None:
        return self._worktree_root

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            try:
                info = await self.client.discover(self.cwd)
            except WorktreeError as exc:
                self._unavailable_reason = str(exc)
                self._initialized = True
                return
            self._repo_root = info.root
            self._worktree_root = info.root.parent / ".kcode-worktrees" / info.root.name
            try:
                self._store = WorktreeStore(info.root, self._worktree_root)
            except WorktreeStoreError as exc:
                self._repo_root = None
                self._worktree_root = None
                self._unavailable_reason = str(exc)
            self._initialized = True

    async def _require(self) -> tuple[Path, Path, WorktreeStore]:
        await self.initialize()
        if self._repo_root is None or self._worktree_root is None or self._store is None:
            raise WorktreeError(
                "worktrees_unavailable",
                self._unavailable_reason or "当前项目不可使用 Git Worktree。",
            )
        return self._repo_root, self._worktree_root, self._store

    @staticmethod
    def _load(store: WorktreeStore) -> tuple[dict[str, WorktreeRecord], tuple[str, ...]]:
        try:
            return store.load(), ()
        except WorktreeStoreError as exc:
            return {}, (str(exc),)

    async def _create(
        self,
        name: str,
        kind: WorktreeKind,
        owner_id: str | None,
    ) -> tuple[WorktreeRecord, tuple[str, ...]]:
        name = validate_slug(name)
        repo, root, store = await self._require()
        warnings: list[str] = []
        dirty = await self.client.is_dirty(repo)
        if kind is WorktreeKind.AGENT and dirty:
            raise WorktreeError(
                "dirty_main_worktree",
                "主工作目录包含未提交修改；自动隔离已拒绝，请先提交或选择 shared。",
            )
        if kind is WorktreeKind.MANUAL and dirty:
            warnings.append("主工作目录有未提交修改；新 Worktree 只包含当前 HEAD。")
        base = await self.client.head(repo)
        path = (root / name).resolve(strict=False)
        if path.parent != root.resolve(strict=False):
            raise WorktreeError("unsafe_worktree_path", "Worktree 路径越过管理根目录。")
        branch = f"kcode-worktree/{name}"
        records = store.load()
        entries = await self.client.list(repo)
        if name in records:
            raise WorktreeError("worktree_conflict", "Worktree 名称已被 Kcode 使用。")
        if os.path.lexists(path):
            raise WorktreeError("worktree_conflict", "Worktree 目标目录已存在。")
        if await self.client.branch_exists(repo, branch):
            raise WorktreeError("worktree_conflict", "Worktree 分支已存在。")
        if any(item.path == path or item.branch == branch for item in entries):
            raise WorktreeError("worktree_conflict", "Git 已登记相同路径或分支的 Worktree。")
        record = WorktreeRecord(name, path, branch, base, kind, owner_id, time.time())
        root.mkdir(parents=True, exist_ok=True)
        added = False
        try:
            await self.client.add(repo, path, branch, base)
            added = True
            verified = await self.client.list(repo)
            if not any(
                item.path == path and item.branch == branch and item.head_commit == base
                for item in verified
            ):
                raise WorktreeError("worktree_verification_failed", "Git Worktree 创建后验证失败。")
            records[name] = record
            store.save(records)
            return record, tuple(warnings)
        except Exception as exc:
            rollback_warnings: list[str] = []
            if added:
                try:
                    await self.client.remove(repo, path)
                except WorktreeError:
                    rollback_warnings.append(f"创建失败后的目录未能安全清理，请检查：{path}")
                try:
                    await self.client.delete_branch(repo, branch)
                except WorktreeError:
                    rollback_warnings.append(f"创建失败后的分支仍保留，请检查：{branch}")
            if rollback_warnings:
                raise WorktreeError(
                    "worktree_create_failed_kept",
                    "Worktree 创建失败；" + "；".join(rollback_warnings),
                ) from exc
            if isinstance(exc, WorktreeError):
                raise
            raise WorktreeError("worktree_create_failed", "Worktree 创建失败。") from exc

    async def create_manual(self, name: str) -> tuple[WorktreeRecord, tuple[str, ...]]:
        await self.initialize()
        async with self._lock:
            return await self._create(name, WorktreeKind.MANUAL, None)

    async def create_agent(self, owner_id: str) -> WorktreeRecord:
        if not owner_id:
            raise WorktreeError("invalid_worktree_owner", "自动 Worktree 缺少任务所有者。")
        await self.initialize()
        async with self._lock:
            for _ in range(8):
                name = f"agent-{uuid.uuid4().hex[:12]}"
                try:
                    record, _ = await self._create(name, WorktreeKind.AGENT, owner_id)
                    return record
                except WorktreeError as exc:
                    if exc.code != "worktree_conflict":
                        raise
            raise WorktreeError("worktree_conflict", "无法生成唯一的 Agent Worktree 名称。")

    async def _status_for(
        self,
        entry: GitWorktreeEntry | None,
        record: WorktreeRecord | None,
        warnings: tuple[str, ...] = (),
    ) -> WorktreeStatus:
        path = entry.path if entry is not None else record.path  # type: ignore[union-attr]
        branch = entry.branch if entry is not None else record.branch  # type: ignore[union-attr]
        managed = (
            entry is not None
            and record is not None
            and entry.path == record.path
            and entry.branch == record.branch
        )
        if entry is None:
            return WorktreeStatus(
                path,
                branch,
                None,
                None,
                None,
                False,
                False,
                record,
                (*warnings, "元数据存在，但 Git 中没有对应 Worktree。"),
            )
        try:
            state = await self.client.status(path)
            changed = state.head_commit != record.base_commit if managed else None
            removable = managed and not state.dirty and changed is False
            return WorktreeStatus(
                path,
                branch,
                state.head_commit,
                state.dirty,
                changed,
                managed,
                removable,
                record if managed else None,
                warnings if managed else (*warnings, "Worktree 缺少可信 Kcode 所有权记录。"),
            )
        except WorktreeError:
            return WorktreeStatus(
                path,
                branch,
                entry.head_commit,
                None,
                None,
                managed,
                False,
                record if managed else None,
                (*warnings, "无法确认 Worktree 状态，已按必须保留处理。"),
            )

    async def list(self) -> tuple[WorktreeStatus, ...]:
        await self.initialize()
        async with self._lock:
            repo, root, store = await self._require()
            records, warnings = self._load(store)
            entries = await self.client.list(repo)
            root = root.resolve(strict=False)
            managed_entries = [item for item in entries if item.path.parent == root]
            by_path = {item.path: item for item in managed_entries}
            result = [
                await self._status_for(entry, records.get(entry.path.name), warnings)
                for entry in managed_entries
            ]
            for record in records.values():
                if record.path not in by_path:
                    result.append(await self._status_for(None, record, warnings))
            return tuple(sorted(result, key=lambda item: item.path.name))

    async def status(self, name: str) -> WorktreeStatus:
        name = validate_slug(name)
        await self.initialize()
        async with self._lock:
            repo, root, store = await self._require()
            records, warnings = self._load(store)
            expected = (root / name).resolve(strict=False)
            entry = next(
                (item for item in await self.client.list(repo) if item.path == expected),
                None,
            )
            record = records.get(name)
            if entry is None and record is None:
                raise WorktreeError("unknown_worktree", f"没有名为 {name} 的 Kcode Worktree。")
            return await self._status_for(entry, record, warnings)

    @staticmethod
    def _report(
        status: WorktreeStatus,
        record: WorktreeRecord,
        kept: bool,
        reason: str,
        warnings=(),
    ):
        return WorktreeFinalizationReport(
            record.name,
            record.path,
            record.branch,
            record.base_commit,
            status.head_commit,
            status.dirty,
            status.head_changed,
            kept,
            reason,
            (*status.warnings, *warnings),
        )

    async def _remove_record(
        self,
        repo: Path,
        store: WorktreeStore,
        records: dict[str, WorktreeRecord],
        record: WorktreeRecord,
        *,
        delete_branch: bool,
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        await self.client.remove(repo, record.path)
        records.pop(record.name, None)
        try:
            store.save(records)
        except WorktreeStoreError:
            warnings.append("目录已删除，但元数据未能更新；下次将显示 missing。")
        if delete_branch:
            try:
                await self.client.delete_branch(repo, record.branch)
            except WorktreeError:
                warnings.append(f"临时分支未能安全删除，仍保留：{record.branch}")
        return tuple(warnings)

    async def remove_manual(self, name: str) -> WorktreeFinalizationReport:
        name = validate_slug(name)
        await self.initialize()
        async with self._lock:
            repo, root, store = await self._require()
            records = store.load()
            record = records.get(name)
            if record is None or record.kind is not WorktreeKind.MANUAL:
                raise WorktreeError(
                    "worktree_not_removable",
                    "只允许删除 Kcode 托管的手动 Worktree。",
                )
            entry = next(
                (item for item in await self.client.list(repo) if item.path == record.path),
                None,
            )
            status = await self._status_for(entry, record)
            if not status.removable:
                return self._report(status, record, True, "状态无法证明无成果，已拒绝删除。")
            try:
                warnings = await self._remove_record(
                    repo,
                    store,
                    records,
                    record,
                    delete_branch=False,
                )
            except WorktreeError:
                return self._report(status, record, True, "Git 普通删除失败，已保留 Worktree。")
            return self._report(status, record, False, "Worktree 已安全删除。", warnings)

    async def finalize(self, record: WorktreeRecord, owner_id: str) -> WorktreeFinalizationReport:
        unknown = WorktreeStatus(record.path, record.branch, None, None, None, False, False)
        try:
            await self.initialize()
            async with self._lock:
                repo, _root, store = await self._require()
                records = store.load()
                trusted = records.get(record.name)
                if (
                    trusted != record
                    or record.kind is not WorktreeKind.AGENT
                    or record.owner_id != owner_id
                ):
                    return self._report(
                        unknown,
                        record,
                        True,
                        "所有权无法确认，已保留 Worktree。",
                    )
                entry = next(
                    (item for item in await self.client.list(repo) if item.path == record.path),
                    None,
                )
                status = await self._status_for(entry, record)
                if not status.removable:
                    return self._report(
                        status,
                        record,
                        True,
                        "检测到成果或状态未知，已保留供 review。",
                    )
                try:
                    warnings = await self._remove_record(
                        repo,
                        store,
                        records,
                        record,
                        delete_branch=True,
                    )
                except WorktreeError:
                    return self._report(
                        status,
                        record,
                        True,
                        "Git 普通删除失败，已保留供 review。",
                    )
                return self._report(
                    status,
                    record,
                    False,
                    "没有检测到成果，已安全清理。",
                    warnings,
                )
        except WorktreeError as exc:
            return self._report(
                unknown,
                record,
                True,
                "结束检查失败，已保留 Worktree 供 review。",
                (str(exc),),
            )
