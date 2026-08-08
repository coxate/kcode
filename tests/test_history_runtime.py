from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kcode.conversation import AssistantMessage, UserMessage
from kcode.events import StreamCompleted, TextDelta
from kcode.history.journal import SessionBusyError
from kcode.history.runtime import SessionCoordinator


class FakeProvider:
    display_name = "fake-provider"

    def __init__(self, model_name: str = "fake-model") -> None:
        self.model_name = model_name


class CompactingProvider(FakeProvider):
    config = SimpleNamespace(context_window=33_000)

    def __init__(self, *, valid: bool) -> None:
        super().__init__("compact-model")
        self.valid = valid
        self.requests = 0

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests += 1
        if self.valid:
            payload = {
                "goal": "resume",
                "confirmed_facts": [],
                "inferences": [],
                "unknowns": [],
                "decisions": [],
                "files": [],
                "errors": [],
                "current_state": "restored",
                "pending_tasks": [],
                "next_steps": [],
                "artifact_references": [],
                "history_incomplete": False,
            }
            yield TextDelta(json.dumps(payload))
        else:
            yield TextDelta("invalid summary")
        yield StreamCompleted("stop")


@pytest.mark.asyncio
async def test_runtime_shares_id_clear_preserves_old_and_creates_fresh(tmp_path) -> None:
    coordinator = SessionCoordinator(tmp_path, FakeProvider())
    old = coordinator.current
    assert old.session_id == old.context_manager.session_id
    assert old.session_id == old.metadata.session_id
    assert await old.journal.append_checkpoint((UserMessage("old"), AssistantMessage("answer")))

    fresh, warnings = await coordinator.clear()
    assert not warnings
    assert fresh.session_id != old.session_id
    assert old.journal.path.is_file()
    size_after_clear = old.journal.path.stat().st_size
    assert await fresh.journal.append_checkpoint((UserMessage("new"), AssistantMessage("reply")))
    assert old.journal.path.stat().st_size == size_after_clear
    assert await coordinator.close() == ()


@pytest.mark.asyncio
async def test_resume_prepares_candidate_then_switches_and_warns_on_model_change(tmp_path) -> None:
    original = SessionCoordinator(tmp_path, FakeProvider("old-model"))
    target_id = original.current.session_id
    messages = (UserMessage("remember me"), AssistantMessage("remembered"))
    await original.current.journal.append_checkpoint(messages)
    await original.close()

    current = SessionCoordinator(tmp_path, FakeProvider("new-model"))
    previous_id = current.current.session_id
    result = await current.resume(target_id)
    assert current.current.session_id == target_id
    assert result.runtime.conversation.messages_snapshot() == messages
    assert result.runtime.resume_reminder is not None
    assert any("new-model" in warning and "old-model" in warning for warning in result.warnings)
    assert previous_id != current.current.session_id

    assert await current.current.journal.append_checkpoint(
        (UserMessage("continued"), AssistantMessage("yes"))
    )
    await current.close()
    assert "continued" in result.runtime.journal.path.read_text(encoding="utf-8")

    reopened = SessionCoordinator(tmp_path, FakeProvider("new-model"))
    assert (await reopened.resume(target_id)).runtime.session_id == target_id
    await reopened.close()


@pytest.mark.asyncio
async def test_busy_or_missing_candidate_keeps_current_runtime(tmp_path) -> None:
    owner = SessionCoordinator(tmp_path, FakeProvider())
    await owner.current.journal.append_checkpoint((UserMessage("held"), AssistantMessage("x")))

    other = SessionCoordinator(tmp_path, FakeProvider())
    original_runtime = other.current
    with pytest.raises(SessionBusyError):
        await other.resume(owner.current.session_id)
    assert other.current is original_runtime

    with pytest.raises(Exception):
        await other.resume("20260808-103000-a1b2")
    assert other.current is original_runtime
    await owner.close()
    await other.close()


@pytest.mark.asyncio
async def test_resume_precompacts_over_budget_and_failure_keeps_old_runtime(tmp_path) -> None:
    archived = SessionCoordinator(tmp_path, FakeProvider("archive-model"))
    target_id = archived.current.session_id
    await archived.current.journal.append_checkpoint(
        (UserMessage("large history"), AssistantMessage("saved"))
    )
    await archived.close()

    compacting = CompactingProvider(valid=True)
    success = SessionCoordinator(tmp_path, compacting)
    result = await success.resume(target_id)
    assert compacting.requests == 1
    assert result.runtime.context_manager.compaction_state is not None
    await success.close()

    before = result.runtime.journal.path.read_bytes()
    failing = SessionCoordinator(tmp_path, CompactingProvider(valid=False))
    old_runtime = failing.current
    with pytest.raises(Exception, match="could not be compacted"):
        await failing.resume(target_id)
    assert failing.current is old_runtime
    assert result.runtime.journal.path.read_bytes() == before
    await failing.close()


class RecordingCloseListener:
    def __init__(self) -> None:
        self.calls = []

    async def session_closed(self, session_id: str, reason: str) -> tuple[str, ...]:
        self.calls.append((session_id, reason))
        return ()


@pytest.mark.asyncio
async def test_close_listener_observes_each_old_session_once(tmp_path) -> None:
    listener = RecordingCloseListener()
    coordinator = SessionCoordinator(tmp_path, FakeProvider(), close_listeners=(listener,))
    old_id = coordinator.current.session_id
    await coordinator.clear()
    fresh_id = coordinator.current.session_id
    await coordinator.close()
    await coordinator.close()
    assert listener.calls == [(old_id, "clear"), (fresh_id, "exit")]
