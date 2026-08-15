import json

import pytest

from kcode.conversation import StableSystemMessage, UserMessage
from kcode.events import StreamCompleted, TextDelta
from kcode.memory.extraction import ExtractionError, MemoryExtractor
from kcode.memory.models import CompletedTurn, MemoryScope


class Provider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self, response: str):
        self.response = response
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((messages, tools, tool_choice))
        yield TextDelta(self.response)
        yield StreamCompleted()


async def test_extracts_strict_candidate_without_tool_or_provider_state() -> None:
    response = json.dumps(
        {
            "candidates": [
                {
                    "action": "create",
                    "type": "project_fact",
                    "scope": "project",
                    "target_ids": [],
                    "title": "Use uv",
                    "summary": "This project uses uv.",
                    "application": "Use uv for Python commands.",
                    "body": "",
                    "reason": "Stable convention",
                    "evidence": "这个项目使用 uv",
                }
            ]
        }
    )
    provider = Provider(response)
    turn = CompletedTurn.create("s1", "请记住，这个项目使用 uv", "明白。", "default")
    proposals = await MemoryExtractor(provider).extract(turn, "")
    assert len(proposals) == 1
    assert proposals[0].scope == MemoryScope.PROJECT
    messages, tools, choice = provider.requests[0]
    assert isinstance(messages[0], StableSystemMessage)
    assert isinstance(messages[1], UserMessage)
    assert tools == ()
    assert choice == "none"


async def test_feedback_is_downgraded_to_project_scope() -> None:
    response = json.dumps(
        {
            "candidates": [
                {
                    "action": "create",
                    "type": "feedback",
                    "scope": "user",
                    "title": "Do not rewrite files",
                    "summary": "Avoid broad rewrites.",
                    "application": "Use focused edits.",
                    "reason": "User correction",
                    "evidence": "不要重写整个文件",
                }
            ]
        }
    )
    turn = CompletedTurn.create("s", "不要重写整个文件", "明白", "default")
    proposal = (await MemoryExtractor(Provider(response)).extract(turn, ""))[0]
    assert proposal.scope == MemoryScope.PROJECT


async def test_invalid_json_and_secrets_are_not_saved() -> None:
    turn = CompletedTurn.create("s", "remember this", "ok", "default")
    with pytest.raises(ExtractionError):
        await MemoryExtractor(Provider("prefix {} suffix")).extract(turn, "")

    response = json.dumps(
        {
            "candidates": [
                {
                    "action": "create",
                    "type": "user_preference",
                    "scope": "user",
                    "title": "API token",
                    "summary": "token=abcdefghijklmnop",
                    "application": "Reuse it",
                    "reason": "Remember",
                    "evidence": "token",
                }
            ]
        }
    )
    assert await MemoryExtractor(Provider(response)).extract(turn, "") == ()
