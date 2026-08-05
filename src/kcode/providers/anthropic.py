from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

import anthropic

from kcode.config import ProviderConfig
from kcode.conversation import (
    AssistantMessage,
    ChatMessage,
    ConversationMessage,
    EnvironmentMessage,
    ProviderContinuationState,
    StableSystemMessage,
    SystemMessage,
    SystemReminderMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.errors import ProviderError, ProviderErrorKind
from kcode.events import (
    ProviderEvent,
    StreamCompleted,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    ToolCallDelta,
    UsageReported,
)
from kcode.tools.base import ToolDefinition


def _optional_nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _serialize(
    messages: Sequence[ConversationMessage],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    systems: list[dict[str, Any]] = []
    output: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message, StableSystemMessage):
            systems.append(
                {
                    "type": "text",
                    "text": message.content,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        elif isinstance(message, EnvironmentMessage):
            systems.append({"type": "text", "text": message.content})
        elif isinstance(message, SystemReminderMessage):
            systems.append({"type": "text", "text": message.render()})
        elif isinstance(message, SystemMessage) or (
            isinstance(message, ChatMessage) and message.role == "system"
        ):
            systems.append({"type": "text", "text": message.content})
        elif isinstance(message, ChatMessage):
            output.append({"role": message.role, "content": message.content})
        elif isinstance(message, UserMessage):
            output.append({"role": "user", "content": message.content})
        elif isinstance(message, AssistantMessage):
            if message.continuation_state and message.continuation_state.protocol == "anthropic":
                content = message.continuation_state.payload
            else:
                content: list[dict[str, Any]] = []
                if message.content:
                    content.append({"type": "text", "text": message.content})
                content.extend(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": json.loads(call.arguments_json),
                    }
                    for call in message.tool_calls
                )
            output.append({"role": "assistant", "content": content})
        elif isinstance(message, ToolResultMessage):
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.result.to_json(),
                "is_error": message.result.status != "success",
            }
            if output and output[-1]["role"] == "user" and isinstance(output[-1]["content"], list):
                output[-1]["content"].append(block)
            else:
                output.append({"role": "user", "content": [block]})
    return systems, output


class AnthropicProvider:
    def __init__(self, config: ProviderConfig, client: Any | None = None) -> None:
        self.config = config
        self._client = client or anthropic.AsyncAnthropic(
            api_key=config.api_key.get_secret_value(), base_url=config.base_url
        )

    @property
    def display_name(self) -> str:
        return self.config.name

    @property
    def model_name(self) -> str:
        return self.config.model

    async def stream(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition] = (),
        tool_choice: Literal["auto", "none"] = "auto",
    ) -> AsyncIterator[ProviderEvent]:
        system, serialized = _serialize(messages)
        request: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": 4096,
            "messages": serialized,
        }
        if system:
            request["system"] = system
        if self.config.thinking:
            request["thinking"] = {"type": "enabled", "budget_tokens": 1024}
        if tools:
            request["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": dict(tool.parameters),
                }
                for tool in tools
            ]
            request["tool_choice"] = {"type": tool_choice}
        stop_reason: str | None = None
        state: ProviderContinuationState | None = None
        usage_snapshot: TokenUsage | None = None
        try:
            async with self._client.messages.stream(**request) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", None)
                    if event_type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if getattr(block, "type", None) == "tool_use":
                            initial = getattr(block, "input", None)
                            yield ToolCallDelta(
                                getattr(event, "index", 0),
                                getattr(block, "id", ""),
                                getattr(block, "name", ""),
                                json.dumps(initial, separators=(",", ":")) if initial else "",
                            )
                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        delta_type = getattr(delta, "type", None)
                        if delta_type == "text_delta" and getattr(delta, "text", ""):
                            yield TextDelta(delta.text)
                        elif delta_type == "thinking_delta" and getattr(delta, "thinking", ""):
                            yield ThinkingDelta(delta.thinking)
                        elif delta_type == "input_json_delta":
                            yield ToolCallDelta(
                                getattr(event, "index", 0),
                                arguments_fragment=getattr(delta, "partial_json", ""),
                            )
                    elif event_type == "message_delta":
                        stop_reason = getattr(
                            getattr(event, "delta", None), "stop_reason", stop_reason
                        )
                get_final = getattr(stream, "get_final_message", None)
                if get_final is not None:
                    final = await get_final()
                    blocks = [
                        block.model_dump(mode="json") if hasattr(block, "model_dump") else block
                        for block in getattr(final, "content", ())
                    ]
                    state = ProviderContinuationState("anthropic", blocks)
                    usage = getattr(final, "usage", None)
                    if usage is not None:
                        input_tokens = _optional_nonnegative_int(
                            getattr(usage, "input_tokens", None)
                        )
                        output_tokens = _optional_nonnegative_int(
                            getattr(usage, "output_tokens", None)
                        )
                        usage_snapshot = TokenUsage(
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            total_tokens=(
                                input_tokens + output_tokens
                                if input_tokens is not None and output_tokens is not None
                                else None
                            ),
                            cache_creation_input_tokens=_optional_nonnegative_int(
                                getattr(usage, "cache_creation_input_tokens", None)
                            ),
                            cache_read_input_tokens=_optional_nonnegative_int(
                                getattr(usage, "cache_read_input_tokens", None)
                            ),
                        )
            if usage_snapshot is not None:
                yield UsageReported(usage_snapshot)
            yield StreamCompleted(stop_reason, state)
        except ProviderError:
            raise
        except anthropic.AuthenticationError as exc:
            raise ProviderError(
                ProviderErrorKind.AUTHENTICATION, "Anthropic authentication failed."
            ) from exc
        except anthropic.RateLimitError as exc:
            raise ProviderError(
                ProviderErrorKind.RATE_LIMIT, "Anthropic rate limit reached."
            ) from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, "Cannot connect to Anthropic.") from exc
        except anthropic.APIError as exc:
            raise ProviderError(
                ProviderErrorKind.INVALID_RESPONSE, "Anthropic returned an invalid response."
            ) from exc
