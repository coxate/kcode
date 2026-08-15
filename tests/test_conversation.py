import pytest

from kcode.conversation import (
    AssistantMessage,
    ChatMessage,
    ChatTurn,
    Conversation,
    StableSystemMessage,
    ToolResultMessage,
    UserMessage,
)
from kcode.tools.base import ToolCall, ToolResult


def test_restore_rebuilds_turns_and_rejects_active_or_noncanonical_history() -> None:
    conversation = Conversation()
    messages = (
        UserMessage("question"),
        AssistantMessage("", (ToolCall(0, "call", "read_file", "{}"),)),
        ToolResultMessage("call", "read_file", ToolResult.success({"content": "x"})),
        AssistantMessage("answer"),
    )
    conversation.restore(messages)
    assert conversation.messages_snapshot() == messages
    assert conversation.snapshot() == (ChatTurn("question", "answer"),)

    handle = conversation.begin_turn("next")
    with pytest.raises(RuntimeError):
        conversation.restore(messages)
    conversation.stop_turn(handle)
    with pytest.raises(ValueError):
        conversation.restore((StableSystemMessage("system"),))


def test_only_committed_turns_appear_in_request() -> None:
    conversation = Conversation()
    assert conversation.build_request("first") == (ChatMessage("user", "first"),)
    conversation.commit("first", "answer")
    assert conversation.build_request("second") == (
        ChatMessage("user", "first"),
        ChatMessage("assistant", "answer"),
        ChatMessage("user", "second"),
    )
    conversation.commit("ignored", "   ")
    assert len(conversation.snapshot()) == 1


def test_clear_removes_history() -> None:
    conversation = Conversation()
    conversation.commit("hello", "world")
    conversation.clear()
    assert conversation.snapshot() == ()


def test_tool_checkpoints_survive_stopped_turn_without_duplicate_user() -> None:
    conversation = Conversation()
    handle = conversation.begin_turn("完成任务")
    call = ToolCall(0, "call-1", "read_file", '{"path":"README.md"}')
    assistant = AssistantMessage("", (call,))
    result = ToolResultMessage("call-1", "read_file", ToolResult.success({"content": "ok"}))
    conversation.checkpoint_tool_step(handle, assistant, (result,))
    conversation.stop_turn(handle)

    assert conversation.messages_snapshot() == (UserMessage("完成任务"), assistant, result)
    assert conversation.snapshot() == ()


def test_completed_checkpoint_turn_records_one_chat_turn() -> None:
    conversation = Conversation()
    handle = conversation.begin_turn("完成任务")
    call = ToolCall(0, "call-1", "read_file", "{}")
    conversation.checkpoint_tool_step(
        handle,
        AssistantMessage("", (call,)),
        (ToolResultMessage("call-1", "read_file", ToolResult.success({})),),
    )
    conversation.complete_turn(handle, AssistantMessage("完成了"))

    assert conversation.snapshot()[0].assistant == "完成了"
    assert sum(isinstance(item, UserMessage) for item in conversation.messages_snapshot()) == 1
