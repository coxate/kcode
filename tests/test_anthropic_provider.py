from types import SimpleNamespace

from kcode.config import ProviderConfig
from kcode.conversation import ChatMessage
from kcode.events import StreamCompleted, TextDelta, ThinkingDelta, ToolCallDelta
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
    block = SimpleNamespace(model_dump=lambda mode: {"type": "thinking", "thinking": "summary", "signature": "sig"})
    messages = FakeMessages(events, SimpleNamespace(content=[block]))
    provider = AnthropicProvider(
        ProviderConfig(name="claude", protocol="anthropic", model="m", base_url="https://test", api_key="x", thinking=True),
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
    assert messages.request["tool_choice"] == {"type": "auto"}
