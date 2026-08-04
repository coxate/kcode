import asyncio

from kcode.config import AgentConfig
from kcode.conversation import Conversation, SystemMessage, ToolResultMessage
from kcode.errors import ProviderError, ProviderErrorKind
from kcode.events import (
    AgentStopped,
    AgentStopReason,
    ApprovalPending,
    StreamCompleted,
    TextDelta,
    TokenUsage,
    TokenUsageUpdated,
    ToolCallDelta,
    ToolFinished,
    UsageReported,
)
from kcode.orchestration import AgentRunner
from kcode.session import AgentMode, AgentSession
from kcode.tools.base import ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.policy import ToolPolicy
from kcode.tools.registry import create_default_registry


async def allow(_request):
    return True


class ScriptedProvider:
    display_name = "scripted"
    model_name = "scripted-model"

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((tuple(messages), tuple(tools), tool_choice))
        response = self.responses[len(self.requests) - 1]
        if isinstance(response, Exception):
            raise response
        for event in response:
            yield event


def make_runner(tmp_path, provider, *, config=None, conversation=None):
    registry = create_default_registry()
    return AgentRunner(
        provider,
        conversation or Conversation(),
        registry,
        ToolExecutor(registry, ToolPolicy(tmp_path)),
        ToolContext(tmp_path),
        allow,
        config,
    )


