from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from kcode.conversation import AssistantMessage, Conversation, ToolResultMessage, UserMessage
from kcode.events import AgentEvent, AgentStopped, AgentStopReason, TurnNotice
from kcode.orchestration import AgentRunner
from kcode.session import AgentSession
from kcode.skills.models import ForkContext, SkillMode
from kcode.skills.parser import render_skill_prompt
from kcode.skills.runtime import SkillRuntime
from kcode.skills.tools import LoadSkillTool
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class SkillInvocation:
    name: str
    display_text: str
    prompt: str
    mode: SkillMode
    warnings: tuple[str, ...] = ()


class SkillExecutor:
    def __init__(self, runtime: SkillRuntime) -> None:
        self.runtime = runtime
        self.active_runner: AgentRunner | None = None

    def prepare(self, name: str, arguments: str) -> SkillInvocation:
        loaded = self.runtime.catalog.load(name)
        if loaded.definition is None:
            raise ValueError(f"Unknown or unavailable Skill: {name}")
        display = f"/{name}" + (f" {arguments}" if arguments else "")
        return SkillInvocation(
            name,
            display,
            render_skill_prompt(loaded.definition, arguments),
            loaded.definition.meta.mode,
            loaded.warnings,
        )

    def cancel(self) -> None:
        if self.active_runner is not None:
            self.active_runner.cancel()

    async def run_fork(
        self,
        invocation: SkillInvocation,
        parent: AgentRunner,
        session: AgentSession,
    ) -> AsyncIterator[AgentEvent]:
        definition = self.runtime.catalog.get(invocation.name)
        if definition is None or definition.meta.mode is not SkillMode.FORK:
            raise ValueError(f"Skill '{invocation.name}' is not a fork Skill")
        conversation = Conversation()
        if definition.meta.fork_context is ForkContext.RECENT:
            for user, assistant in self._recent_text_turns(parent.conversation):
                conversation.commit(user, assistant)
        child_runtime = SkillRuntime(self.runtime.catalog)
        child_registry = self._child_registry(parent.registry, definition.meta.allowed_tools)
        child_registry.register(LoadSkillTool(child_runtime))
        child_executor = ToolExecutor(
            child_registry,
            parent.executor.permissions,
            parent.executor.local_store,
        )
        child = AgentRunner(
            parent.provider,
            conversation,
            child_registry,
            child_executor,
            parent.context,
            parent.approve,
            parent.config,
        )
        child.bind_skills(child_runtime)
        if parent.hook_engine is not None:
            child.bind_hooks(parent.hook_engine, parent.hook_session)
        child_session = AgentSession(
            session.permission_mode,
            initial_mode=session.initial_mode,
        )
        self.active_runner = child
        stopped: AgentStopped | None = None
        try:
            async for event in child.run(invocation.prompt, child_session):
                if isinstance(event, AgentStopped):
                    stopped = event
                yield event
        finally:
            self.active_runner = None
        if stopped is None:
            stopped = AgentStopped(
                AgentStopReason.INVALID_RESPONSE,
                0,
                "Fork ended without a stop event.",
            )
        if stopped.reason is AgentStopReason.CANCELLED:
            return
        turns = conversation.snapshot()
        if stopped.reason is AgentStopReason.COMPLETED and turns:
            assistant_text = turns[-1].assistant
        else:
            detail = stopped.detail or stopped.reason.value
            assistant_text = f"Skill /{invocation.name} failed: {detail}"
        warnings = await parent.commit_external_turn(
            invocation.prompt,
            assistant_text,
            session.permission_mode,
        )
        for warning in (*invocation.warnings, *warnings):
            yield TurnNotice(warning)

    @staticmethod
    def _child_registry(parent: ToolRegistry, allowed_tools: tuple[str, ...]) -> ToolRegistry:
        allowed = set(allowed_tools) if allowed_tools else parent.names()
        registry = ToolRegistry()
        for tool in parent.tools():
            if tool.spec.name == "load_skill":
                continue
            if tool.spec.name in allowed or tool.spec.always_visible:
                registry.register(tool)
        return registry

    @staticmethod
    def _recent_text_turns(conversation: Conversation) -> tuple[tuple[str, str], ...]:
        completed: list[tuple[str, str]] = []
        user: str | None = None
        saw_tools = False
        for message in conversation.messages_snapshot():
            if isinstance(message, UserMessage):
                user = message.content
                saw_tools = False
            elif isinstance(message, ToolResultMessage):
                saw_tools = True
            elif isinstance(message, AssistantMessage):
                if message.tool_calls:
                    saw_tools = True
                elif user is not None and message.content.strip():
                    if not saw_tools:
                        completed.append((user, message.content))
                    user = None
                    saw_tools = False
        return tuple(completed[-2:])
