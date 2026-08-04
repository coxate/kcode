from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

import openai

from kcode.config import ProviderConfig
from kcode.conversation import (
    AssistantMessage,
    ChatMessage,
    ConversationMessage,
    SystemMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.errors import ProviderError, ProviderErrorKind
from kcode.events import ProviderEvent, StreamCompleted, TextDelta, ToolCallDelta
from kcode.tools.base import ToolDefinition


def _message(message: ConversationMessage) -> dict[str, Any]:
    if isinstance(message, ChatMessage):
        return {"role": message.role, "content": message.content}
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, UserMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AssistantMessage):
        value: dict[str, Any] = {"role": "assistant", "content": message.content or None}
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
        try:
            request: dict[str, Any] = {
                "model": self.config.model,
                "messages": [_message(message) for message in messages],
                "stream": True,
            }
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
                request["parallel_tool_calls"] = False
            response = await self._client.chat.completions.create(**request)
            async for chunk in response:
                choices = getattr(chunk, "choices", ())
                if not choices:
                    continue
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                content = getattr(delta, "content", None)
                if content:
                    yield TextDelta(content)
                for call in getattr(delta, "tool_calls", None) or ():
                    function = getattr(call, "function", None)
                    yield ToolCallDelta(
                        index=getattr(call, "index", 0),
                        id_fragment=getattr(call, "id", None) or "",
                        name_fragment=getattr(function, "name", None) or "",
                        arguments_fragment=getattr(function, "arguments", None) or "",
                    )
                stop_reason = getattr(choice, "finish_reason", None) or stop_reason
            yield StreamCompleted(stop_reason)
        except ProviderError:
            raise
        except openai.AuthenticationError as exc:
            raise ProviderError(ProviderErrorKind.AUTHENTICATION, "OpenAI-compatible authentication failed.") from exc
        except openai.RateLimitError as exc:
            raise ProviderError(ProviderErrorKind.RATE_LIMIT, "OpenAI-compatible rate limit reached.") from exc
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise ProviderError(ProviderErrorKind.NETWORK, "Cannot connect to the OpenAI-compatible endpoint.") from exc
        except openai.APIError as exc:
            raise ProviderError(ProviderErrorKind.INVALID_RESPONSE, "The endpoint returned an invalid response.") from exc
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result
