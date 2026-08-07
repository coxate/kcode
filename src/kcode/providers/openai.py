from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlparse

import openai

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
from kcode.errors import ProviderError, ProviderErrorKind, is_prompt_too_long_error
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


class OpenAICacheMode(StrEnum):
    EXPLICIT = "explicit"
    AUTOMATIC = "automatic"


OPENAI_EXPLICIT_CACHE_MODEL_PREFIXES = ("gpt-5.6",)


def detect_cache_mode(config: ProviderConfig) -> OpenAICacheMode:
    hostname = (urlparse(config.base_url).hostname or "").lower()
    if hostname == "api.openai.com" and config.model.lower().startswith(
        OPENAI_EXPLICIT_CACHE_MODEL_PREFIXES
    ):
        return OpenAICacheMode.EXPLICIT
    return OpenAICacheMode.AUTOMATIC


def build_prompt_cache_key(
    model: str,
    stable_prompt: str,
    tools: Sequence[ToolDefinition],
) -> str:
    canonical_tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
        }
        for tool in tools
    ]
    payload = json.dumps(
        {
            "version": "v1",
            "model": model,
            "stable_prompt": stable_prompt,
            "tools": canonical_tools,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"kcode:v1:{hashlib.sha256(payload).hexdigest()[:32]}"


def _optional_nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _message(
    message: ConversationMessage,
    *,
    include_reasoning: bool = False,
    cache_mode: OpenAICacheMode = OpenAICacheMode.AUTOMATIC,
) -> dict[str, Any]:
    if isinstance(message, ChatMessage):
        return {"role": message.role, "content": message.content}
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, StableSystemMessage):
        if cache_mode == OpenAICacheMode.EXPLICIT:
            return {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": message.content,
                        "prompt_cache_breakpoint": {"mode": "explicit"},
                    }
                ],
            }
        return {"role": "system", "content": message.content}
    if isinstance(message, EnvironmentMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, SystemReminderMessage):
        return {"role": "system", "content": message.render()}
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AssistantMessage):
        value: dict[str, Any] = {"role": "assistant", "content": message.content or None}
        if (
            include_reasoning
            and message.continuation_state
            and message.continuation_state.protocol == "deepseek"
        ):
            value["reasoning_content"] = message.continuation_state.payload
        if message.tool_calls:
            value["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments_json},
                }
                for call in message.tool_calls
            ]
        return value
    if isinstance(message, ToolResultMessage):
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.result.to_json(),
        }
    raise TypeError(f"Unsupported message: {type(message)!r}")


class OpenAIProvider:
    def __init__(self, config: ProviderConfig, client: Any | None = None) -> None:
        self.config = config
        self._client = client or openai.AsyncOpenAI(
            api_key=config.api_key.get_secret_value(), base_url=config.base_url
        )
        self._is_deepseek = (
            "deepseek" in config.name.lower() or "deepseek" in config.base_url.lower()
        )
        self._cache_mode = detect_cache_mode(config)

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
        response: Any = None
        stop_reason: str | None = None
        reasoning = ""
        saw_tool_call = False
        usage_snapshot: TokenUsage | None = None
        try:
            request: dict[str, Any] = {
                "model": self.config.model,
                "messages": [
                    _message(
                        message,
                        include_reasoning=self._is_deepseek,
                        cache_mode=self._cache_mode,
                    )
                    for message in messages
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            stable_prompt = next(
                (
                    message.content
                    for message in messages
                    if isinstance(message, StableSystemMessage)
                ),
                None,
            )
            if self._cache_mode == OpenAICacheMode.EXPLICIT and stable_prompt is not None:
                request["prompt_cache_key"] = build_prompt_cache_key(
                    self.config.model, stable_prompt, tools
                )
                request["prompt_cache_options"] = {"mode": "explicit"}
            if tools:
                request["tools"] = [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": dict(tool.parameters),
                        },
                    }
                    for tool in tools
                ]
                request["tool_choice"] = tool_choice
                request["parallel_tool_calls"] = True
            response = await self._client.chat.completions.create(**request)
            async for chunk in response:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    details = getattr(usage, "prompt_tokens_details", None)
                    if self._is_deepseek:
                        cache_creation = None
                        cache_read = _optional_nonnegative_int(
                            getattr(usage, "prompt_cache_hit_tokens", None)
                        )
                    else:
                        cache_creation = _optional_nonnegative_int(
                            getattr(details, "cache_write_tokens", None)
                            if details is not None
                            else None
                        )
                        cache_read = _optional_nonnegative_int(
                            getattr(details, "cached_tokens", None) if details is not None else None
                        )
                        if cache_read is None:
                            cache_read = _optional_nonnegative_int(
                                getattr(usage, "prompt_cache_hit_tokens", None)
                            )
                    usage_snapshot = TokenUsage(
                        input_tokens=_optional_nonnegative_int(
                            getattr(usage, "prompt_tokens", None)
                        ),
                        output_tokens=_optional_nonnegative_int(
                            getattr(usage, "completion_tokens", None)
                        ),
                        total_tokens=_optional_nonnegative_int(
                            getattr(usage, "total_tokens", None)
                        ),
                        cache_creation_input_tokens=cache_creation,
                        cache_read_input_tokens=cache_read,
                    )
                choices = getattr(chunk, "choices", ())
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None)
                if content:
                    yield TextDelta(content)
                reasoning_content = getattr(delta, "reasoning_content", None)
                if reasoning_content:
                    reasoning += reasoning_content
                    yield ThinkingDelta(reasoning_content)
                for call in getattr(delta, "tool_calls", None) or ():
                    saw_tool_call = True
                    function = getattr(call, "function", None)
                    yield ToolCallDelta(
                        index=getattr(call, "index", 0),
                        id_fragment=getattr(call, "id", None) or "",
                        name_fragment=getattr(function, "name", None) or "",
                        arguments_fragment=getattr(function, "arguments", None) or "",
                    )
                stop_reason = getattr(choice, "finish_reason", None) or stop_reason
            if usage_snapshot is not None:
                yield UsageReported(usage_snapshot)
            state = (
                ProviderContinuationState("deepseek", reasoning)
                if self._is_deepseek and reasoning and saw_tool_call
                else None
            )
            yield StreamCompleted(stop_reason, state)
        except ProviderError:
            raise
        except openai.AuthenticationError as exc:
            raise ProviderError(
                ProviderErrorKind.AUTHENTICATION, "OpenAI-compatible authentication failed."
            ) from exc
        except openai.RateLimitError as exc:
            raise ProviderError(
                ProviderErrorKind.RATE_LIMIT, "OpenAI-compatible rate limit reached."
            ) from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise ProviderError(
                ProviderErrorKind.NETWORK, "Cannot connect to the OpenAI-compatible endpoint."
            ) from exc
        except openai.BadRequestError as exc:
            if is_prompt_too_long_error(exc):
                raise ProviderError(
                    ProviderErrorKind.PROMPT_TOO_LONG,
                    "The request exceeds the model context window.",
                ) from exc
            raise ProviderError(
                ProviderErrorKind.INVALID_RESPONSE, "The endpoint rejected the request."
            ) from exc
        except openai.APIError as exc:
            raise ProviderError(
                ProviderErrorKind.INVALID_RESPONSE, "The endpoint returned an invalid response."
            ) from exc
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
