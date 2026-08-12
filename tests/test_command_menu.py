from __future__ import annotations

import pytest
from textual.widgets import Input, OptionList

from kcode.ui.app import KCodeApp
from kcode.ui.command_menu import CommandMenu
from kcode.ui.widgets import ChatMessageWidget


class FakeProvider:
    display_name = "fake-provider"
    model_name = "fake-model"

    def __init__(self) -> None:
        self.requests: list[tuple[object, ...]] = []

    async def stream(self, messages):
        self.requests.append(tuple(messages))
        if False:
            yield None


async def test_menu_filters_canonical_names_and_alias_selects_default() -> None:
    app = KCodeApp(FakeProvider())
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/s"
        await pilot.pause()

        menu = app.query_one("#command-menu", CommandMenu)
        assert menu.display
        assert [command.name for command in menu.commands] == ["session", "skill", "status"]
        assert menu.selected_name() == "status"
        assert app.focused is prompt


async def test_tab_completes_without_execution_and_space_closes_menu() -> None:
    provider = FakeProvider()
    app = KCodeApp(provider)
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/s"
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()

        assert prompt.value == "/status "
        assert not app.query_one("#command-menu", CommandMenu).display
        assert provider.requests == []


async def test_arrows_enter_escape_and_zero_match() -> None:
    app = KCodeApp(FakeProvider())
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        menu = app.query_one("#command-menu", CommandMenu)

        prompt.value = "/"
        await pilot.pause()
        first = menu.selected_name()
        await pilot.press("down")
        assert menu.selected_name() != first
        await pilot.press("up")
        assert menu.selected_name() == first
        await pilot.press("escape")
        assert prompt.value == "/"
        assert not menu.display

        prompt.value = "/does-not-exist"
        await pilot.pause()
        assert menu.display
        assert menu.selected_name() is None
        assert menu.get_option_at_index(0).disabled
        await pilot.press("enter")
        await pilot.pause()
        assert not menu.display
        notices = [widget.text for widget in app.query(ChatMessageWidget)]
        assert any("未知命令" in notice for notice in notices)


@pytest.mark.parametrize("size", [(80, 24), (100, 30)])
async def test_enter_executes_highlighted_command_and_menu_has_six_line_cap(
    size: tuple[int, int],
) -> None:
    app = KCodeApp(FakeProvider())
    async with app.run_test(size=size) as pilot:
        prompt = app.query_one("#prompt", Input)
        menu = app.query_one("#command-menu", OptionList)
        prompt.value = "/s"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        notices = [widget.text for widget in app.query(ChatMessageWidget)]
        assert any("会话 Token" in notice for notice in notices)

        prompt.value = "/"
        await pilot.pause()
        assert menu.option_count == 18
        assert menu.size.height <= 6
