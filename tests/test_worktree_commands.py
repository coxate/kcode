from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from kcode.ui.app import KCodeApp
from kcode.worktrees import WorktreeManager


class Provider:
    display_name = "fake"
    model_name = "fake"

    async def stream(self, messages, tools=(), tool_choice="auto"):
        if False:
            yield


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


async def test_app_worktree_command_methods_complete_manual_cycle(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    app = KCodeApp(Provider(), cwd=repo, worktree_manager=WorktreeManager(repo))
    notices: list[tuple[str, str]] = []

    async def notice(text: str, style: str = "system") -> None:
        notices.append((text, style))

    app._append_notice = notice
    await app.command_worktree_create("demo")
    assert "Worktree 已创建" in notices[-1][0]
    await app.command_worktree_list()
    assert "名称：demo" in notices[-1][0]
    await app.command_worktree_status("demo")
    assert "可安全删除：true" in notices[-1][0]
    await app.command_worktree_remove("demo")
    assert "Kept: false" in notices[-1][0]
    assert app.cwd == repo
    assert git(repo, "status", "--porcelain") == ""
    await app.task_manager.close()


async def test_app_non_git_worktree_error_does_not_raise(tmp_path: Path) -> None:
    app = KCodeApp(Provider(), cwd=tmp_path, worktree_manager=WorktreeManager(tmp_path))
    notices: list[tuple[str, str]] = []

    async def notice(text: str, style: str = "system") -> None:
        notices.append((text, style))

    app._append_notice = notice
    await app.command_worktree_list()
    assert notices[-1][1] == "error"
    assert notices[-1][0]
    await app.task_manager.close()


async def test_app_exit_keeps_event_loop_clean(tmp_path: Path) -> None:
    app = KCodeApp(Provider(), cwd=tmp_path)
    await app.task_manager.close()
    await asyncio.sleep(0)
