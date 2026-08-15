from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

from kcode.config import SubAgentConfig
from kcode.conversation import Conversation
from kcode.events import AgentStopped, AgentStopReason
from kcode.permissions import PermissionEngine, empty_permission_settings
from kcode.permissions.models import ApprovalChoice, PermissionMode
from kcode.session import AgentSession
from kcode.subagents.approval import ApprovalBroker
from kcode.subagents.catalog import AgentCatalog
from kcode.subagents.factory import ChildAgent
from kcode.subagents.manager import TaskManager
from kcode.subagents.models import AgentDefinition, AgentMeta, AgentSource
from kcode.subagents.service import SubAgentService
from kcode.tools.base import (
    FindFilesArgs,
    ReadFileArgs,
    RunCommandArgs,
    SearchCodeArgs,
    ToolCall,
    ToolContext,
)
from kcode.tools.command import RunCommandTool
from kcode.tools.filesystem import ReadFileTool
from kcode.tools.search import FindFilesTool, SearchCodeTool
from kcode.worktrees import WorktreeManager


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(path: Path) -> None:
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.name", "Kcode Test")
    git(path, "config", "user.email", "test@example.com")
    (path / "same.txt").write_text("main\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")


async def allow(_request):
    return ApprovalChoice.ALLOW_ONCE


class Runner:
    def __init__(self, conversation: Conversation, context: ToolContext, *, write: bool) -> None:
        self.conversation = conversation
        self.context = context
        self.write = write
        self.approve = allow
        self.cancelled = False

    async def run(self, prompt, session):
        if self.write:
            (self.context.workspace_root / "same.txt").write_text("child\n", encoding="utf-8")
        self.conversation.commit(prompt, "done")
        yield AgentStopped(AgentStopReason.COMPLETED, 1)

    def cancel(self):
        self.cancelled = True


class Factory:
    def __init__(self, *, write: bool, fail: bool = False) -> None:
        self.write = write
        self.fail = fail
        self.contexts: list[ToolContext] = []
        self.notices: list[str] = []

    def defined(
        self,
        definition,
        parent,
        mode,
        approve,
        *,
        background,
        context=None,
        worktree_notice="",
    ):
        if self.fail:
            raise ValueError("factory failed")
        child_context = context or parent.context
        self.contexts.append(child_context)
        self.notices.append(worktree_notice)
        conversation = Conversation()
        return ChildAgent(
            Runner(conversation, child_context, write=self.write),
            conversation,
            AgentSession(mode),
            mode,
        )


def service(repo: Path, *, write: bool, fail: bool = False):
    definition = AgentDefinition(
        AgentMeta(name="worker", description="Work", isolation="worktree"),
        "Work in isolation.",
        AgentSource.USER,
        repo / "worker.md",
        repo,
        "digest",
    )
    catalog = AgentCatalog((definition,))
    factory = Factory(write=write, fail=fail)
    manager = TaskManager(SubAgentConfig(), ApprovalBroker(allow))
    parent = SimpleNamespace(
        context=ToolContext(repo),
        approve=allow,
        delegation_snapshot=SimpleNamespace(mode=PermissionMode.DEFAULT),
    )
    worktrees = WorktreeManager(repo)
    return (
        SubAgentService(
            catalog,
            factory,
            manager,
            parent,
            SubAgentConfig(),
            worktrees,
        ),
        factory,
        manager,
        worktrees,
    )


async def test_isolated_service_cleans_no_result_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    subagents, factory, manager, worktrees = service(repo, write=False)
    result = await subagents.invoke(
        prompt="inspect",
        description="worker",
        subagent_type="worker",
        run_in_background=False,
        name=None,
    )
    assert result.status == "success"
    assert "Kept: false" in result.data["result"]
    isolated = factory.contexts[0].workspace_root
    assert not isolated.exists()
    assert str(repo) in factory.notices[0]
    assert worktrees.worktree_root is not None
    assert git(repo, "status", "--porcelain") == ""
    await manager.close()


