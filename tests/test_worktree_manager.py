from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from kcode.worktrees import WorktreeError, WorktreeManager


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Kcode Test")
    git(path, "config", "user.email", "test@example.com")
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")


async def test_manual_create_status_remove(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorktreeManager(repo)
    record, warnings = await manager.create_manual("demo")
    assert warnings == ()
    assert record.path == tmp_path / ".kcode-worktrees" / "repo" / "demo"
    status = await manager.status("demo")
    assert status.removable
    report = await manager.remove_manual("demo")
    assert not report.kept
    assert not record.path.exists()
    assert git(repo, "branch", "--list", record.branch) == record.branch
    assert git(repo, "status", "--porcelain") == ""


async def test_manual_remove_keeps_dirty_and_committed_results(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorktreeManager(repo)
    dirty, _ = await manager.create_manual("dirty")
    (dirty.path / "result.txt").write_text("valuable", encoding="utf-8")
    dirty_report = await manager.remove_manual("dirty")
    assert dirty_report.kept and dirty.path.exists()

    committed, _ = await manager.create_manual("committed")
    (committed.path / "tracked.txt").write_text("committed\n", encoding="utf-8")
    git(committed.path, "add", "tracked.txt")
    git(committed.path, "commit", "-qm", "result")
    committed_report = await manager.remove_manual("committed")
    assert committed_report.kept and committed.path.exists()
    assert committed_report.head_changed


async def test_manual_dirty_warns_but_agent_refuses(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    (repo / "untracked.txt").write_text("local", encoding="utf-8")
    manager = WorktreeManager(repo)
    record, warnings = await manager.create_manual("manual")
    assert warnings and not (record.path / "untracked.txt").exists()
    with pytest.raises(WorktreeError, match="未提交"):
        await manager.create_agent("task-1")


async def test成果_are_kept_and_owner_is_checked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorktreeManager(repo)
    record = await manager.create_agent("task-1")
    (record.path / "result.txt").write_text("valuable", encoding="utf-8")
    wrong = await manager.finalize(record, "task-2")
    assert wrong.kept and record.path.exists()
    report = await manager.finalize(record, "task-1")
    assert report.kept and report.dirty
    assert record.path.exists()


async def test_clean_agent_is_removed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorktreeManager(repo)
    record = await manager.create_agent("task-1")
    report = await manager.finalize(record, "task-1")
    assert not report.kept
    assert not record.path.exists()
    assert git(repo, "branch", "--list", record.branch) == ""


async def test_finalize_metadata_failure_reports_path_and_keeps(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorktreeManager(repo)
    record = await manager.create_agent("task-1")
    assert manager.worktree_root is not None
    metadata = manager.worktree_root / ".metadata.json"
    metadata.write_text("not json", encoding="utf-8")
    report = await manager.finalize(record, "task-1")
    assert report.kept
    assert report.path == record.path
    assert "检查失败" in report.reason
    assert record.path.exists()


async def test_concurrent_agents_get_unique_worktrees(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorktreeManager(repo)
    first, second = await asyncio.gather(
        manager.create_agent("task-1"),
        manager.create_agent("task-2"),
    )
    assert first.name != second.name
    assert first.path != second.path
    assert first.branch != second.branch
    (first.path / "tracked.txt").write_text("first\n", encoding="utf-8")
    (second.path / "tracked.txt").write_text("second\n", encoding="utf-8")
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "base\n"
    statuses = await manager.list()
    assert {item.path for item in statuses} == {first.path, second.path}
    assert all(item.dirty for item in statuses)


async def test_non_git_is_unavailable_without_init_failure(tmp_path: Path) -> None:
    manager = WorktreeManager(tmp_path)
    await manager.initialize()
    assert not manager.available
    with pytest.raises(WorktreeError):
        await manager.list()


async def test_symlink_management_root_is_unavailable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / ".kcode-worktrees"
    root.mkdir()
    (root / "repo").symlink_to(outside, target_is_directory=True)
    manager = WorktreeManager(repo)
    await manager.initialize()
    assert not manager.available
    with pytest.raises(WorktreeError, match="符号链接"):
        await manager.create_manual("demo")
    assert list(outside.iterdir()) == []
