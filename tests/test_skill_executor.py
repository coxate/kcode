import asyncio
from pathlib import Path

from kcode.conversation import AssistantMessage, Conversation, ToolResultMessage
from kcode.errors import ProviderError, ProviderErrorKind
from kcode.events import (
    AgentStopped,
    AgentStopReason,
    StreamCompleted,
    TextDelta,
    TokenUsage,
    UsageReported,
)
from kcode.orchestration import AgentRunner
from kcode.permissions import LocalPermissionStore, PermissionEngine, empty_permission_settings
from kcode.session import AgentSession
from kcode.skills.catalog import SkillCatalog
from kcode.skills.executor import SkillExecutor
from kcode.skills.models import ForkContext, SkillDefinition, SkillMeta, SkillMode, SkillSource
from kcode.skills.runtime import SkillRuntime
from kcode.skills.tools import LoadSkillTool
from kcode.tools.base import ToolCall, ToolContext, ToolResult
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import create_default_registry


async def allow(_request):
    return True


class Provider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self) -> None:
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((tuple(messages), tuple(tools)))
        yield TextDelta("reviewed")
        yield UsageReported(TokenUsage(10, 2, 12))
        yield StreamCompleted("stop")


class FailingProvider(Provider):
    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((tuple(messages), tuple(tools)))
        if False:
            yield
        raise ProviderError(ProviderErrorKind.NETWORK, "provider unavailable")


class WaitingProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.wait = asyncio.Event()

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((tuple(messages), tuple(tools)))
        yield TextDelta("partial")
        await self.wait.wait()
        yield StreamCompleted("stop")


def fork_definition(context: ForkContext = ForkContext.NONE) -> SkillDefinition:
    path = Path("/tmp/review/SKILL.md")
    return SkillDefinition(
        SkillMeta(
            name="review",
            description="Review code",
            allowed_tools=("read_file",),
            mode=SkillMode.FORK,
            fork_context=context,
        ),
        "Review $ARGUMENTS",
        SkillSource.BUILTIN,
        path,
        path.parent.parent,
        "digest",
    )


def parent_runner(tmp_path: Path, provider: Provider, conversation: Conversation):
    registry = create_default_registry()
    runtime = SkillRuntime(SkillCatalog((fork_definition(),)))
    registry.register(LoadSkillTool(runtime))
    settings = empty_permission_settings(tmp_path)
    runner = AgentRunner(
        provider,
        conversation,
        registry,
        ToolExecutor(
            registry,
            PermissionEngine(settings),
            LocalPermissionStore(settings.layers[0].path),
        ),
        ToolContext(tmp_path),
        allow,
    )
    return runner, runtime


async def test_fork_success_is_restricted_and_returns_complete_main_turn(tmp_path: Path) -> None:
    provider = Provider()
    conversation = Conversation()
    parent, runtime = parent_runner(tmp_path, provider, conversation)
    executor = SkillExecutor(runtime)
    invocation = executor.prepare("review", "concurrency")
    events = [event async for event in executor.run_fork(invocation, parent, AgentSession())]
    assert any(
        isinstance(event, AgentStopped) and event.reason is AgentStopReason.COMPLETED
        for event in events
    )
    turn = conversation.snapshot()[0]
    assert turn.user.endswith("Review concurrency")
    assert turn.assistant == "reviewed"
    tool_names = {tool.name for tool in provider.requests[0][1]}
    assert tool_names == {"read_file", "load_skill"}


def test_recent_context_excludes_tool_rounds() -> None:
    conversation = Conversation()
    first = conversation.begin_turn("plain one")
    conversation.complete_turn(first, AssistantMessage("answer one"))
    tool_turn = conversation.begin_turn("tool turn")
    assistant = AssistantMessage("", (ToolCall(0, "call", "read_file", "{}"),))
    conversation.checkpoint_tool_step(
        tool_turn,
        assistant,
        (ToolResultMessage("call", "read_file", ToolResult.success({"ok": True})),),
    )
    conversation.complete_turn(tool_turn, AssistantMessage("tool answer"))
    last = conversation.begin_turn("plain two")
    conversation.complete_turn(last, AssistantMessage("answer two"))
    assert SkillExecutor._recent_text_turns(conversation) == (
        ("plain one", "answer one"),
        ("plain two", "answer two"),
    )


async def test_fork_provider_failure_returns_a_persistable_failure_turn(tmp_path: Path) -> None:
    provider = FailingProvider()
    conversation = Conversation()
    parent, runtime = parent_runner(tmp_path, provider, conversation)
    executor = SkillExecutor(runtime)
    invocation = executor.prepare("review", "")
    events = [event async for event in executor.run_fork(invocation, parent, AgentSession())]
    assert any(
        isinstance(event, AgentStopped) and event.reason is AgentStopReason.STREAM_ERROR
        for event in events
    )
    assert len(conversation.snapshot()) == 1
    assert "failed" in conversation.snapshot()[0].assistant


async def test_fork_cancel_does_not_write_main_history(tmp_path: Path) -> None:
    provider = WaitingProvider()
    conversation = Conversation()
    parent, runtime = parent_runner(tmp_path, provider, conversation)
    executor = SkillExecutor(runtime)
    invocation = executor.prepare("review", "")

    async def consume() -> None:
        async for _ in executor.run_fork(invocation, parent, AgentSession()):
            pass

    task = asyncio.create_task(consume())
    for _ in range(50):
        if executor.active_runner is not None:
            break
        await asyncio.sleep(0)
    executor.cancel()
    await task
    assert conversation.snapshot() == ()
