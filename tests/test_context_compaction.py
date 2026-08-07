import json

from kcode.context import (
    CompactionEngine,
    estimate_tokens_from_characters,
    message_character_count,
    message_groups,
    select_recent_messages,
)
from kcode.conversation import AssistantMessage, ToolResultMessage, UserMessage
from kcode.events import StreamCompleted, TextDelta, ToolCallDelta
from kcode.tools.base import ToolCall, ToolResult


def summary_payload(**updates):
    value = {
        "goal": "finish task",
        "confirmed_facts": ["file exists"],
        "inferences": ["bug may be in parser"],
        "unknowns": ["test status"],
        "decisions": ["keep canonical history"],
        "files": ["src/app.py"],
        "errors": [],
        "current_state": "implementing",
        "pending_tasks": ["run tests"],
        "next_steps": ["read artifact"],
        "artifact_references": [".kcode/sessions/s/tool-results/call-1"],
        "history_incomplete": False,
    }
    value.update(updates)
    return json.dumps(value)


class SummaryProvider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self, events):
        self.events = events
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((tuple(messages), tuple(tools), tool_choice))
        for event in self.events:
            yield event


async def test_compaction_request_has_no_tools_and_parses_sections() -> None:
    provider = SummaryProvider([TextDelta(summary_payload()), StreamCompleted("stop")])
    result = await CompactionEngine(provider).compact((UserMessage("do it"),))

    assert result.success is True
    assert result.summary is not None
    assert result.summary.confirmed_facts == ("file exists",)
    assert result.summary.inferences == ("bug may be in parser",)
    assert provider.requests[0][1:] == ((), "none")


async def test_compaction_rejects_tool_calls() -> None:
    provider = SummaryProvider([ToolCallDelta(0, "id", "read_file", "{}")])
    result = await CompactionEngine(provider).compact((UserMessage("do it"),))

    assert result.success is False
    assert "tool call" in (result.failure_reason or "")


async def test_prompt_too_long_retry_marks_history_incomplete() -> None:
    class RetryProvider(SummaryProvider):
        async def stream(self, messages, tools=(), tool_choice="auto"):
            self.requests.append((tuple(messages), tuple(tools), tool_choice))
            if len(self.requests) == 1:
                from kcode.errors import ProviderError, ProviderErrorKind

                raise ProviderError(ProviderErrorKind.PROMPT_TOO_LONG, "prompt too long")
            yield TextDelta(summary_payload(history_incomplete=False))
            yield StreamCompleted("stop")

    provider = RetryProvider([])
    result = await CompactionEngine(provider).compact((UserMessage("old"), UserMessage("new")))

    assert result.success is True
    assert result.history_incomplete is True
    assert result.dropped_messages == 1
    assert result.retry_count == 1


def test_recent_selection_never_splits_tool_pair() -> None:
    call = ToolCall(0, "call-1", "read_file", "{}")
    messages = (
        UserMessage("old"),
        AssistantMessage("", (call,)),
        ToolResultMessage(call.id, call.name, ToolResult.success({"content": "x" * 100})),
        UserMessage("new"),
    )

    groups = message_groups(messages)
    start, recent = select_recent_messages(messages, minimum_tokens=1, minimum_messages=2)

    assert groups[0] == (0, 3)
    assert start != 2
    assert recent == messages[start:]


def test_recent_selection_meets_token_and_message_minimums() -> None:
    messages = tuple(UserMessage(str(index) + "x" * 3_499) for index in range(12))

    start, recent = select_recent_messages(messages)
    token_estimate = estimate_tokens_from_characters(
        sum(message_character_count(message) for message in recent)
    )

    assert start == 2
    assert len(recent) == 10
    assert token_estimate >= 10_000
