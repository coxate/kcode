import asyncio
import time
from pathlib import Path

from textual.widgets import Input, Static

from kcode.events import StreamCompleted, TextDelta
from kcode.memory.models import (
    MemoryAction,
    MemoryProposal,
    MemoryRecord,
    MemoryScope,
    MemoryType,
    proposal_id,
)
from kcode.memory.runtime import MemoryCoordinator
from kcode.ui.app import KCodeApp
from kcode.ui.memory import MemoryReviewScreen, MemoryScreen


class Provider:
    display_name = "fake"
    model_name = "fake-model"

    async def stream(self, messages, tools=(), tool_choice="auto"):
        if False:
            yield


async def test_ctrl_m_opens_memory_panel_without_slash_command(tmp_path: Path) -> None:
    coordinator = MemoryCoordinator(tmp_path, Provider(), home=tmp_path / "home")
    coordinator.start()
    app = KCodeApp(Provider(), cwd=tmp_path, memory_coordinator=coordinator)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("ctrl+m")
        await pilot.pause()
        assert isinstance(app.screen, MemoryScreen)
        await pilot.press("escape")


async def test_persisted_candidate_is_reviewed_when_app_becomes_idle(tmp_path: Path) -> None:
    coordinator = MemoryCoordinator(tmp_path, Provider(), home=tmp_path / "home")
    values = {"scope": "project", "title": "Use uv", "source": "a" * 64}
    proposal = MemoryProposal(
        id=proposal_id(values),
        action=MemoryAction.CREATE,
        type=MemoryType.PROJECT_FACT,
        scope=MemoryScope.PROJECT,
        title="Use uv",
        summary="This project uses uv.",
        application="Use uv for Python commands.",
        reason="Stable convention",
        evidence="The user said so.",
        source_session_id="session",
        source_turn_hash="a" * 64,
        created_at=time.time(),
    )
    coordinator.project_store.save_proposal(proposal)
    coordinator.start()
    app = KCodeApp(Provider(), cwd=tmp_path, memory_coordinator=coordinator)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MemoryReviewScreen)
        await pilot.press("1")
        await pilot.pause()
        assert len(coordinator.records()) == 1


async def test_panel_inactivates_restores_and_double_confirms_delete(tmp_path: Path) -> None:
    coordinator = MemoryCoordinator(tmp_path, Provider(), home=tmp_path / "home")
    now = time.time()
    record = MemoryRecord(
        id="mem_" + "b" * 32,
        type=MemoryType.PROJECT_FACT,
        scope=MemoryScope.PROJECT,
        title="Use uv",
        summary="This project uses uv.",
        application="Use uv.",
        source_session_id="session",
        source_turn_hash="b" * 64,
        created_at=now,
        updated_at=now,
    )
    coordinator.project_store.save(record)
    coordinator.start()
    app = KCodeApp(Provider(), cwd=tmp_path, memory_coordinator=coordinator)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("ctrl+m")
        await pilot.pause()
        await pilot.press("i")
        await pilot.pause()
        assert coordinator.records()[0].status.value == "inactive"

        await pilot.press("i")
        await pilot.pause()
        assert coordinator.records()[0].status.value == "active"

        await pilot.press("d")
        await pilot.pause()
        assert coordinator.records()
        await pilot.press("y")
        await pilot.pause()
        assert coordinator.records() == ()
        assert app.conversation.messages_snapshot() == ()


async def test_update_review_displays_existing_and_proposed_values(tmp_path: Path) -> None:
    coordinator = MemoryCoordinator(tmp_path, Provider(), home=tmp_path / "home")
    now = time.time()
    record = MemoryRecord(
        id="mem_" + "c" * 32,
        type=MemoryType.PROJECT_FACT,
        scope=MemoryScope.PROJECT,
        title="Old title",
        summary="Old summary",
        application="Old application",
        source_session_id="session",
        source_turn_hash="c" * 64,
        created_at=now,
        updated_at=now,
    )
    coordinator.project_store.save(record)
    values = {"scope": "project", "title": "New title", "target": record.id}
    proposal = MemoryProposal(
        id=proposal_id(values),
        action=MemoryAction.UPDATE,
        type=MemoryType.PROJECT_FACT,
        scope=MemoryScope.PROJECT,
        target_ids=(record.id,),
        title="New title",
        summary="New summary",
        application="New application",
        reason="Correction",
        evidence="The project changed.",
        source_session_id="session",
        source_turn_hash="d" * 64,
        created_at=now,
    )
    coordinator.project_store.save_proposal(proposal)
    coordinator.start()
    app = KCodeApp(Provider(), cwd=tmp_path, memory_coordinator=coordinator)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert isinstance(app.screen, MemoryReviewScreen)
        visible = "\n".join(str(widget.content) for widget in app.screen.query(Static))
        assert "Existing" in visible
        assert "Old title" in visible
        assert app.screen.query_one("#memory-title", Input).value == "New title"
        await pilot.press("escape")


async def test_ctrl_m_does_not_preempt_active_generation(tmp_path: Path) -> None:
    release = asyncio.Event()

    class SlowChatProvider(Provider):
        async def stream(self, messages, tools=(), tool_choice="auto"):
            yield TextDelta("working")
            await release.wait()
            yield StreamCompleted()

    coordinator = MemoryCoordinator(tmp_path, Provider(), home=tmp_path / "home")
    coordinator.start()
    app = KCodeApp(SlowChatProvider(), cwd=tmp_path, memory_coordinator=coordinator)
    async with app.run_test(size=(100, 30)) as pilot:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "plain question"
        await pilot.press("enter")
        await pilot.pause()
        assert app.generating
        await pilot.press("ctrl+m")
        await pilot.pause()
        assert not isinstance(app.screen, MemoryScreen)
        release.set()
        await pilot.pause()
