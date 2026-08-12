import subprocess
from pathlib import Path
from types import SimpleNamespace

from kcode.config import SubAgentConfig, TeamConfig
from kcode.conversation import Conversation
from kcode.events import AgentStopped, AgentStopReason
from kcode.permissions.models import ApprovalChoice, PermissionMode
from kcode.session import AgentSession
from kcode.subagents.approval import ApprovalBroker
from kcode.subagents.catalog import AgentCatalog
from kcode.subagents.factory import ChildAgent
from kcode.subagents.manager import TaskManager
from kcode.subagents.models import AgentDefinition, AgentMeta, AgentSource, TaskKind
from kcode.teams import TeamCaller
from kcode.teams.manager import TeamManager
from kcode.tools.base import ToolContext
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
    def __init__(self, conversation, context, source):
        self.conversation = conversation
        self.context = context
        self.source = source
        self.approve = allow

    async def run(self, prompt, session):
        self.source.take_team_messages()
        (self.context.workspace_root / "same.txt").write_text(prompt + "\n", encoding="utf-8")
        self.conversation.commit(prompt, "done")
        yield AgentStopped(AgentStopReason.COMPLETED, 1)

    def cancel(self):
        return None


class Factory:
    def team_member(self, *args, **kwargs):
        conversation = Conversation()
        return ChildAgent(
            Runner(conversation, kwargs["context"], kwargs["message_source"]),
            conversation,
            AgentSession(),
            PermissionMode.DEFAULT,
        )


async def test_two_team_members_modify_separate_worktrees(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    make_repo(repo)
    definition = AgentDefinition(
        AgentMeta(name="general-purpose", description="General"),
        "work",
        AgentSource.BUILTIN,
        repo / "general.md",
        repo,
        "digest",
    )
    tasks = TaskManager(SubAgentConfig(), ApprovalBroker(allow))
    worktrees = WorktreeManager(repo)
    manager = TeamManager(
        TeamConfig(enabled=True),
        SubAgentConfig(),
        AgentCatalog((definition,)),
        Factory(),
        tasks,
        SimpleNamespace(
            context=ToolContext(repo),
            delegation_snapshot=SimpleNamespace(mode=PermissionMode.DEFAULT),
            approve=allow,
        ),
        worktrees,
    )
    await manager.create(TeamCaller.lead(), "core", "ship")
    await manager.spawn(TeamCaller.lead(), "alice", "alice")
    await manager.spawn(TeamCaller.lead(), "bob", "bob")
    alice = manager.active.members["alice"]
    bob = manager.active.members["bob"]
    alice_record = tasks.get(alice.task_id, TaskKind.TEAM_MEMBER)
    bob_record = tasks.get(bob.task_id, TaskKind.TEAM_MEMBER)
    await alice_record.task
    await bob_record.task

    assert alice.worktree.path != bob.worktree.path
    assert alice.worktree.branch != bob.worktree.branch
    assert (alice.worktree.path / "same.txt").read_text() == "alice\n"
    assert (bob.worktree.path / "same.txt").read_text() == "bob\n"
    assert (repo / "same.txt").read_text() == "main\n"
    assert git(repo, "status", "--porcelain") == ""

    await manager.stop(TeamCaller.lead(), "alice")
    await manager.stop(TeamCaller.lead(), "bob")
    assert alice.worktree.path.exists()
    assert bob.worktree.path.exists()
    await manager.delete(TeamCaller.lead())
    assert alice.worktree.path.exists()
    assert bob.worktree.path.exists()
    await tasks.close()
