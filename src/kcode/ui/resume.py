from __future__ import annotations

import time

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option

from kcode.history.models import SessionSummary


class ResumeScreen(ModalScreen[str | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "previous_option", "Previous", show=False, priority=True),
        Binding("down", "next_option", "Next", show=False, priority=True),
        Binding("enter", "choose", "Resume", show=False, priority=True),
    ]
    CSS = """
    ResumeScreen { align: center middle; background: $background 60%; }
    #resume-dialog {
        width: 92;
        height: 75%;
        padding: 1 2;
        border: round $accent;
        background: $surface;
    }
    #resume-search { margin: 1 0; }
    #resume-options { height: 1fr; }
    """

    def __init__(self, summaries: tuple[SessionSummary, ...]) -> None:
        super().__init__()
        self.summaries = summaries
        self.filtered = summaries

    def compose(self) -> ComposeResult:
        with Vertical(id="resume-dialog"):
            yield Label("恢复本地会话")
            yield Input(placeholder="搜索标题、模型或 session ID", id="resume-search")
            yield OptionList(*self._options(self.filtered), id="resume-options")

    def on_mount(self) -> None:
        self.query_one("#resume-search", Input).focus()
        options = self.query_one("#resume-options", OptionList)
        if options.option_count:
            options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().casefold()
        self.filtered = tuple(
            summary
            for summary in self.summaries
            if not query
            or query in summary.title.casefold()
            or query in summary.model.casefold()
            or query in summary.session_id.casefold()
        )
        options = self.query_one("#resume-options", OptionList)
        options.set_options(self._options(self.filtered))
        if options.option_count:
            options.highlighted = 0

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_choose()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id is not None and not event.option.disabled:
            self.dismiss(event.option.id)

    def action_previous_option(self) -> None:
        self.query_one("#resume-options", OptionList).action_cursor_up()

    def action_next_option(self) -> None:
        self.query_one("#resume-options", OptionList).action_cursor_down()

    def action_choose(self) -> None:
        options = self.query_one("#resume-options", OptionList)
        highlighted = options.highlighted
        if highlighted is None or not (0 <= highlighted < options.option_count):
            return
        option = options.get_option_at_index(highlighted)
        if option.id is not None and not option.disabled:
            self.dismiss(option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @classmethod
    def _options(cls, summaries: tuple[SessionSummary, ...]) -> list[Option]:
        if not summaries:
            return [Option("没有可恢复的会话", id=None, disabled=True)]
        return [
            Option(
                f"{summary.title}\n  {cls._relative_time(summary.last_active_at)} · "
                f"{summary.model} · {cls._size(summary.size_bytes)}"
                + (" · 正被占用" if summary.busy else ""),
                id=summary.session_id,
                disabled=summary.busy,
            )
            for summary in summaries
        ]

    @staticmethod
    def _relative_time(timestamp: float) -> str:
        seconds = max(0, int(time.time() - timestamp))
        if seconds < 60:
            return "刚刚"
        if seconds < 3600:
            return f"{seconds // 60} 分钟前"
        if seconds < 86400:
            return f"{seconds // 3600} 小时前"
        return f"{seconds // 86400} 天前"

    @staticmethod
    def _size(size_bytes: int) -> str:
        if size_bytes < 1024:
            return f"{size_bytes} B"
        if size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KiB"
        return f"{size_bytes / (1024 * 1024):.1f} MiB"