async def test_isolated_service_keeps_file_result_and_main_unchanged(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    subagents, factory, manager, _worktrees = service(repo, write=True)
    result = await subagents.invoke(
        prompt="edit",
        description="worker",
        subagent_type="worker",
        run_in_background=False,
        name=None,
    )
    isolated = factory.contexts[0].workspace_root
    assert result.status == "success"
    assert "Kept: true" in result.data["result"]
    assert isolated.exists()
    assert (isolated / "same.txt").read_text(encoding="utf-8") == "child\n"
    assert (repo / "same.txt").read_text(encoding="utf-8") == "main\n"
    assert git(repo, "status", "--porcelain") == ""
    await manager.close()


async def test_factory_failure_returns_safe_cleanup_report(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    subagents, _factory, manager, worktrees = service(repo, write=False, fail=True)
    result = await subagents.invoke(
        prompt="edit",
        description="worker",
        subagent_type="worker",
        run_in_background=False,
        name=None,
    )
    assert result.status == "error"
    assert "<worktree-result>" in result.error.message
    assert "factory failed" not in result.error.message
    assert "Kept: false" in result.error.message
    assert worktrees.worktree_root is not None
    assert not any(path.is_dir() for path in worktrees.worktree_root.glob("agent-*"))
    await manager.close()


async def test_background_and_hook_reuse_isolated_lifecycle(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    subagents, factory, manager, _worktrees = service(repo, write=False)
    background = await subagents.invoke(
        prompt="inspect",
        description="background worker",
        subagent_type="worker",
        run_in_background=True,
        name=None,
    )
    record = manager.get(background.data["task_id"])
    isolated = factory.contexts[-1].workspace_root
    await record.task
    assert record.status.value == "completed"
    assert "Kept: false" in record.result
    assert not isolated.exists()

    hook = await subagents.launch_hook(
        prompt="inspect hook",
        subagent_type="worker",
        name="hook worker",
        mode=PermissionMode.DEFAULT,
    )
    hook_record = manager.get(hook.task_id)
    hook_isolated = factory.contexts[-1].workspace_root
    await hook_record.task
    assert "Kept: false" in hook_record.result
    assert not hook_isolated.exists()
    await manager.close()


async def test_foreground_cancellation_leaves_cleanup_to_registered_task(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    subagents, factory, manager, _worktrees = service(repo, write=False)
    original_launch = manager.launch
    entered = asyncio.Event()

    async def delayed_launch(*args, **kwargs):
        result = await original_launch(*args, **kwargs)
        entered.set()
        return result

    manager.launch = delayed_launch
    invocation = asyncio.create_task(
        subagents.invoke(
            prompt="inspect",
            description="worker",
            subagent_type="worker",
            run_in_background=False,
            name=None,
        )
    )
    await entered.wait()
    result = await invocation
    assert result.status == "success"
    assert "Kept: false" in result.data["result"]
    assert not factory.contexts[0].workspace_root.exists()
    await manager.close()


async def test_real_tools_and_command_sandbox_use_worktree_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    manager = WorktreeManager(repo)
    record = await manager.create_agent("task-tools")
    context = ToolContext(record.path)
    read = await ReadFileTool().execute(ReadFileArgs(path="same.txt"), context)
    found = await FindFilesTool().execute(FindFilesArgs(pattern="*.txt"), context)
    searched = await SearchCodeTool().execute(
        SearchCodeArgs(pattern="main", file_pattern="*.txt"), context
    )
    command = await RunCommandTool().execute(RunCommandArgs(command="pwd"), context)
    assert read.data["path"] == str(record.path / "same.txt")
    assert all(str(path).startswith(str(record.path)) for path in found.data["matches"])
    assert searched.data["matches"][0]["path"] == str(record.path / "same.txt")
    assert command.data["cwd"] == str(record.path)

    permissions = PermissionEngine(empty_permission_settings(record.path))
    denied = permissions.evaluate(
        ToolCall(0, "cwd", "run_command", "{}"),
        RunCommandArgs(command="pwd", cwd=str(repo)),
        context,
        PermissionMode.BYPASS_PERMISSIONS,
    )
    assert denied.verdict.value == "deny"
    assert denied.source.value == "sandbox"
    await manager.finalize(record, "task-tools")
