from types import SimpleNamespace

from kcode.config import ProviderConfig
from kcode.conversation import AssistantMessage, ChatMessage, ProviderContinuationState
from kcode.events import StreamCompleted, TextDelta, ThinkingDelta, TokenUsage, ToolCallDelta, UsageReported
from kcode.tools.base import ToolCall
from kcode.providers.factory import create_provider
from kcode.providers.openai import OpenAIProvider
from kcode.tools.base import ToolDefinition


class FakeResponse:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __aiter__(self):
        async def iterate():
            for chunk in self.chunks:
                yield chunk

        return iterate()

    async def close(self):
        self.closed = True


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.request = None

    async def create(self, **request):
        self.request = request
        return self.response


async def test_openai_compatible_stream_and_cleanup() -> None:
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="one"), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=" two"), finish_reason="stop")]
        ),
    ]
    response = FakeResponse(chunks)
    completions = FakeCompletions(response)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    config = ProviderConfig(
        name="deepseek",
        protocol="openai",
        model="deepseek-chat",
        base_url="https://deepseek.test",
        api_key="not-real",
    )
    provider = OpenAIProvider(config, client)
    result = [event async for event in provider.stream([ChatMessage("user", "hi")])]
    assert result == [TextDelta("one"), TextDelta(" two"), StreamCompleted("stop")]
    assert completions.request["stream"] is True
    assert response.closed is True


def test_factory_warns_when_thinking_is_ignored(monkeypatch) -> None:
    config = ProviderConfig(
        name="openai",
        protocol="openai",
        model="m",
        base_url="https://openai.test",
        api_key="not-real",
        thinking=True,
    )
    monkeypatch.setattr("kcode.providers.factory.OpenAIProvider", lambda value: object())
    _, warnings = create_provider(config)
    assert warnings and "ignored" in warnings[0]


async def test_openai_streams_fragmented_tool_call() -> None:
    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                    index=0, id="call-1", function=SimpleNamespace(name="read_file", arguments='{"pa')
                )]),
                finish_reason=None,
            )]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
                    index=0, id=None, function=SimpleNamespace(name=None, arguments='th":"README.md"}')
                )]),
                finish_reason="tool_calls",
            )]
        ),
    ]
    response = FakeResponse(chunks)
    completions = FakeCompletions(response)
    provider = OpenAIProvider(
        ProviderConfig(name="deepseek", protocol="openai", model="m", base_url="https://test", api_key="x"),
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    definition = ToolDefinition("read_file", "Read", {"type": "object"})
    events = [event async for event in provider.stream([ChatMessage("user", "read")], [definition])]
    assert events == [
        ToolCallDelta(0, "call-1", "read_file", '{"pa'),
        ToolCallDelta(0, "", "", 'th":"README.md"}'),
        StreamCompleted("tool_calls"),
    ]
    assert completions.request["parallel_tool_calls"] is True
    assert completions.request["tool_choice"] == "auto"


async def test_deepseek_usage_reasoning_and_continuation_round_trip() -> None:
    usage = SimpleNamespace(
        prompt_tokens=10,
        completion_tokens=4,
        total_tokens=14,
        prompt_cache_hit_tokens=6,
    )
    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    reasoning_content="先读取",
                    tool_calls=[SimpleNamespace(
                        index=0,
                        id="call-1",
                        function=SimpleNamespace(name="read_file", arguments='{"path":"README.md"}'),
                    )],
                ),
                finish_reason="tool_calls",
            )],
        ),
        SimpleNamespace(usage=usage, choices=[]),
    ]
    completions = FakeCompletions(FakeResponse(chunks))
    provider = OpenAIProvider(
        ProviderConfig(
            name="deepseek",
            protocol="openai",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="x",
        ),
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    events = [event async for event in provider.stream([ChatMessage("user", "read")])]
    assert ThinkingDelta("先读取") in events
    assert UsageReported(TokenUsage(10, 4, 14, None, 6)) in events
    completed = events[-1]
    assert completed.continuation_state == ProviderContinuationState("deepseek", "先读取")

    message = AssistantMessage(
        "",
        (ToolCall(0, "call-1", "read_file", '{"path":"README.md"}'),),
        completed.continuation_state,
    )
    completions.response = FakeResponse(
        [SimpleNamespace(usage=None, choices=[SimpleNamespace(
            delta=SimpleNamespace(content="done", reasoning_content=None, tool_calls=[]),
            finish_reason="stop",
        )])]
    )
    _ = [event async for event in provider.stream([message])]
    assert completions.request["messages"][0]["reasoning_content"] == "先读取"
    assert completions.request["stream_options"] == {"include_usage": True}


async def test_openai_streams_multiple_tool_calls_by_index() -> None:
    chunks = [
        SimpleNamespace(
            usage=None,
            choices=[SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    reasoning_content=None,
                    tool_calls=[
                        SimpleNamespace(index=0, id="a", function=SimpleNamespace(name="read_file", arguments="{}")),
                        SimpleNamespace(index=1, id="b", function=SimpleNamespace(name="find_files", arguments="{}")),
                    ],
                ),
                finish_reason="tool_calls",
            )],
        )
    ]
    completions = FakeCompletions(FakeResponse(chunks))
    provider = OpenAIProvider(
        ProviderConfig(name="openai", protocol="openai", model="m", base_url="https://test", api_key="x"),
        SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    events = [event async for event in provider.stream([ChatMessage("user", "tools")])]
    calls = [event for event in events if isinstance(event, ToolCallDelta)]
    assert [(call.index, call.id_fragment, call.name_fragment) for call in calls] == [
        (0, "a", "read_file"),
        (1, "b", "find_files"),
    ]
