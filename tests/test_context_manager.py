import json

from kcode.context import ContextManager
from kcode.conversation import UserMessage
from kcode.events import StreamCompleted, TextDelta
from kcode.tools.base import ToolDefinition


class SummaryProvider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self):
        self.calls = 0

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.calls += 1
        yield TextDelta(
            json.dumps(
                {
                    "goal": "continue",
                    "confirmed_facts": ["canonical history remains"],
                    "inferences": [],
                    "unknowns": [],
                    "decisions": [],
                    "files": [],
                    "errors": [],
                    "current_state": "compacted",
                    "pending_tasks": [],
                    "next_steps": [],
                    "artifact_references": [],
                    "history_incomplete": False,
                }
            )
        )
        yield StreamCompleted("stop")


async def test_manual_compaction_changes_only_model_view(tmp_path) -> None:
    provider = SummaryProvider()
    manager = ContextManager(tmp_path, provider=provider, context_window=100_000)
    canonical = tuple(UserMessage(f"message-{index}-" + "x" * 9_000) for index in range(7))
    original = tuple(canonical)

    snapshot = await manager.compact(canonical)

    assert canonical == original
    assert snapshot.used_compaction is True
    assert snapshot.reason == "manual"
    assert len(snapshot.messages) < len(canonical) + 2
    assert "structured working memory" in snapshot.messages[0].content
    assert "recovery segment" in snapshot.messages[1].content
    assert provider.calls == 1


async def test_automatic_compaction_breaker_preserves_history(tmp_path) -> None:
    class InvalidProvider(SummaryProvider):
        async def stream(self, messages, tools=(), tool_choice="auto"):
            self.calls += 1
            yield TextDelta("not-json")
            yield StreamCompleted("stop")

    provider = InvalidProvider()
    manager = ContextManager(tmp_path, provider=provider, context_window=33_001)
    canonical = (UserMessage("x" * 100),)

    for _ in range(4):
        snapshot = await manager.build_snapshot(canonical)

    assert manager.automatic_compaction_disabled is True
    assert provider.calls == 3
    assert snapshot.messages == canonical


async def test_failed_compaction_keeps_the_previous_model_view(tmp_path) -> None:
    class ChangingProvider(SummaryProvider):
        async def stream(self, messages, tools=(), tool_choice="auto"):
            self.calls += 1
            if self.calls == 1:
                async for event in super().stream(messages, tools, tool_choice):
                    yield event
                return
            yield TextDelta("not-json")
            yield StreamCompleted("stop")

    provider = ChangingProvider()
    manager = ContextManager(tmp_path, provider=provider, context_window=100_000)
    canonical = tuple(UserMessage(f"message-{index}-" + "x" * 9_000) for index in range(7))
    first = await manager.compact(canonical)
    state = manager.compaction_state

    second = await manager.compact(canonical)

    assert first.used_compaction is True
    assert second.used_compaction is False
    assert manager.compaction_state == state
    assert second.messages == first.messages


async def test_recovery_keeps_five_latest_files_and_exact_tool_schema(tmp_path) -> None:
    provider = SummaryProvider()
    manager = ContextManager(tmp_path, provider=provider, context_window=100_000)
    for index in range(7):
        await manager.record_file_snapshot(f"file-{index}.py", "x" * 20_000)
    tools = (ToolDefinition("read_file", "Read a file", {"type": "object"}),)
    canonical = tuple(UserMessage(f"message-{index}-" + "x" * 9_000) for index in range(7))

    snapshot = await manager.compact(canonical, tools)
    recovery = snapshot.messages[1].content

    assert snapshot.tools == tools
    assert "file-6.py" in recovery
    assert "file-2.py" in recovery
    assert "file-1.py" not in recovery
    assert "read_at:" in recovery
    assert "truncated: true" in recovery
    assert '"name":"read_file"' in recovery
    assert "may be incomplete or stale" in recovery
