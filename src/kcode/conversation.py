from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from kcode.tools.base import ToolCall, ToolResult


@dataclass(frozen=True, slots=True)
class ProviderContinuationState:
    protocol: str
    payload: Any


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["user", "assistant", "system"]
    content: str


@dataclass(frozen=True, slots=True)
class SystemMessage:
    content: str


@dataclass(frozen=True, slots=True)
class UserMessage:
    content: str


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    continuation_state: ProviderContinuationState | None = None


@dataclass(frozen=True, slots=True)
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    result: ToolResult


ConversationMessage: TypeAlias = ChatMessage | SystemMessage | UserMessage | AssistantMessage | ToolResultMessage


@dataclass(frozen=True, slots=True)
class ChatTurn:
    user: str
    assistant: str


class Conversation:
    def __init__(self) -> None:
        self._messages: list[ConversationMessage] = []
        self._turns: list[ChatTurn] = []

    def build_request(self, user_text: str) -> tuple[ConversationMessage, ...]:
        return (*self._messages, ChatMessage("user", user_text))

    def commit(self, user_text: str, assistant_text: str) -> None:
        if assistant_text.strip():
            self._messages.extend(
                (ChatMessage("user", user_text), ChatMessage("assistant", assistant_text))
            )
            self._turns.append(ChatTurn(user_text, assistant_text))

    def commit_messages(self, messages: tuple[ConversationMessage, ...]) -> None:
        if not messages:
            return
        final = next(
            (item.content for item in reversed(messages) if isinstance(item, AssistantMessage) and item.content.strip()),
            "",
        )
        user = next((item.content for item in messages if isinstance(item, UserMessage)), "")
        if not final:
            return
        self._messages.extend(messages)
        self._turns.append(ChatTurn(user, final))

    def clear(self) -> None:
        self._messages.clear()
        self._turns.clear()

    def snapshot(self) -> tuple[ChatTurn, ...]:
        return tuple(self._turns)

    def messages_snapshot(self) -> tuple[ConversationMessage, ...]:
        return tuple(self._messages)
