import asyncio
import json
import time
from pathlib import Path

from filelock import FileLock

from kcode.events import StreamCompleted, TextDelta
from kcode.memory import DecisionKind, MemoryDecision, MemoryScope, MemoryStatus
from kcode.memory.models import (
    CompletedTurn,
    MemoryRecord,
    MemoryState,
    MemoryType,
)
from kcode.memory.runtime import MemoryCoordinator


class Provider:
    display_name = "fake"
    model_name = "fake-model"

    def __init__(self, responses=()):
        self.responses = list(responses)
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append((messages, tools, tool_choice))
        yield TextDelta(self.responses.pop(0))
        yield StreamCompleted()


def candidate_json() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "action": "create",
                    "type": "project_fact",
                    "scope": "project",
                    "title": "Use uv",
                    "summary": "This project uses uv.",
                    "application": "Use uv for Python commands.",
                    "reason": "Stable convention",
                    "evidence": "这个项目使用 uv",
                }
            ]
        }
    )


async def test_signal_to_persisted_candidate_to_confirmed_prompt(tmp_path: Path) -> None:
    provider = Provider((candidate_json(),))
    coordinator = MemoryCoordinator(tmp_path, provider, home=tmp_path / "home")
    assert not coordinator.start().content
    turn = CompletedTurn.create(
        "session-1",
        "请记住，这个项目使用 uv",
        "明白。",
        "default",
    )
    assert coordinator.submit_turn(turn)
    await coordinator._queue.join()
    pending = coordinator.pending()
    assert len(pending) == 1
    candidate_path = coordinator.project_store.paths.proposals / f"{pending[0].id}.json"
    assert candidate_path.is_file()

    result = await coordinator.apply(
        MemoryDecision(proposal_id=pending[0].id, kind=DecisionKind.APPROVE)
    )
    assert result.changed
    assert "This project uses uv" in result.prompt.content
    assert coordinator.pending() == ()
    assert len(coordinator.records()) == 1
    await coordinator.close()


async def test_plain_turn_skips_provider_but_counts_completed_session(tmp_path: Path) -> None:
    provider = Provider()
    coordinator = MemoryCoordinator(tmp_path, provider, home=tmp_path / "home")
    coordinator.start()
    turn = CompletedTurn.create("session-plain", "What is a tuple?", "A sequence.", "default")
    assert not coordinator.submit_turn(turn)
    assert not provider.requests
    assert await coordinator.session_closed("session-plain", "clear") == ()
    assert "session-plain" in coordinator.project_store.load_state().completed_session_ids
    await coordinator.close()


async def test_status_edit_delete_and_lock_release(tmp_path: Path) -> None:
    provider = Provider((candidate_json(),))
    coordinator = MemoryCoordinator(tmp_path, provider, home=tmp_path / "home")
    coordinator.start()
    coordinator.submit_turn(CompletedTurn.create("s", "请记住项目使用 uv", "好的", "default"))
    await coordinator._queue.join()
    proposal = coordinator.pending()[0]
    await coordinator.apply(MemoryDecision(proposal_id=proposal.id, kind=DecisionKind.APPROVE))
    record = coordinator.records()[0]

    lock = FileLock(str(coordinator.project_store.paths.lock))
    lock.acquire(timeout=0)
    lock.release()

    edited = await coordinator.edit_record(
        MemoryScope.PROJECT,
        record.id,
        title="Use uv only",
        summary="Use uv for this project.",
        application="Never invoke bare pip.",
        body="Reviewed.",
    )
    assert edited.changed
    assert coordinator.records()[0].title == "Use uv only"
    await coordinator.set_status(MemoryScope.PROJECT, record.id, MemoryStatus.INACTIVE)
    assert not coordinator.render_prompt().content
    await coordinator.set_status(MemoryScope.PROJECT, record.id, MemoryStatus.ACTIVE)
    assert coordinator.render_prompt().content
    await coordinator.delete(MemoryScope.PROJECT, record.id)
    assert coordinator.records() == ()
    await coordinator.close()


