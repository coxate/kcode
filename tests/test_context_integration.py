import json

from kcode.conversation import Conversation, ToolResultMessage
from kcode.errors import ProviderError, ProviderErrorKind
from kcode.events import AgentStopped, AgentStopReason, StreamCompleted, TextDelta, ToolCallDelta
from kcode.orchestration import AgentRunner
from kcode.permissions import (
    LocalPermissionStore,
    PermissionEngine,
    empty_permission_settings,
)
from kcode.tools.base import ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import create_default_registry


async def allow(_request):
    return True


def summary_payload() -> str:
    return json.dumps(
        {
            "goal": "continue the coding task",
            "confirmed_facts": ["the request exceeded the context window"],
            "inferences": [],
            "unknowns": [],
            "decisions": ["retry once after compaction"],
            "files": [],
            "errors": ["prompt_too_long"],
            "current_state": "retrying",
            "pending_tasks": [],
            "next_steps": ["send the compacted model view"],
            "artifact_references": [],
            "history_incomplete": False,
        }
    )


def make_runner(tmp_path, provider, conversation=None):
    registry = create_default_registry()
    settings = empty_permission_settings(tmp_path)
    return AgentRunner(
        provider,
        conversation or Conversation(),
        registry,
        ToolExecutor(
            registry,
            PermissionEngine(settings),
            LocalPermissionStore(settings.layers[0].path),
        ),
        ToolContext(tmp_path),
        allow,
    )


class LargeReadProvider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self):
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((tuple(messages), tuple(tools), tool_choice))
        if len(self.requests) == 1:
            yield ToolCallDelta(0, "read-1", "read_file", '{"path":"large.txt"}')
            yield StreamCompleted("tool_calls")
        else:
            yield TextDelta("done")
            yield StreamCompleted("stop")


async def test_agent_offloads_large_result_without_changing_canonical_history(tmp_path) -> None:
    (tmp_path / "large.txt").write_text("x" * 60_000, encoding="utf-8")
    provider = LargeReadProvider()
    conversation = Conversation()
    runner = make_runner(tmp_path, provider, conversation)

    events = [event async for event in runner.run("read the large file")]

    assert len(provider.requests) == 2
    second_messages = provider.requests[1][0]
    visible_result = next(item for item in second_messages if isinstance(item, ToolResultMessage))
    assert "KCode Artifact preview" in visible_result.result.to_json()
    canonical_result = next(
        item for item in conversation.messages_snapshot() if isinstance(item, ToolResultMessage)
    )
    assert len(canonical_result.result.to_json()) > 50_000
    assert list((tmp_path / ".kcode" / "sessions").glob("*/tool-results/read-1"))
    assert events[-1] == AgentStopped(AgentStopReason.COMPLETED, 2, "")


class EmergencyProvider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self):
        self.requests = []
        self.normal_requests = 0

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((tuple(messages), tuple(tools), tool_choice))
        if tool_choice == "none":
            yield TextDelta(summary_payload())
            yield StreamCompleted("stop")
            return
        self.normal_requests += 1
        if self.normal_requests == 1:
            raise ProviderError(ProviderErrorKind.PROMPT_TOO_LONG, "prompt too long")
        yield TextDelta("recovered")
        yield StreamCompleted("stop")


async def test_prompt_too_long_runs_one_emergency_compaction_and_one_retry(tmp_path) -> None:
    provider = EmergencyProvider()
    conversation = Conversation()
    runner = make_runner(tmp_path, provider, conversation)

    events = [event async for event in runner.run("continue")]

    assert len(provider.requests) == 3
    assert provider.requests[1][1:] == ((), "none")
    assert provider.requests[0][1] == provider.requests[2][1]
    assert provider.normal_requests == 2
    assert conversation.snapshot()[0].assistant == "recovered"
    assert events[-1] == AgentStopped(AgentStopReason.COMPLETED, 1, "")


async def test_second_prompt_too_long_does_not_recurse(tmp_path) -> None:
    class AlwaysTooLongProvider(EmergencyProvider):
        async def stream(self, messages, tools=(), tool_choice="auto"):
            self.requests.append((tuple(messages), tuple(tools), tool_choice))
            if tool_choice == "none":
                yield TextDelta(summary_payload())
                yield StreamCompleted("stop")
                return
            self.normal_requests += 1
            raise ProviderError(ProviderErrorKind.PROMPT_TOO_LONG, "prompt too long")

    provider = AlwaysTooLongProvider()
    runner = make_runner(tmp_path, provider)

    events = [event async for event in runner.run("continue")]

    assert len(provider.requests) == 3
    assert provider.normal_requests == 2
    assert events[-1].reason == AgentStopReason.STREAM_ERROR
