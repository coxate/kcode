from __future__ import annotations

import time

import pytest
from textual.widgets import Input

from kcode.conversation import (
    AssistantMessage,
    StableSystemMessage,
    SystemReminderMessage,
    UserMessage,
)
from kcode.events import StreamCompleted, TextDelta, ToolCallDelta
from kcode.history.ids import create_session_id
from kcode.history.journal import SessionJournal
from kcode.history.models import SessionMetadata
from kcode.history.runtime import SessionCoordinator
from kcode.instructions import InstructionLoader
from kcode.prompting import DEFAULT_PROMPT_SECTIONS, SystemPromptBuilder
from kcode.ui.app import KCodeApp
from kcode.ui.resume import ResumeScreen
from kcode.ui.widgets import ChatMessageWidget


class FakeProvider:
    display_name = "fake"
    model_name = "fake-model"

    async def stream(self, messages, tools=(), tool_choice="auto"):
        raise AssertionError("/resume and /clear must not call the provider")
        yield


class EndToEndProvider:
    display_name = "fake"

    def __init__(self, model_name: str, *, use_tool: bool) -> None:
        self.model_name = model_name
        self.use_tool = use_tool
        self.calls = 0
        self.requests = []

    async def stream(self, messages, tools=(), tool_choice="auto"):
        self.requests.append(tuple(messages))
        self.calls += 1
        if self.use_tool and self.calls == 1:
            yield ToolCallDelta(0, "read-e2e", "read_file", '{"path":"source.txt"}')
            yield StreamCompleted("tool_calls")
            return
        yield TextDelta("end-to-end answer")
        yield StreamCompleted("stop")


async def archived_session(tmp_path, title: str) -> str:
    session_id = create_session_id()
    journal = SessionJournal(
        tmp_path,
        SessionMetadata(1, session_id, time.time(), "fake", "fake-model"),
    )
    await journal.append_checkpoint((UserMessage(title), AssistantMessage(f"answer {title}")))
    await journal.close()
    return session_id


async def submit(app: KCodeApp, pilot, text: str) -> None:
    prompt = app.query_one("#prompt", Input)
    prompt.value = text
    prompt.focus()
    await pilot.press("enter")


@pytest.mark.asyncio
async def test_resume_screen_search_select_and_redraw_are_local(tmp_path) -> None:
    await archived_session(tmp_path, "alpha topic")
    selected_id = await archived_session(tmp_path, "beta topic")
    provider = FakeProvider()
    coordinator = SessionCoordinator(tmp_path, provider)
    app = KCodeApp(
        provider,
        coordinator.current.conversation,
        cwd=tmp_path,
        coordinator=coordinator,
    )

    async with app.run_test(size=(100, 32)) as pilot:
        await submit(app, pilot, "/resume")
        await pilot.pause()
        assert isinstance(app.screen, ResumeScreen)
        await pilot.press("b", "e", "t", "a")
        await pilot.pause()
        assert app.screen.filtered[0].session_id == selected_id
        await pilot.press("enter")
        await pilot.pause(0.5)
        notices = [widget.text for widget in app.query(ChatMessageWidget)]
        assert coordinator.current.session_id == selected_id, notices
        assert coordinator.current.conversation.snapshot()[0].user == "beta topic"
        assert "beta topic" in [widget.text for widget in app.query(ChatMessageWidget)]


@pytest.mark.asyncio
async def test_resume_escape_and_clear_keep_state_consistent(tmp_path) -> None:
    await archived_session(tmp_path, "history")
    provider = FakeProvider()
    coordinator = SessionCoordinator(tmp_path, provider)
    app = KCodeApp(
        provider,
        coordinator.current.conversation,
        cwd=tmp_path,
        coordinator=coordinator,
    )
    initial_id = coordinator.current.session_id

    async with app.run_test(size=(100, 32)) as pilot:
        await submit(app, pilot, "/resume")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert coordinator.current.session_id == initial_id

        await submit(app, pilot, "/clear")
        await pilot.pause()
        assert coordinator.current.session_id != initial_id
        assert coordinator.current.conversation.snapshot() == ()


@pytest.mark.asyncio
async def test_phase_one_end_to_end_through_tui(tmp_path) -> None:
    home = tmp_path / "home"
    (home / ".kcode").mkdir(parents=True)
    (home / ".kcode/KCODE.md").write_text("user rule", encoding="utf-8")
    (tmp_path / "KCODE.md").write_text("project rule", encoding="utf-8")
    (tmp_path / ".kcode").mkdir()
    (tmp_path / ".kcode/KCODE.md").write_text("local rule", encoding="utf-8")
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    instructions = InstructionLoader().load(tmp_path, home)
    prompt_builder = SystemPromptBuilder(DEFAULT_PROMPT_SECTIONS).with_content(
        "custom_instructions", instructions.content
    )

    first_provider = EndToEndProvider("old-model", use_tool=True)
    first = SessionCoordinator(tmp_path, first_provider)
    first_app = KCodeApp(
        first_provider,
        first.current.conversation,
        cwd=tmp_path,
        coordinator=first,
        prompt_builder=prompt_builder,
    )
    target_id = first.current.session_id
    async with first_app.run_test(size=(100, 32)) as pilot:
        await submit(first_app, pilot, "start with tool")
        await pilot.pause(0.5)
        assert first.current.conversation.snapshot()[0].assistant == "end-to-end answer"
        assert any(
            isinstance(message, StableSystemMessage) and "local rule" in message.content
            for message in first_provider.requests[0]
        )

    journal_path = tmp_path / ".kcode/sessions" / target_id / "conversation.jsonl"
    assert journal_path.is_file()
    assert "read-e2e" in journal_path.read_text(encoding="utf-8")

    second_provider = EndToEndProvider("new-model", use_tool=False)
    second = SessionCoordinator(tmp_path, second_provider)
    second_app = KCodeApp(
        second_provider,
        second.current.conversation,
        cwd=tmp_path,
        coordinator=second,
        prompt_builder=prompt_builder,
    )
    async with second_app.run_test(size=(100, 32)) as pilot:
        await submit(second_app, pilot, "/resume")
        await pilot.pause()
        assert isinstance(second_app.screen, ResumeScreen)
        await pilot.press("enter")
        await pilot.pause(0.5)
        assert second.current.session_id == target_id
        notices = [widget.text for widget in second_app.query(ChatMessageWidget)]
        assert any("old-model" in notice and "new-model" in notice for notice in notices)

        await submit(second_app, pilot, "continue")
        await pilot.pause(0.3)
        assert any(
            isinstance(message, SystemReminderMessage) and message.kind == "session_resume"
            for message in second_provider.requests[0]
        )

        resumed_id = second.current.session_id
        await submit(second_app, pilot, "/clear")
        await pilot.pause()
        assert second.current.session_id != resumed_id
        assert resumed_id in {summary.session_id for summary in second.list_sessions()}
