from kcode.conversation import Conversation
from kcode.events import StreamCompleted, TextDelta, ToolCallDelta, ToolFinished
from kcode.orchestration import TurnRunner
from kcode.tools.base import ToolCall, ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.policy import ToolPolicy
from kcode.tools.registry import create_default_registry


class FakeToolProvider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((tuple(messages), tuple(tools), tool_choice))
        for event in self.responses[len(self.requests) - 1]:
            yield event


async def allow(_request):
    return True


async def test_single_tool_uses_two_model_requests_and_commits(tmp_path) -> None:
    target = tmp_path / "hello.txt"
    target.write_text("hello", encoding="utf-8")
    provider = FakeToolProvider(
        [
            [
                ToolCallDelta(0, "call-1", "read_file", '{"path":"'),
                ToolCallDelta(0, arguments_fragment=str(target) + '"}'),
                StreamCompleted("tool_calls"),
            ],
            [TextDelta("文件内容是 hello"), StreamCompleted("stop")],
        ]
    )
    registry = create_default_registry()
    conversation = Conversation()
    runner = TurnRunner(
        provider,
        conversation,
        registry,
        ToolExecutor(registry, ToolPolicy(tmp_path)),
        ToolContext(tmp_path),
        allow,
    )
    events = [event async for event in runner.run("读取文件")]
    assert len(provider.requests) == 2
    assert provider.requests[0][2] == "auto"
    assert provider.requests[1][2] == "auto"
    assert any(isinstance(event, ToolFinished) and event.result.status == "success" for event in events)
    assert conversation.snapshot()[0].assistant == "文件内容是 hello"
