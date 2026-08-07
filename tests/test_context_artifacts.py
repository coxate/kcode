import asyncio

from kcode.context import ArtifactStore, OffloadLedger
from kcode.conversation import AssistantMessage, ToolResultMessage
from kcode.tools.base import ToolCall, ToolResult


async def test_large_result_is_stored_once_with_stable_preview(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "session")
    ledger = OffloadLedger()
    call = ToolCall(0, "large-1", "run_command", "{}")
    result = ToolResultMessage(
        call.id,
        call.name,
        ToolResult.success({"stdout": "x" * 60_000}),
    )
    messages = (AssistantMessage("", (call,)), result)

    first, first_decisions = await ledger.process(messages, store)
    second, second_decisions = await ledger.process(messages, store)

    assert first == second
    assert first_decisions == second_decisions
    assert first_decisions[0].offloaded is True
    assert first_decisions[0].artifact is not None
    assert await store.read_range("large-1", offset=0, length=20)
    replacement = first[1]
    assert isinstance(replacement, ToolResultMessage)
    assert "not the complete tool result" in replacement.result.to_json()
    assert "large-1" in replacement.result.to_json()


async def test_concurrent_offload_reuses_one_atomic_decision(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "session")
    ledger = OffloadLedger()
    call = ToolCall(0, "large-1", "run_command", "{}")
    result = ToolResultMessage(
        call.id,
        call.name,
        ToolResult.success({"stdout": "x" * 60_000}),
    )
    messages = (AssistantMessage("", (call,)), result)

    first, second = await asyncio.gather(
        ledger.process(messages, store),
        ledger.process(messages, store),
    )

    assert first == second
    assert len(tuple(store.root.iterdir())) == 1


async def test_aggregate_offload_uses_stable_original_order(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "session")
    ledger = OffloadLedger()
    calls = tuple(ToolCall(index, f"call-{index}", "search_code", "{}") for index in range(5))
    results = tuple(
        ToolResultMessage(
            call.id,
            call.name,
            ToolResult.success({"content": str(index) + "x" * 44_990}),
        )
        for index, call in enumerate(calls)
    )

    visible, decisions = await ledger.process((AssistantMessage("", calls), *results), store)

    offloaded = [decision.tool_use_id for decision in decisions if decision.offloaded]
    assert offloaded == ["call-0"]
    visible_bytes = sum(
        len(message.result.to_json().encode("utf-8"))
        for message in visible
        if isinstance(message, ToolResultMessage)
    )
    assert visible_bytes <= ledger.aggregate_limit_bytes


async def test_failed_artifact_write_does_not_freeze_the_decision(tmp_path) -> None:
    class FailingStore(ArtifactStore):
        async def store(self, *args, **kwargs):
            raise OSError("disk unavailable")

    call = ToolCall(0, "large-1", "run_command", "{}")
    result = ToolResultMessage(
        call.id,
        call.name,
        ToolResult.success({"stdout": "x" * 60_000}),
    )
    messages = (AssistantMessage("", (call,)), result)
    ledger = OffloadLedger()

    visible, decisions = await ledger.process(messages, FailingStore(tmp_path, "failed"))
    assert visible == messages
    assert decisions == ()
    assert ledger.decision_for(call.id) is None

    _, recovered = await ledger.process(messages, ArtifactStore(tmp_path, "recovered"))
    assert recovered[0].offloaded is True


async def test_reading_an_artifact_does_not_reoffload_the_requested_content(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "session")
    reference = await store.store("source", "run_command", "x" * 60_000)
    ledger = OffloadLedger()
    call = ToolCall(0, "read-1", "read_file", "{}")
    result = ToolResultMessage(
        call.id,
        call.name,
        ToolResult.success({"path": str(tmp_path / reference.path), "content": "x" * 60_000}),
    )

    visible, decisions = await ledger.process((AssistantMessage("", (call,)), result), store)

    assert decisions[0].decision == "keep"
    assert visible[1] == result


async def test_artifact_store_redacts_sensitive_values(tmp_path) -> None:
    store = ArtifactStore(tmp_path, "session", sensitive_values=("secret-token",))
    reference = await store.store("call-1", "run_command", "value=secret-token")

    assert reference.redacted is True
    assert await store.read_range(reference) == "value=[REDACTED]"
