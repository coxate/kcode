from __future__ import annotations

from dataclasses import dataclass

from kcode.config import AgentConfig
from kcode.conversation import Conversation
from kcode.hooks.runtime import InMemoryHookSession
from kcode.orchestration import AgentRunner
from kcode.permissions.models import PermissionMode
from kcode.session import AgentSession
from kcode.skills.runtime import SkillRuntime
from kcode.skills.tools import LoadSkillTool
from kcode.subagents.filter import defined_registry, fork_registry, skill_fork_registry
from kcode.subagents.models import AgentDefinition, restricted_mode
from kcode.subagents.provider import ProviderPool
from kcode.tools.base import ApprovalHandler
from kcode.tools.executor import ToolExecutor


@dataclass(frozen=True, slots=True)
class ChildAgent:
    runner: AgentRunner
    conversation: Conversation
    session: AgentSession
    mode: PermissionMode


class SubAgentFactory:
    def __init__(self, providers: ProviderPool) -> None:
        self.providers = providers

    @staticmethod
    def _skills(parent: AgentRunner) -> SkillRuntime:
        catalog = parent.skill_runtime.catalog if parent.skill_runtime is not None else None
        return SkillRuntime(catalog)

    @staticmethod
    def _register_load_skill(parent: AgentRunner, registry, runtime: SkillRuntime) -> None:
        if parent.registry.get("load_skill") is not None:
            registry.register(LoadSkillTool(runtime))

    def defined(
        self,
        definition: AgentDefinition,
        parent: AgentRunner,
        parent_mode: PermissionMode,
        approve: ApprovalHandler,
        *,
        background: bool,
    ) -> ChildAgent:
        provider = self.providers.get(definition.meta.model, parent.provider)
        runtime = self._skills(parent)
        registry = defined_registry(parent.registry, definition.meta, background=background)
        self._register_load_skill(parent, registry, runtime)
        conversation = Conversation()
        mode = restricted_mode(parent_mode, definition.meta.permission_mode)
        iterations = definition.meta.max_turns or parent.config.max_iterations
        config = AgentConfig(
            max_iterations=iterations,
            max_parallel_tools=parent.config.max_parallel_tools,
        )
        prompt_builder = parent.prompt_builder.with_appended_content(
            "custom_instructions",
            f"## SubAgent Role: {definition.meta.name}\n\n{definition.body}",
        )
        runner = AgentRunner(
            provider,
            conversation,
            registry,
            ToolExecutor(registry, parent.executor.permissions, parent.executor.local_store),
            parent.context,
            approve,
            config,
            prompt_builder=prompt_builder,
            is_subagent=True,
        )
        runner.bind_skills(runtime)
        if parent.hook_engine is not None:
            runner.bind_hooks(parent.hook_engine, InMemoryHookSession())
        return ChildAgent(runner, conversation, AgentSession(mode), mode)

    def fork(
        self,
        parent: AgentRunner,
        parent_mode: PermissionMode,
        approve: ApprovalHandler,
    ) -> ChildAgent:
        snapshot = parent.delegation_snapshot
        if snapshot is None:
            raise RuntimeError("The parent Agent has no request snapshot to fork.")
        runtime = self._skills(parent)
        registry = fork_registry(parent.registry)
        self._register_load_skill(parent, registry, runtime)
        conversation = Conversation()
        runner = AgentRunner(
            parent.provider,
            conversation,
            registry,
            ToolExecutor(registry, parent.executor.permissions, parent.executor.local_store),
            parent.context,
            approve,
            parent.config,
            prompt_builder=parent.prompt_builder,
            request_seed=snapshot.request_messages,
            is_subagent=True,
        )
        runner.bind_skills(runtime)
        if parent.hook_engine is not None:
            runner.bind_hooks(parent.hook_engine, InMemoryHookSession())
        return ChildAgent(
            runner,
            conversation,
            AgentSession(parent_mode),
            parent_mode,
        )

    def skill_fork(
        self,
        parent: AgentRunner,
        parent_mode: PermissionMode,
        approve: ApprovalHandler,
        allowed_tools: tuple[str, ...],
        conversation: Conversation,
    ) -> ChildAgent:
        runtime = self._skills(parent)
        registry = skill_fork_registry(parent.registry, allowed_tools)
        self._register_load_skill(parent, registry, runtime)
        runner = AgentRunner(
            parent.provider,
            conversation,
            registry,
            ToolExecutor(registry, parent.executor.permissions, parent.executor.local_store),
            parent.context,
            approve,
            parent.config,
            prompt_builder=parent.prompt_builder,
            is_subagent=True,
        )
        runner.bind_skills(runtime)
        if parent.hook_engine is not None:
            runner.bind_hooks(parent.hook_engine, InMemoryHookSession())
        return ChildAgent(runner, conversation, AgentSession(parent_mode), parent_mode)
