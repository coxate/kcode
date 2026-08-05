from types import SimpleNamespace

from kcode.config import ProviderConfig
from kcode.conversation import (
    ChatMessage,
    EnvironmentMessage,
    StableSystemMessage,
    SystemReminderMessage,
)
from kcode.events import (
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    ToolCallDelta,
    UsageReported,
)
from kcode.providers.anthropic import AnthropicProvider
from kcode.tools.base import ToolDefinition


class FakeStream:
    def __init__(self, events, final=None):
        self.events = events
        self.final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        async def iterate():
            for event in self.events:
                yield event

        return iterate()

    async def get_final_message(self):
        if self.final is None:
            return SimpleNamespace(content=[])
        return self.final


class FakeMessages:
    def __init__(self, events, final=None):
        self.events = events
        self.final = final
        self.request = None

    def stream(self, **request):
        self.request = request
        return FakeStream(self.events, self.final)


async def test_anthropic_maps_thinking_text_and_completion() -> None:
    events = [
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="thinking_delta", thinking="reason"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            delta=SimpleNamespace(type="text_delta", text="answer"),
        ),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="end_turn")),
    ]
    messages = FakeMessages(events)
    client = SimpleNamespace(messages=messages)
    config = ProviderConfig(
        name="claude",
        protocol="anthropic",
        model="claude-test",
        base_url="https://anthropic.test",
        api_key="not-real",
        thinking=True,
    )
    provider = AnthropicProvider(config, client)
    result = [event async for event in provider.stream([ChatMessage("user", "hi")])]
    assert result[-1].stop_reason == "end_turn"
    assert result[:-1] == [ThinkingDelta("reason"), TextDelta("answer")]
    assert messages.request["thinking"] == {"type": "enabled", "budget_tokens": 1024}
    assert messages.request["max_tokens"] == 4096


async def test_anthropic_tool_fragments_and_continuation_state() -> None:
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="tool-1", name="read_file", input={}),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":"README.md"}'),
        ),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="tool_use")),
    ]
    block = SimpleNamespace(
        model_dump=lambda mode: {"type": "thinking", "thinking": "summary", "signature": "sig"}
    )
    redacted = SimpleNamespace(
        model_dump=lambda mode: {"type": "redacted_thinking", "data": "opaque"}
    )
    messages = FakeMessages(events, SimpleNamespace(content=[block, redacted]))
    provider = AnthropicProvider(
        ProviderConfig(
            name="claude",
            protocol="anthropic",
            model="m",
            base_url="https://test",
            api_key="x",
            thinking=True,
        ),
        SimpleNamespace(messages=messages),
    )
    definition = ToolDefinition("read_file", "Read", {"type": "object"})
    result = [event async for event in provider.stream([ChatMessage("user", "read")], [definition])]
    assert result[:2] == [
        ToolCallDelta(1, "tool-1", "read_file", ""),
        ToolCallDelta(1, arguments_fragment='{"path":"README.md"}'),
    ]
    completed = result[-1]
    assert completed.continuation_state.payload[0]["signature"] == "sig"
    assert completed.continuation_state.payload[1] == {
        "type": "redacted_thinking",
        "data": "opaque",
    }
    assert messages.request["tool_choice"] == {"type": "auto"}


async def test_anthropic_reports_final_usage() -> None:
    usage = SimpleNamespace(
        input_tokens=20,
        output_tokens=5,
        cache_creation_input_tokens=3,
        cache_read_input_tokens=7,
    )
    messages = FakeMessages([], SimpleNamespace(content=[], usage=usage))
    provider = AnthropicProvider(
        ProviderConfig(
            name="claude",
            protocol="anthropic",
            model="m",
            base_url="https://test",
            api_key="x",
        ),
        SimpleNamespace(messages=messages),
    )
    result = [event async for event in provider.stream([ChatMessage("user", "hi")])]
    assert result[-2] == UsageReported(TokenUsage(20, 5, 25, 3, 7))


async def test_anthropic_usage_preserves_zero_and_rejects_invalid_values() -> None:
    usage = SimpleNamespace(
        input_tokens=0,
        output_tokens=True,
        cache_creation_input_tokens=-1,
        cache_read_input_tokens="7",
    )
    messages = FakeMessages([], SimpleNamespace(content=[], usage=usage))
    provider = AnthropicProvider(
        ProviderConfig(
            name="claude",
            protocol="anthropic",
            model="m",
            base_url="https://test",
            api_key="x",
        ),
        SimpleNamespace(messages=messages),
    )
    result = [event async for event in provider.stream([ChatMessage("user", "hi")])]
    assert UsageReported(TokenUsage(0, None, None, None, None)) in result


async def test_anthropic_streams_multiple_tool_uses() -> None:
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="tool_use", id="a", name="read_file", input={}),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="b", name="find_files", input={}),
        ),
        SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason="tool_use")),
    ]
    messages = FakeMessages(events, SimpleNamespace(content=[]))
    provider = AnthropicProvider(
        ProviderConfig(
            name="claude", protocol="anthropic", model="m", base_url="https://test", api_key="x"
        ),
        SimpleNamespace(messages=messages),
    )
    result = [event async for event in provider.stream([ChatMessage("user", "tools")])]
    calls = [event for event in result if isinstance(event, ToolCallDelta)]
    assert [(call.index, call.id_fragment, call.name_fragment) for call in calls] == [
        (0, "a", "read_file"),
        (1, "b", "find_files"),
    ]


async def test_anthropic_preserves_system_blocks_and_marks_only_stable() -> None:
    messages = FakeMessages([])
    provider = AnthropicProvider(
        ProviderConfig(
            name="claude",
            protocol="anthropic",
            model="m",
            base_url="https://test",
            api_key="x",
        ),
        SimpleNamespace(messages=messages),
    )
    request_messages = [
        StableSystemMessage("stable"),
        EnvironmentMessage("dynamic"),
        SystemReminderMessage("plan_mode", "remember"),
        ChatMessage("user", "hi"),
    ]
    _ = [event async for event in provider.stream(request_messages)]
    system = messages.request["system"]
    assert system[0] == {
        "type": "text",
        "text": "stable",
        "cache_control": {"type": "ephemeral"},
    }
    assert system[1] == {"type": "text", "text": "dynamic"}
    assert "<system-reminder>" in system[2]["text"]
    assert "cache_control" not in system[2]
