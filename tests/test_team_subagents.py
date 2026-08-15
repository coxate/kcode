from pathlib import Path

from kcode.config import ProviderConfig
from kcode.conversation import Conversation
from kcode.orchestration import AgentRunner
from kcode.permissions import LocalPermissionStore, PermissionEngine, empty_permission_settings
from kcode.permissions.models import PermissionMode
from kcode.subagents.factory import SubAgentFactory
from kcode.subagents.models import AgentDefinition, AgentMeta, AgentSource
from kcode.subagents.provider import ProviderPool
from kcode.teams import TeamCaller
from kcode.teams.mailbox import TeamMailbox
from kcode.teams.tools import member_tools
from kcode.tools.base import ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import create_default_registry


class Provider:
    display_name = "fake"
    model_name = "fake"

    def __init__(self) -> None:
        self.config = ProviderConfig(
            name="main",
            protocol="openai",
            model="fake",
            base_url="https://example.test",
            api_key="secret",
        )

    async def stream(self, messages, tools=(), tool_choice="auto"):
        if False:
            yield


async def allow(_request):
    return True


class Manager:
    def __getattr__(self, name):
        async def call(*args, **kwargs):
            raise AssertionError(name)

        return call


def test_real_factory_builds_bounded_team_member(tmp_path: Path) -> None:
    provider = Provider()
    registry = create_default_registry()
    settings = empty_permission_settings(tmp_path)
    parent = AgentRunner(
        provider,
        Conversation(),
        registry,
        ToolExecutor(
            registry,
            PermissionEngine(settings),
            LocalPermissionStore(settings.layers[0].path),
        ),
        ToolContext(tmp_path),
        allow,
    )
    definition = AgentDefinition(
        AgentMeta(name="worker", description="Work"),
        "Work safely.",
        AgentSource.BUILTIN,
        tmp_path / "worker.md",
        tmp_path,
        "digest",
    )
    mailbox = TeamMailbox()
    mailbox.register("alice")
    caller = TeamCaller.member("alice", "team-000000000001")
    collaboration = member_tools(Manager(), caller)
    child = SubAgentFactory(ProviderPool(provider, {"main": provider.config})).team_member(
        definition,
        parent,
        PermissionMode.DEFAULT,
        allow,
        context=ToolContext(tmp_path),
        collaboration_tools=collaboration,
        message_source=mailbox.source("alice"),
        team_notice="<team-context>alice</team-context>",
    )
    names = child.runner.registry.names()
    assert {item.spec.name for item in collaboration} <= names
    assert "agent" not in names
    assert not any(name.startswith("task_") for name in names)
    assert not {"team_create", "team_spawn", "team_stop", "team_delete"} & names
    assert "<team-context>alice" in child.runner.prompt_builder.build()


def test_plan_mode_exposes_only_read_only_team_tools(tmp_path: Path) -> None:
    from kcode.teams.tools import register_team_tools

    provider = Provider()
    registry = create_default_registry()
    register_team_tools(registry, Manager())
    settings = empty_permission_settings(tmp_path)
    runner = AgentRunner(
        provider,
        Conversation(),
        registry,
        ToolExecutor(
            registry,
            PermissionEngine(settings),
            LocalPermissionStore(settings.layers[0].path),
        ),
        ToolContext(tmp_path),
        allow,
    )
    names = {item.name for item in runner.tool_definitions(PermissionMode.PLAN)}
    assert {name for name in names if name.startswith("team_")} == {
        "team_status",
        "team_task_list",
    }
