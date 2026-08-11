from pathlib import Path

from kcode.config import ProviderConfig
from kcode.conversation import Conversation, UserMessage
from kcode.orchestration import AgentRunner, DelegationSnapshot
from kcode.permissions import (
    LocalPermissionStore,
    PermissionEngine,
    PermissionMode,
    empty_permission_settings,
)
from kcode.skills.runtime import SkillRuntime
from kcode.skills.tools import LoadSkillTool
from kcode.subagents.factory import SubAgentFactory
from kcode.subagents.models import AgentDefinition, AgentMeta, AgentSource
from kcode.subagents.provider import ProviderPool
from kcode.tools.base import ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import create_default_registry


class Provider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self):
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


def parent(tmp_path: Path):
    provider = Provider()
    registry = create_default_registry()
    runtime = SkillRuntime()
    registry.register(LoadSkillTool(runtime))
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
    runner.bind_skills(runtime)
    return runner, provider


def test_factory_definition_is_isolated_and_restricted(tmp_path: Path) -> None:
    runner, provider = parent(tmp_path)
    definition = AgentDefinition(
        AgentMeta(
            name="explore",
            description="Explore",
            tools=("read_file",),
            permission_mode=PermissionMode.PLAN,
        ),
        "Read only.",
        AgentSource.BUILTIN,
        tmp_path / "explore.md",
        tmp_path,
        "digest",
    )
    child = SubAgentFactory(ProviderPool(provider, {"main": provider.config})).defined(
        definition,
        runner,
        PermissionMode.DEFAULT,
        allow,
        background=False,
    )
    assert child.conversation is not runner.conversation
    assert child.mode is PermissionMode.PLAN
    assert child.runner.registry.names() == {"read_file", "load_skill"}
    assert "Read only." in child.runner.prompt_builder.build()


def test_factory_fork_uses_request_seed_and_control_proxies(tmp_path: Path) -> None:
    runner, provider = parent(tmp_path)
    runner._delegation_snapshot = DelegationSnapshot(
        (UserMessage("parent request"),),
        runner.registry.definitions(),
        PermissionMode.DEFAULT,
    )
    child = SubAgentFactory(ProviderPool(provider, {"main": provider.config})).fork(
        runner,
        PermissionMode.DEFAULT,
        allow,
    )
    assert child.runner._request_seed == (UserMessage("parent request"),)
    assert child.runner.registry.get("load_skill") is not runner.registry.get("load_skill")
