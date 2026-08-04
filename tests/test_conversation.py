from kcode.conversation import ChatMessage, Conversation


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
