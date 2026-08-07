from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from kcode.context.models import ContextBudget, NormalizedUsage, TokenConfidence
from kcode.conversation import (
    AssistantMessage,
    ChatMessage,
    ConversationMessage,
    EnvironmentMessage,
    StableSystemMessage,
    SystemMessage,
    SystemReminderMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.events import TokenUsage
from kcode.tools.base import ToolDefinition

CHARACTERS_PER_TOKEN = 3.5
DEFAULT_CONTEXT_WINDOW = 64_000


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _nonnegative_int(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def normalize_usage(raw: Any, provider: str = "generic") -> NormalizedUsage:
    if isinstance(raw, TokenUsage):
        input_tokens = _nonnegative_int(raw.input_tokens)
        output_tokens = _nonnegative_int(raw.output_tokens)
        cache_write = _nonnegative_int(raw.cache_creation_input_tokens)
        cache_read = _nonnegative_int(raw.cache_read_input_tokens)
    else:
        input_tokens = _nonnegative_int(
            _field(raw, "input_tokens")
            if _field(raw, "input_tokens") is not None
            else _field(raw, "prompt_tokens")
        )
        output_tokens = _nonnegative_int(
            _field(raw, "output_tokens")
            if _field(raw, "output_tokens") is not None
            else _field(raw, "completion_tokens")
        )
        cache_write = _nonnegative_int(
            _field(raw, "cache_creation_input_tokens")
            if _field(raw, "cache_creation_input_tokens") is not None
            else _field(raw, "cache_write_tokens")
        )
        cache_read = _nonnegative_int(
            _field(raw, "cache_read_input_tokens")
            if _field(raw, "cache_read_input_tokens") is not None
            else (
                _field(raw, "cached_tokens")
                if _field(raw, "cached_tokens") is not None
                else _field(raw, "prompt_cache_hit_tokens")
            )
        )

    context_input = input_tokens
    confidence: TokenConfidence = "high" if context_input is not None else "low"
    return NormalizedUsage(
        context_input_tokens=context_input,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        is_exact=context_input is not None,
        confidence=confidence,
        source=provider,
    )


def normalize_anthropic_usage(raw: Any) -> NormalizedUsage:
    return normalize_usage(raw, "anthropic")


def normalize_openai_usage(raw: Any) -> NormalizedUsage:
    return normalize_usage(raw, "openai")


def normalize_deepseek_usage(raw: Any) -> NormalizedUsage:
    return normalize_usage(raw, "deepseek")


def message_character_count(message: ConversationMessage) -> int:
    if isinstance(
        message,
        (
            ChatMessage,
            SystemMessage,
            StableSystemMessage,
            EnvironmentMessage,
            UserMessage,
            AssistantMessage,
        ),
    ):
        count = len(message.content)
        if isinstance(message, AssistantMessage):
            count += sum(
                len(call.id) + len(call.name) + len(call.arguments_json)
                for call in message.tool_calls
            )
        return count
    if isinstance(message, SystemReminderMessage):
        return len(message.render())
    if isinstance(message, ToolResultMessage):
        return len(message.tool_call_id) + len(message.tool_name) + len(message.result.to_json())
    return len(repr(message))


def tools_character_count(tools: Sequence[ToolDefinition]) -> int:
    if not tools:
        return 0
    payload = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.parameters),
        }
        for tool in tools
    ]
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def estimate_tokens_from_characters(characters: int) -> int:
    if characters <= 0:
        return 0
    return math.ceil(characters / CHARACTERS_PER_TOKEN)


def resolve_context_window(
    *,
    explicit: int | None = None,
    model: str | None = None,
    model_metadata: Mapping[str, int] | None = None,
    provider_default: int = DEFAULT_CONTEXT_WINDOW,
) -> tuple[int, TokenConfidence]:
    if explicit is not None:
        if type(explicit) is not int or explicit <= 0:
            raise ValueError("context_window must be a positive integer")
        return explicit, "high"
    if model is not None and model_metadata is not None:
        value = model_metadata.get(model)
        if type(value) is int and value > 0:
            return value, "medium"
    if type(provider_default) is not int or provider_default <= 0:
        raise ValueError("provider_default must be a positive integer")
    return provider_default, "low"


class UsageEstimator:
    def __init__(self) -> None:
        self._anchor_tokens: int | None = None
        self._anchor_characters = 0
        self._anchor_messages: tuple[ConversationMessage, ...] = ()
        self._anchor_tools: tuple[ToolDefinition, ...] = ()
        self._last_usage: NormalizedUsage | None = None

    @property
    def last_usage(self) -> NormalizedUsage | None:
        return self._last_usage

    def record(
        self,
        usage: NormalizedUsage,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> None:
        self._last_usage = usage
        if usage.context_input_tokens is None:
            return
        self._anchor_tokens = usage.context_input_tokens
        self._anchor_characters = self._characters(messages, tools)
        self._anchor_messages = tuple(messages)
        self._anchor_tools = tuple(tools)

    def estimate(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition] = (),
    ) -> NormalizedUsage:
        characters = self._characters(messages, tools)
        anchor_matches = (
            self._anchor_tokens is not None
            and tuple(tools) == self._anchor_tools
            and len(messages) >= len(self._anchor_messages)
            and tuple(messages[: len(self._anchor_messages)]) == self._anchor_messages
        )
        if not anchor_matches:
            estimate = estimate_tokens_from_characters(characters)
            return NormalizedUsage(
                estimated_input_tokens=estimate,
                is_exact=False,
                confidence="low",
                source="character_estimate",
            )
        delta = max(0, characters - self._anchor_characters)
        estimate = self._anchor_tokens + estimate_tokens_from_characters(delta)
        confidence: TokenConfidence = "high" if delta == 0 else "medium"
        base = self._last_usage or NormalizedUsage()
        return replace(
            base,
            estimated_input_tokens=estimate,
            is_exact=delta == 0 and base.is_exact,
            confidence=confidence,
            source="usage_anchor" if delta == 0 else "usage_anchor+character_estimate",
        )

    def budget(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition] = (),
        *,
        context_window: int = DEFAULT_CONTEXT_WINDOW,
        summary_output_reserve: int = 20_000,
        automatic_safety_margin: int = 13_000,
        manual_safety_margin: int = 3_000,
    ) -> ContextBudget:
        usage = self.estimate(messages, tools)
        return ContextBudget(
            context_window=context_window,
            estimated_input_tokens=usage.effective_input_tokens or 0,
            summary_output_reserve=summary_output_reserve,
            automatic_safety_margin=automatic_safety_margin,
            manual_safety_margin=manual_safety_margin,
            confidence=usage.confidence,
        )

    def clear(self) -> None:
        self._anchor_tokens = None
        self._anchor_characters = 0
        self._anchor_messages = ()
        self._anchor_tools = ()
        self._last_usage = None

    @staticmethod
    def _characters(
        messages: Sequence[ConversationMessage],
        tools: Sequence[ToolDefinition],
    ) -> int:
        message_characters = sum(message_character_count(message) for message in messages)
        return message_characters + tools_character_count(tools)
