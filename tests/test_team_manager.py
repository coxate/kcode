from pathlib import Path

import pytest

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
from kcode.teams import Team, TeamCaller, TeamError, TeamMember, validate_team_slug
from kcode.teams.manager import TeamManager
from kcode.tools.base import ToolContext


@pytest.mark.parametrize(
    "value",
    ("", ".", "..", "A", "has space", "a/b", r"a\\b", "../x", "a" * 65),
)
def test_team_slug_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(TeamError):
        validate_team_slug(value)


def test_team_models_have_safe_identity_defaults() -> None:
    team = Team("core", "ship safely")
    member = TeamMember("alice", "task-000000000001", "general-purpose", "shared")
    assert team.id.startswith("team-")
    assert member.status.value == "starting"
    assert not member.wake_scheduled
    assert TeamCaller.member("alice", team.id).member_name == "alice"


async def allow(_request):
    return ApprovalChoice.ALLOW_ONCE


class Runner:
    def __init__(self, conversation, message_source=None):
        self.conversation = conversation
        self.message_source = message_source
        self.approve = allow
        self.cancelled = False

    async def run(self, prompt, session):
        if self.message_source is not None:
            self.message_source.take_team_messages()
        if self.cancelled:
            yield AgentStopped(AgentStopReason.CANCELLED, 1)
            return
        self.conversation.commit(prompt, "done")
        yield AgentStopped(AgentStopReason.COMPLETED, 1)

    def cancel(self):
        self.cancelled = True


class Parent:
    def __init__(self, root: Path):
        self.context = ToolContext(root)
        self.delegation_snapshot = None
        self.approve = allow


class Factory:
    def team_member(self, *args, **kwargs):
        conversation = Conversation()
        return ChildAgent(
            Runner(conversation, kwargs.get("message_source")),
            conversation,
            AgentSession(),
            PermissionMode.DEFAULT,
        )


def catalog(tmp_path: Path) -> AgentCatalog:
    definition = AgentDefinition(
        AgentMeta(name="general-purpose", description="General"),
        "work",
        AgentSource.BUILTIN,
        tmp_path / "general.md",
        tmp_path,
        "digest",
    )
    return AgentCatalog((definition,))


def manager(tmp_path: Path, *, enabled: bool = True):
    tasks = TaskManager(SubAgentConfig(), ApprovalBroker(allow))
    return (
        TeamManager(
            TeamConfig(enabled=enabled),
            SubAgentConfig(),
            catalog(tmp_path),
            Factory(),
            tasks,
            Parent(tmp_path),
            None,
        ),
        tasks,
    )


async def test_team_disabled_has_no_side_effects(tmp_path: Path) -> None:
    item, tasks = manager(tmp_path, enabled=False)
    with pytest.raises(TeamError) as caught:
        await item.create(TeamCaller.lead(), "core", "ship")
    assert caught.value.code == "teams_disabled"
    assert tasks.summaries() == ()
    await tasks.close()


async def test_shared_member_becomes_idle_and_resumes_once(tmp_path: Path) -> None:
    item, tasks = manager(tmp_path)
    await item.create(TeamCaller.lead(), "core", "ship")
    await item.spawn(
        TeamCaller.lead(),
        "alice",
        "first",
        isolation="shared",
    )
    member = item.active.members["alice"]
    record = tasks.get(member.task_id, expected_kind=TaskKind.TEAM_MEMBER)
    await record.task
    assert member.status.value == "idle"
    original_child = record.child
    result = await item.send_message(TeamCaller.lead(), "alice", "continue")
    assert result.data["awakened"] == ["alice"]
    await record.task
    assert member.status.value == "idle"
    assert record.child is original_child
    assert len(record.child.conversation.snapshot()) == 2
    await item.stop(TeamCaller.lead(), "alice")
    deleted = await item.delete(TeamCaller.lead())
    assert deleted.data["deleted"] is True
    await tasks.close()


async def test_lead_message_does_not_wake_model(tmp_path: Path) -> None:
    item, tasks = manager(tmp_path)
    await item.create(TeamCaller.lead(), "core", "ship")
    item.mailbox.register("alice")
    item.active.members["alice"] = TeamMember(
        "alice", "task-000000000001", "general-purpose", "shared"
    )
    item.active.members["alice"].status = "running"
    await item.send_message(TeamCaller.member("alice", item.active.id), "lead", "hello")
    assert item.mailbox.pending("lead") == 1
    assert tasks.running_count == 0
    await item.close()
    await tasks.close()
