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
class StableSystemMessage:
    content: str


@dataclass(frozen=True, slots=True)
class EnvironmentMessage:
    content: str


@dataclass(frozen=True, slots=True)
class SystemReminderMessage:
    kind: Literal["plan_mode", "approved_plan", "session_resume", "hook", "task"]
    content: str

    def render(self) -> str:
        content = self.content.replace("</system-reminder", "&lt;/system-reminder").replace(
            "<system-reminder", "&lt;system-reminder"
        )
        return (
            "<system-reminder>\n"
            f"{content}\n"
            "Do not respond to this reminder directly.\n"
            "</system-reminder>"
        )


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


ConversationMessage: TypeAlias = (
    ChatMessage
    | SystemMessage
    | StableSystemMessage
    | EnvironmentMessage
    | SystemReminderMessage
    | UserMessage
    | AssistantMessage
    | ToolResultMessage
)


@dataclass(frozen=True, slots=True)
class ChatTurn:
    user: str
    assistant: str


@dataclass(frozen=True, slots=True)
class TurnHandle:
    id: int
    user: str


@dataclass(slots=True)
class _ActiveTurn:
    handle: TurnHandle
    checkpointed: bool = False


class Conversation:
    def __init__(self) -> None:
        self._messages: list[ConversationMessage] = []
        self._turns: list[ChatTurn] = []
        self._active: _ActiveTurn | None = None
        self._next_turn_id = 1

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
            (
                item.content
                for item in reversed(messages)
                if isinstance(item, AssistantMessage) and item.content.strip()
            ),
            "",
        )
        user = next((item.content for item in messages if isinstance(item, UserMessage)), "")
        if not final:
            return
        self._messages.extend(messages)
        self._turns.append(ChatTurn(user, final))

    def begin_turn(self, user_text: str) -> TurnHandle:
        if self._active is not None:
            raise RuntimeError("A conversation turn is already active.")
        handle = TurnHandle(self._next_turn_id, user_text)
        self._next_turn_id += 1
        self._active = _ActiveTurn(handle)
        return handle

    def checkpoint_tool_step(
        self,
        handle: TurnHandle,
        assistant: AssistantMessage,
        results: tuple[ToolResultMessage, ...],
    ) -> None:
        active = self._require_active(handle)
        if not active.checkpointed:
            self._messages.append(UserMessage(handle.user))
            self._messages.append(assistant)
            active.checkpointed = True
        self._messages.extend(results)

    def complete_turn(self, handle: TurnHandle, assistant: AssistantMessage) -> None:
        active = self._require_active(handle)
        if not assistant.content.strip():
            raise ValueError("A completed turn requires assistant text.")
        if not active.checkpointed:
            self._messages.append(UserMessage(handle.user))
        self._messages.append(assistant)
        self._turns.append(ChatTurn(handle.user, assistant.content))
        self._active = None

    def stop_turn(self, handle: TurnHandle) -> None:
        self._require_active(handle)
        self._active = None

    def _require_active(self, handle: TurnHandle) -> _ActiveTurn:
        if self._active is None or self._active.handle != handle:
            raise RuntimeError("Conversation turn is not active.")
        return self._active

    def clear(self) -> None:
        self._messages.clear()
        self._turns.clear()
        self._active = None

    def restore(self, messages: tuple[ConversationMessage, ...]) -> None:
        if self._active is not None:
            raise RuntimeError("Cannot restore over an active conversation turn.")
        allowed = (UserMessage, AssistantMessage, ToolResultMessage)
        if any(not isinstance(message, allowed) for message in messages):
            raise ValueError("Restored history contains a non-canonical message.")

        turns: list[ChatTurn] = []
        current_user: str | None = None
        for message in messages:
            if isinstance(message, UserMessage):
                current_user = message.content
            elif (
                isinstance(message, AssistantMessage)
                and message.content.strip()
                and current_user is not None
            ):
                turns.append(ChatTurn(current_user, message.content))
                current_user = None

        self._messages = list(messages)
        self._turns = turns
        self._active = None
        self._next_turn_id = len(turns) + 1

    def snapshot(self) -> tuple[ChatTurn, ...]:
        return tuple(self._turns)

    def messages_snapshot(self) -> tuple[ConversationMessage, ...]:
        return tuple(self._messages)