async def test_agent_loops_three_tools_then_completes(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    provider = ScriptedProvider(
        [
            [ToolCallDelta(0, "read-1", "read_file", '{"path":"note.txt"}'), StreamCompleted("tool_calls")],
            [ToolCallDelta(0, "edit-1", "edit_file", '{"path":"note.txt","old_text":"before","new_text":"after"}'), StreamCompleted("tool_calls")],
            [ToolCallDelta(0, "read-2", "read_file", '{"path":"note.txt"}'), StreamCompleted("tool_calls")],
            [
                TextDelta("任务完成"),
                UsageReported(TokenUsage(10, 2, 12)),
                StreamCompleted("stop"),
            ],
        ]
    )
    conversation = Conversation()
    runner = make_runner(tmp_path, provider, conversation=conversation)
    events = [event async for event in runner.run("修改并验证")]

    assert len(provider.requests) == 4
    assert all(request[2] == "auto" for request in provider.requests)
    assert target.read_text(encoding="utf-8") == "after"
    assert sum(isinstance(event, ToolFinished) for event in events) == 3
    assert events[-1] == AgentStopped(AgentStopReason.COMPLETED, 4)
    assert conversation.snapshot()[0].assistant == "任务完成"
    assert any(isinstance(event, TokenUsageUpdated) for event in events)


async def test_iteration_limit_stops_after_last_tool_checkpoint(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("value", encoding="utf-8")
    tool_response = [
        ToolCallDelta(0, "read", "read_file", '{"path":"note.txt"}'),
        StreamCompleted("tool_calls"),
    ]
    provider = ScriptedProvider([tool_response, tool_response])
    conversation = Conversation()
    runner = make_runner(
        tmp_path,
        provider,
        config=AgentConfig(max_iterations=2, max_parallel_tools=2),
        conversation=conversation,
    )
    events = [event async for event in runner.run("一直读取")]

    assert len(provider.requests) == 2
    assert events[-1].reason == AgentStopReason.ITERATION_LIMIT
    assert sum(isinstance(item, ToolResultMessage) for item in conversation.messages_snapshot()) == 2


async def test_two_unknown_only_rounds_stop_the_loop(tmp_path) -> None:
    response = [ToolCallDelta(0, "x", "missing_tool", "{}"), StreamCompleted("tool_calls")]
    provider = ScriptedProvider([response, response])
    runner = make_runner(tmp_path, provider)
    events = [event async for event in runner.run("未知工具")]
    assert len(provider.requests) == 2
    assert events[-1].reason == AgentStopReason.UNKNOWN_TOOL_LIMIT


async def test_empty_response_stops_as_invalid(tmp_path) -> None:
    provider = ScriptedProvider([[StreamCompleted("stop")]])
    runner = make_runner(tmp_path, provider)
    events = [event async for event in runner.run("空响应")]
    assert events[-1].reason == AgentStopReason.INVALID_RESPONSE
    assert len(provider.requests) == 1


async def test_bad_tool_arguments_are_returned_and_model_can_recover(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            [ToolCallDelta(0, "bad", "read_file", "{"), StreamCompleted("tool_calls")],
            [TextDelta("参数错误后已调整"), StreamCompleted("stop")],
        ]
    )
    runner = make_runner(tmp_path, provider)
    events = [event async for event in runner.run("恢复")]
    result = next(event.result for event in events if isinstance(event, ToolFinished))
    assert result.error.code == "invalid_arguments"
    assert events[-1].reason == AgentStopReason.COMPLETED


async def test_missing_usage_remains_unknown_in_cumulative_total(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("value", encoding="utf-8")
    provider = ScriptedProvider(
        [
            [
                ToolCallDelta(0, "read", "read_file", '{"path":"note.txt"}'),
                UsageReported(TokenUsage(5, 2, 7)),
                StreamCompleted("tool_calls"),
            ],
            [TextDelta("done"), StreamCompleted("stop")],
        ]
    )
    runner = make_runner(tmp_path, provider)
    events = [event async for event in runner.run("usage")]
    usage_events = [event for event in events if isinstance(event, TokenUsageUpdated)]
    assert usage_events[0].cumulative.total_tokens == 7
    assert usage_events[1].request.total_tokens is None
    assert usage_events[1].cumulative.total_tokens is None


async def test_multiple_tool_results_are_returned_in_call_order(tmp_path) -> None:
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    provider = ScriptedProvider(
        [
            [
                ToolCallDelta(1, "b", "read_file", '{"path":"b.txt"}'),
                ToolCallDelta(0, "a", "read_file", '{"path":"a.txt"}'),
                StreamCompleted("tool_calls"),
            ],
            [TextDelta("都读取完成"), StreamCompleted("stop")],
        ]
    )
    runner = make_runner(tmp_path, provider)
    _ = [event async for event in runner.run("并行读取")]
    second_request = provider.requests[1][0]
    results = [item for item in second_request if isinstance(item, ToolResultMessage)]
    assert [item.tool_call_id for item in results] == ["a", "b"]


async def test_stream_error_keeps_completed_tool_checkpoint(tmp_path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("value", encoding="utf-8")
    provider = ScriptedProvider(
        [
            [ToolCallDelta(0, "read", "read_file", '{"path":"note.txt"}'), StreamCompleted("tool_calls")],
            ProviderError(ProviderErrorKind.NETWORK, "offline"),
        ]
    )
    conversation = Conversation()
    runner = make_runner(tmp_path, provider, conversation=conversation)
    events = [event async for event in runner.run("读取后继续")]
    assert events[-1].reason == AgentStopReason.STREAM_ERROR
    assert any(isinstance(item, ToolResultMessage) for item in conversation.messages_snapshot())


async def test_plan_mode_hides_writers_and_rejects_hallucinated_write(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            [ToolCallDelta(0, "write", "write_file", '{"path":"blocked.txt","content":"x"}'), StreamCompleted("tool_calls")],
            [TextDelta("只读计划"), StreamCompleted("stop")],
        ]
    )
    session = AgentSession(AgentMode.PLAN)
    runner = make_runner(tmp_path, provider)
    events = [event async for event in runner.run("先规划", session)]

    assert {tool.name for tool in provider.requests[0][1]} == {
        "read_file",
        "find_files",
        "search_code",
        "run_command",
    }
    result = next(event.result for event in events if isinstance(event, ToolFinished))
    assert result.error.code == "plan_mode_denied"
    assert not (tmp_path / "blocked.txt").exists()
    assert session.latest_plan == "只读计划"


async def test_do_mode_consumes_plan_in_next_request(tmp_path) -> None:
    provider = ScriptedProvider([[TextDelta("done"), StreamCompleted("stop")]])
    session = AgentSession(AgentMode.DO, "先检查 README")
    runner = make_runner(tmp_path, provider)
    _ = [event async for event in runner.run("执行", session)]
    systems = [item.content for item in provider.requests[0][0] if isinstance(item, SystemMessage)]
    assert any("先检查 README" in item for item in systems)
    assert session.latest_plan is None


async def test_side_effect_approval_is_exposed_as_agent_event(tmp_path) -> None:
    outside = tmp_path.parent / "agent-loop-outside.txt"
    provider = ScriptedProvider(
        [
            [
                ToolCallDelta(
                    0,
                    "outside",
                    "write_file",
                    '{"path":"%s","content":"ok"}' % outside,
                ),
                StreamCompleted("tool_calls"),
            ],
            [TextDelta("完成"), StreamCompleted("stop")],
        ]
    )
    runner = make_runner(tmp_path, provider)
    events = [event async for event in runner.run("外部写入")]
    assert any(isinstance(event, ApprovalPending) for event in events)
    assert outside.read_text(encoding="utf-8") == "ok"


class SlowProvider:
    display_name = "slow"
    model_name = "slow-model"

    def __init__(self) -> None:
        self.closed = False

    async def stream(self, messages, tools=(), tool_choice="auto"):
        try:
            yield TextDelta("partial")
            await asyncio.sleep(10)
            yield StreamCompleted("stop")
        finally:
            self.closed = True


async def test_cooperative_cancel_closes_provider_stream(tmp_path) -> None:
    provider = SlowProvider()
    runner = make_runner(tmp_path, provider)

    async def collect():
        return [event async for event in runner.run("cancel")]

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.02)
    runner.cancel()
    events = await asyncio.wait_for(task, 1)
    assert provider.closed is True
    assert events[-1].reason == AgentStopReason.CANCELLED
