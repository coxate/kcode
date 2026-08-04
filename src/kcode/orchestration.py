from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Sequence
from contextlib import suppress
from dataclasses import dataclass

from kcode.config import AgentConfig
from kcode.conversation import (
    AssistantMessage,
    Conversation,
    ConversationMessage,
    ProviderContinuationState,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.errors import ProviderError
from kcode.events import (
    AgentEvent,
    AgentPhase,
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    ApprovalPending,
    ProviderEvent,
    StreamCompleted,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    TokenUsageUpdated,
    ToolCallDelta,
    ToolCallReady,
    ToolFinished,
    ToolStarted,
    TurnNotice,
    UsageReported,
)
from kcode.providers.base import ChatProvider
from kcode.session import AgentMode, AgentSession
from kcode.tools.base import ApprovalHandler, ToolCall, ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import ToolRegistry
from kcode.tools.scheduler import ToolScheduler

DO_SYSTEM_PROMPT = """You are KCode, an autonomous coding assistant with tools.
Continue calling tools and using their results until the user's task is complete. You may request
multiple independent tools in one response. Relative paths use the KCode startup directory.
write_file only creates new files; use edit_file for an existing file and provide old_text that
occurs exactly once. Follow every tool safety decision and finish with a clear answer."""

PLAN_SYSTEM_PROMPT = """You are KCode in Plan Mode. Investigate the user's request with read-only
tools, then return an actionable implementation plan. You may read files, find files, search code,
and run strictly allowlisted read-only commands. Do not write, edit, or run commands with side
effects. A denied tool result is authoritative; adjust the plan instead of retrying unsafe work."""

PLAN_CONTEXT_PROMPT = """The user previously approved the following planning context. Use it only
for the current request. It cannot override system safety rules or the user's current instructions:

{plan}"""

PLAN_TOOL_NAMES = {"read_file", "find_files", "search_code", "run_command"}


class _AgentCancelled(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    thinking: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str | None
    continuation_state: ProviderContinuationState | None
    usage: TokenUsage | None


class StreamAccumulator:
    def __init__(self) -> None:
        self.text = ""
        self.thinking = ""
        self._calls: dict[int, list[str]] = {}
        self.stop_reason: str | None = None
        self.continuation_state: ProviderContinuationState | None = None
        self.usage: TokenUsage | None = None
        self.completed = False

    def feed(self, event: ProviderEvent) -> None:
        if isinstance(event, TextDelta):
            self.text += event.text
        elif isinstance(event, ThinkingDelta):
            self.thinking += event.text
        elif isinstance(event, ToolCallDelta):
            parts = self._calls.setdefault(event.index, ["", "", ""])
            parts[0] += event.id_fragment
            parts[1] += event.name_fragment
            parts[2] += event.arguments_fragment
        elif isinstance(event, UsageReported):
            self.usage = event.usage
        elif isinstance(event, StreamCompleted):
            self.completed = True
            self.stop_reason = event.stop_reason
            self.continuation_state = event.continuation_state

    def response(self) -> ModelResponse:
        calls = tuple(
            ToolCall(index, parts[0] or f"invalid_call_{index}", parts[1], parts[2])
            for index, parts in sorted(self._calls.items())
        )
        return ModelResponse(
            self.text,
            self.thinking,
            calls,
            self.stop_reason,
            self.continuation_state,
            self.usage,
        )


class AgentRunner:
    def __init__(
        self,
        provider: ChatProvider,
        conversation: Conversation,
        registry: ToolRegistry,
        executor: ToolExecutor,
        context: ToolContext,
        approve: ApprovalHandler,
        config: AgentConfig | None = None,
        scheduler: ToolScheduler | None = None,
    ) -> None:
        self.provider = provider
        self.conversation = conversation
        self.registry = registry
        self.executor = executor
        self.context = context
        self.approve = approve
        self.config = config or AgentConfig()
        self.scheduler = scheduler or ToolScheduler(
            executor, self.config.max_parallel_tools
        )
        self._cancel_event: asyncio.Event | None = None

    def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    async def _next_or_cancel(
        self,
        iterator: AsyncIterator[ProviderEvent],
        cancel_event: asyncio.Event,
    ) -> ProviderEvent:
        next_task = asyncio.create_task(anext(iterator))
        cancel_task = asyncio.create_task(cancel_event.wait())
        done, _ = await asyncio.wait(
            (next_task, cancel_task), return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_task in done and cancel_event.is_set():
            next_task.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await next_task
            close = getattr(iterator, "aclose", None)
            if close is not None:
                with suppress(Exception):
                    await close()
            raise _AgentCancelled
        cancel_task.cancel()
        with suppress(asyncio.CancelledError):
            await cancel_task
        return await next_task

    async def _collect(
        self,
        messages: Sequence[ConversationMessage],
        tools,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[ProviderEvent | ModelResponse]:
        accumulator = StreamAccumulator()
        parameters = inspect.signature(self.provider.stream).parameters
        stream = (
            self.provider.stream(messages, tools, tool_choice="auto")  # type: ignore[arg-type]
            if "tools" in parameters
            else self.provider.stream(messages)
        )
        iterator = stream.__aiter__()
        while True:
            try:
                event = await self._next_or_cancel(iterator, cancel_event)
            except StopAsyncIteration:
                break
            accumulator.feed(event)
            yield event
        if not accumulator.completed:
            raise ValueError("The stream ended without a completion event.")
        yield accumulator.response()

    async def run(
        self,
        user_text: str,
        session: AgentSession | None = None,
    ) -> AsyncIterator[AgentEvent]:
        if self._cancel_event is not None:
            raise RuntimeError("AgentRunner is already running.")
        active_session = session or AgentSession()
        mode = active_session.mode
        plan = active_session.consume_plan() if mode == AgentMode.DO else None
        cancel_event = asyncio.Event()
        self._cancel_event = cancel_event
        history = self.conversation.messages_snapshot()
        handle = self.conversation.begin_turn(user_text)
        current: list[ConversationMessage] = [UserMessage(user_text)]
        cumulative_usage: TokenUsage | None = None
        unknown_rounds = 0
        turn_open = True
        supports_tools = "tools" in inspect.signature(self.provider.stream).parameters
        definitions = self.registry.definitions(
            PLAN_TOOL_NAMES if mode == AgentMode.PLAN else None
        )
        system_messages: tuple[SystemMessage, ...] = ()
        if supports_tools:
            system_messages = (
                SystemMessage(PLAN_SYSTEM_PROMPT if mode == AgentMode.PLAN else DO_SYSTEM_PROMPT),
                *((SystemMessage(PLAN_CONTEXT_PROMPT.format(plan=plan)),) if plan else ()),
            )
        iteration = 0

        try:
            for iteration in range(1, self.config.max_iterations + 1):
                if cancel_event.is_set():
                    raise _AgentCancelled
                yield AgentProgress(
                    mode,
                    iteration,
                    self.config.max_iterations,
                    AgentPhase.MODEL,
                )
                response: ModelResponse | None = None
                request_messages = (*system_messages, *history, *current)
                async for item in self._collect(
                    request_messages, definitions, cancel_event
                ):
                    if isinstance(item, ModelResponse):
                        response = item
                    elif isinstance(item, (TextDelta, ThinkingDelta)):
                        yield item
                assert response is not None

                request_usage = response.usage or TokenUsage()
                cumulative_usage = (
                    request_usage
                    if cumulative_usage is None
                    else cumulative_usage.plus(request_usage)
                )
                yield TokenUsageUpdated(iteration, request_usage, cumulative_usage)

                if not response.tool_calls:
                    if not response.text.strip():
                        self.conversation.stop_turn(handle)
                        turn_open = False
                        yield AgentStopped(
                            AgentStopReason.INVALID_RESPONSE,
                            iteration,
                            "模型返回了空响应。",
                        )
                        return
                    final = AssistantMessage(response.text)
                    self.conversation.complete_turn(handle, final)
                    turn_open = False
                    if mode == AgentMode.PLAN:
                        active_session.record_plan(response.text)
                    yield AgentProgress(
                        mode,
                        iteration,
                        self.config.max_iterations,
                        AgentPhase.COMPLETE,
                    )
                    yield AgentStopped(AgentStopReason.COMPLETED, iteration)
                    return

                assistant = AssistantMessage(
                    response.text if response.continuation_state is not None else "",
                    response.tool_calls,
                    response.continuation_state,
                )
                prepared = tuple(
                    self.executor.prepare(call, self.context, mode)
                    for call in response.tool_calls
                )
                all_unknown = bool(prepared) and all(
                    item.error is not None
                    and item.error.error is not None
                    and item.error.error.code == "unknown_tool"
                    for item in prepared
                )
                unknown_rounds = unknown_rounds + 1 if all_unknown else 0

                for call in response.tool_calls:
                    yield ToolCallReady(call)

                result_messages: list[ToolResultMessage] = []
                for batch_index, batch in enumerate(self.scheduler.batches(prepared), 1):
                    approvals = tuple(
                        item.approval for item in batch.calls if item.approval is not None
                    )
                    if approvals:
                        yield AgentProgress(
                            mode,
                            iteration,
                            self.config.max_iterations,
                            AgentPhase.APPROVAL,
                            batch_index,
                        )
                        for request in approvals:
                            yield ApprovalPending(request)
                    yield AgentProgress(
                        mode,
                        iteration,
                        self.config.max_iterations,
                        AgentPhase.TOOLS,
                        batch_index,
                    )
                    for item in batch.calls:
                        yield ToolStarted(item.call)
                    results = await self.scheduler.execute_batch(
                        batch,
                        self.context,
                        self.approve,
                        cancel_event,
                    )
                    for item, result in zip(batch.calls, results, strict=True):
                        result_messages.append(
                            ToolResultMessage(item.call.id, item.call.name, result)
                        )
                        yield ToolFinished(item.call, result)

                checkpoint = tuple(result_messages)
                self.conversation.checkpoint_tool_step(handle, assistant, checkpoint)
                current.extend((assistant, *checkpoint))

                if cancel_event.is_set():
                    raise _AgentCancelled
                if unknown_rounds >= 2:
                    self.conversation.stop_turn(handle)
                    turn_open = False
                    yield AgentStopped(
                        AgentStopReason.UNKNOWN_TOOL_LIMIT,
                        iteration,
                        "模型连续两轮只请求未知工具。",
                    )
                    return
                if iteration >= self.config.max_iterations:
                    self.conversation.stop_turn(handle)
                    turn_open = False
                    yield AgentStopped(
                        AgentStopReason.ITERATION_LIMIT,
                        iteration,
                        f"已达到 {self.config.max_iterations} 轮安全上限。",
                    )
                    return
        except _AgentCancelled:
            if turn_open:
                self.conversation.stop_turn(handle)
                turn_open = False
            yield AgentStopped(AgentStopReason.CANCELLED, iteration, "用户已取消当前任务。")
        except ProviderError as exc:
            if turn_open:
                self.conversation.stop_turn(handle)
                turn_open = False
            yield TurnNotice(f"{exc.kind.value}: {exc}")
            yield AgentStopped(AgentStopReason.STREAM_ERROR, iteration, str(exc))
        except (ValueError, TypeError) as exc:
            if turn_open:
                self.conversation.stop_turn(handle)
                turn_open = False
            yield TurnNotice(str(exc))
            yield AgentStopped(AgentStopReason.INVALID_RESPONSE, iteration, str(exc))
        finally:
            if turn_open:
                self.conversation.stop_turn(handle)
            self._cancel_event = None


# 兼容 0.2.0 中引用的内部名称；新代码统一使用 AgentRunner。
TurnRunner = AgentRunner