async def test_rejected_or_secret_edited_candidate_never_becomes_memory(tmp_path: Path) -> None:
    provider = Provider((candidate_json(), candidate_json()))
    coordinator = MemoryCoordinator(
        tmp_path,
        provider,
        home=tmp_path / "home",
        sensitive_values=("known-secret",),
    )
    coordinator.start()
    first = CompletedTurn.create("s1", "请记住项目使用 uv", "好的", "default")
    coordinator.submit_turn(first)
    await coordinator._queue.join()
    proposal = coordinator.pending()[0]
    rejected = await coordinator.apply(
        MemoryDecision(proposal_id=proposal.id, kind=DecisionKind.REJECT)
    )
    assert not rejected.changed
    assert coordinator.records() == ()

    second = CompletedTurn.create("s2", "请记住项目使用 uv", "确认", "default")
    coordinator.submit_turn(second)
    await coordinator._queue.join()
    proposal = coordinator.pending()[0]
    blocked = await coordinator.apply(
        MemoryDecision(
            proposal_id=proposal.id,
            kind=DecisionKind.EDIT,
            title="Secret",
            summary="token=known-secret",
            application="Reuse it",
        )
    )
    assert not blocked.changed
    assert coordinator.records() == ()
    assert coordinator.pending()
    await coordinator.close()


async def test_pending_candidate_survives_restart(tmp_path: Path) -> None:
    provider = Provider((candidate_json(),))
    first = MemoryCoordinator(tmp_path, provider, home=tmp_path / "home")
    first.start()
    first.submit_turn(CompletedTurn.create("s", "请记住项目使用 uv", "好", "default"))
    await first._queue.join()
    proposal_id = first.pending()[0].id
    await first.close()

    reopened = MemoryCoordinator(tmp_path, Provider(), home=tmp_path / "home")
    reopened.start()
    assert reopened.pending()[0].id == proposal_id
    await reopened.apply(MemoryDecision(proposal_id=proposal_id, kind=DecisionKind.APPROVE))
    await reopened.close()

    new_session = MemoryCoordinator(tmp_path, Provider(), home=tmp_path / "home")
    assert "This project uses uv" in new_session.start().content
    await new_session.close()


async def test_invalid_user_scope_does_not_disable_project_scope(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (home / ".kcode").symlink_to(outside, target_is_directory=True)
    coordinator = MemoryCoordinator(tmp_path, Provider(), home=home)
    coordinator.start()
    assert coordinator.user_store is None
    assert coordinator.project_store is not None
    assert any("user long-term memory is unavailable" in item for item in coordinator.warnings)
    await coordinator.close()


async def test_invalid_extraction_degrades_without_losing_chat_state(tmp_path: Path) -> None:
    coordinator = MemoryCoordinator(
        tmp_path,
        Provider(("not-json",)),
        home=tmp_path / "home",
    )
    coordinator.start()
    coordinator.submit_turn(CompletedTurn.create("s", "please remember this", "okay", "default"))
    await coordinator._queue.join()
    assert coordinator.pending() == ()
    assert any("extraction failed" in item for item in coordinator.warnings)
    await coordinator.close()


async def test_session_close_triggers_due_governance_and_resets_counter(tmp_path: Path) -> None:
    provider = Provider((json.dumps({"candidates": []}),))
    coordinator = MemoryCoordinator(tmp_path, provider, home=tmp_path / "home")
    coordinator.start()
    assert coordinator.project_store is not None
    now = time.time()
    for index in range(10):
        coordinator.project_store.save(
            MemoryRecord(
                id=f"mem_{index:032x}",
                type=MemoryType.PROJECT_FACT,
                scope=MemoryScope.PROJECT,
                title=f"Fact {index}",
                summary="Summary",
                application="Apply",
                source_session_id="source",
                source_turn_hash=f"{index:064x}",
                created_at=now,
                updated_at=now,
            )
        )
    coordinator.project_store.save_state(
        MemoryState(completed_session_ids=("s1", "s2", "s3", "s4"))
    )
    coordinator._reload(MemoryScope.PROJECT)
    coordinator.submit_turn(CompletedTurn.create("s5", "plain question", "answer", "default"))
    await coordinator.session_closed("s5", "clear")
    await asyncio.gather(*tuple(coordinator._governance_tasks))
    state = coordinator.project_store.load_state()
    assert state.last_governed_at is not None
    assert state.completed_session_ids == ()
    await coordinator.close()


async def test_close_cancels_unfinished_extraction(tmp_path: Path, monkeypatch) -> None:
    class SlowProvider(Provider):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        async def stream(self, messages, tools=(), tool_choice="auto"):
            try:
                await asyncio.sleep(10)
                yield TextDelta(candidate_json())
                yield StreamCompleted()
            finally:
                self.closed = True

    monkeypatch.setattr("kcode.memory.runtime.CLOSE_TIMEOUT_SECONDS", 0.01)
    provider = SlowProvider()
    coordinator = MemoryCoordinator(tmp_path, provider, home=tmp_path / "home")
    coordinator.start()
    coordinator.submit_turn(CompletedTurn.create("s", "remember this", "okay", "default"))
    await asyncio.sleep(0)
    warnings = await coordinator.close()
    assert provider.closed
    assert warnings
    assert coordinator.pending() == ()
