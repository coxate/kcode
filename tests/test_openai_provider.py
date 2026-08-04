from types import SimpleNamespace

from kcode.config import ProviderConfig
from kcode.conversation import ChatMessage
from kcode.events import StreamCompleted, TextDelta, ToolCallDelta
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
    assert completions.request["parallel_tool_calls"] is False
    assert completions.request["tool_choice"] == "auto"
