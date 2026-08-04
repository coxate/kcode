from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import inspect

from kcode.conversation import (
    AssistantMessage,
    Conversation,
    ConversationMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.errors import ProviderError, ProviderErrorKind
from kcode.events import (
    ProviderEvent,
    StreamCompleted,
    TextDelta,
    ThinkingDelta,
    ToolCallDelta,
    ToolCallReady,
    ToolFinished,
    ToolStarted,
    TurnCompleted,
    TurnEvent,
    TurnNotice,
)
from kcode.providers.base import ChatProvider
from kcode.tools.base import ApprovalHandler, ToolCall, ToolContext, ToolResult
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import ToolRegistry

TOOL_SYSTEM_PROMPT = """You are KCode, a coding assistant with tools. Use at most one tool in a response.
Relative paths are based on the directory where KCode started. write_file only creates new files;
use edit_file for an existing file and provide old_text that occurs exactly once. After a tool result,
answer the user without requesting another tool."""


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    thinking: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str | None
    continuation_state: object | None


class StreamAccumulator:
    def __init__(self) -> None:
        self.text = ""
        self.thinking = ""
        self._calls: dict[int, list[str]] = {}
        self.stop_reason: str | None = None
        self.continuation_state = None
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
        )


class TurnRunner:
    def __init__(
        self,
        provider: ChatProvider,
        conversation: Conversation,
        registry: ToolRegistry,
        executor: ToolExecutor,
        context: ToolContext,
        approve: ApprovalHandler,
    ) -> None:
        self.provider = provider
        self.conversation = conversation
        self.registry = registry
        self.executor = executor
        self.context = context
        self.approve = approve

    async def _collect(
        self,
        messages: tuple[ConversationMessage, ...],
        *,
        tool_choice: str,
    ) -> AsyncIterator[ProviderEvent | ModelResponse]:
        accumulator = StreamAccumulator()
        parameters = inspect.signature(self.provider.stream).parameters
        stream = (
            self.provider.stream(messages, self.registry.definitions(), tool_choice=tool_choice)  # type: ignore[arg-type]
            if "tools" in parameters
            else self.provider.stream(messages)
        )
        async for event in stream:
            accumulator.feed(event)
            yield event
        if not accumulator.completed:
            raise ProviderError(ProviderErrorKind.INVALID_RESPONSE, "The stream ended without a completion event.")
        yield accumulator.response()

    async def run(self, user_text: str) -> AsyncIterator[TurnEvent]:
        history = self.conversation.messages_snapshot()
        supports_tools = "tools" in inspect.signature(self.provider.stream).parameters
        first_messages: tuple[ConversationMessage, ...] = (
            *((SystemMessage(TOOL_SYSTEM_PROMPT),) if supports_tools else ()),
            *history,
            UserMessage(user_text),
        )
        first: ModelResponse | None = None
        async for item in self._collect(first_messages, tool_choice="auto"):
            if isinstance(item, ModelResponse):
                first = item
            else:
                yield item
        assert first is not None
        if not first.tool_calls:
            if not first.text.strip():
                raise ProviderError(ProviderErrorKind.INVALID_RESPONSE, "The provider returned an empty answer.")
            self.conversation.commit_messages((UserMessage(user_text), AssistantMessage(first.text)))
            yield TurnCompleted(True)
            return

        assistant = AssistantMessage(
            first.text if first.continuation_state is not None else "",
            first.tool_calls,
            first.continuation_state,  # type: ignore[arg-type]
        )
        results: list[ToolResultMessage] = []
        if len(first.tool_calls) > 1:
            for call in first.tool_calls:
                yield ToolCallReady(call)
                result = ToolResult.failure(
                    "multiple_tool_calls", "This version executes at most one tool per turn."
                )
                results.append(ToolResultMessage(call.id, call.name, result))
                yield ToolFinished(call, result)
        else:
            call = first.tool_calls[0]
            yield ToolCallReady(call)
            yield ToolStarted(call)
            result = await self.executor.execute(call, self.context, self.approve)
            results.append(ToolResultMessage(call.id, call.name, result))
            yield ToolFinished(call, result)

        second_messages: tuple[ConversationMessage, ...] = (
            *((SystemMessage(TOOL_SYSTEM_PROMPT),) if supports_tools else ()),
            *history,
            UserMessage(user_text),
            assistant,
            *results,
        )
        second: ModelResponse | None = None
        async for item in self._collect(second_messages, tool_choice="none"):
            if isinstance(item, ModelResponse):
                second = item
            else:
                yield item
        assert second is not None
        if second.tool_calls:
            yield TurnNotice("模型再次请求工具；本版本需要 Agent Loop 才能继续。")
            yield TurnCompleted(False)
            return
        if not second.text.strip():
            raise ProviderError(ProviderErrorKind.INVALID_RESPONSE, "The provider returned an empty final answer.")
        self.conversation.commit_messages(
            (UserMessage(user_text), assistant, *results, AssistantMessage(second.text))
        )
        yield TurnCompleted(True)
