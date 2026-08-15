from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from kcode.worktrees import GitWorktreeClient, GitWorktreeError, parse_worktree_porcelain


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path: Path) -> str:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Kcode Test")
    git(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    git(path, "add", "README.md")
    git(path, "commit", "-qm", "base")
    return git(path, "rev-parse", "HEAD")


def test_parse_porcelain_paths_and_rejects_damage(tmp_path: Path) -> None:
    sample = (
        f"worktree {tmp_path / 'with space'}\0HEAD {'a' * 40}\0"
        "branch refs/heads/demo\0\0"
        f"worktree {tmp_path / '中文'}\0HEAD {'b' * 40}\0detached\0\0"
    ).encode()
    entries = parse_worktree_porcelain(sample)
    assert [item.path.name for item in entries] == ["with space", "中文"]
    assert entries[0].branch == "demo"
    assert entries[1].detached
    with pytest.raises(GitWorktreeError):
        parse_worktree_porcelain(sample[:-1])
    with pytest.raises(GitWorktreeError):
        parse_worktree_porcelain(f"worktree /tmp/x\0HEAD {'x' * 40}\0\0".encode())


async def test_real_repository_lifecycle_and_dirty_states(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = make_repo(repo)
    client = GitWorktreeClient()
    assert (await client.discover(repo / "README.md".replace("README.md", "."))).root == repo
    assert await client.head(repo) == base
    assert not await client.is_dirty(repo)
    target = tmp_path / "work tree 中文"
    await client.add(repo, target, "kcode-worktree/demo", base)
    entries = await client.list(repo)
    assert any(item.path == target and item.branch == "kcode-worktree/demo" for item in entries)
    (target / "new.txt").write_text("dirty", encoding="utf-8")
    assert (await client.status(target)).dirty
    (target / "new.txt").unlink()
    await client.remove(repo, target)
    await client.delete_branch(repo, "kcode-worktree/demo")
    assert not target.exists()


async def test_runner_timeout_reaps_process(tmp_path: Path, monkeypatch) -> None:
    client = GitWorktreeClient(timeout_seconds=0.05)
    create_process = asyncio.create_subprocess_exec

    async def fake_exec(*args, **kwargs):
        return await create_process(
            "sh",
            "-c",
            "sleep 10",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(GitWorktreeError, match="超时"):
        await client._run(tmp_path, "status")


async def test_runner_disables_interaction_and_limits_output(tmp_path: Path, monkeypatch) -> None:
    client = GitWorktreeClient(max_stream_bytes=8)
    create_process = asyncio.create_subprocess_exec
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured.update(kwargs)
        return await create_process(
            "sh",
            "-c",
            "printf 123456789",
            stdin=kwargs["stdin"],
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
        )

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(GitWorktreeError, match="上限"):
        await client._run(tmp_path, "status")
    assert captured["stdin"] == asyncio.subprocess.DEVNULL
    assert captured["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert captured["env"]["GIT_ASKPASS"] == ""
