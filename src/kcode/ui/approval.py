from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from kcode.permissions.models import ApprovalChoice
from kcode.tools.base import ApprovalRequest


class ApprovalScreen(ModalScreen[ApprovalChoice | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("up", "previous_option", "Previous", show=False),
        Binding("down", "next_option", "Next", show=False),
        Binding("1", "allow_once", "Allow once", show=False),
        Binding("2", "allow_always", "Always allow", show=False),
        Binding("3", "deny", "Deny", show=False),
    ]
    CSS = """
    ApprovalScreen { align: center middle; background: $background 60%; }
    #approval-dialog {
        width: 76;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    #approval-summary { height: auto; margin: 1 0; }
    #approval-actions { height: auto; }
    #approval-actions Button { width: 1fr; margin-top: 1; }
    """

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Label(f"工具授权：{self.request.tool_name}")
            if self.request.source_label:
                yield Static(f"来源：{self.request.source_label}", markup=False)
            yield Static(self.request.preview, id="approval-summary", markup=False)
            yield Static(self.request.reason, markup=False)
            with Vertical(id="approval-actions"):
                yield Button("1. 允许本次", id="allow-once", variant="success")
                yield Button("2. 永久允许", id="allow-always")
                yield Button("3. 拒绝本次", id="deny", variant="error")

    def on_mount(self) -> None:
        self.query_one("#allow-once", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choices = {
            "allow-once": ApprovalChoice.ALLOW_ONCE,
            "allow-always": ApprovalChoice.ALLOW_ALWAYS,
            "deny": ApprovalChoice.DENY,
        }
        self.dismiss(choices[event.button.id])

    def _move_focus(self, offset: int) -> None:
        buttons = list(self.query(Button))
        current = next((index for index, button in enumerate(buttons) if button.has_focus), 0)
        buttons[(current + offset) % len(buttons)].focus()

    def action_previous_option(self) -> None:
        self._move_focus(-1)

    def action_next_option(self) -> None:
        self._move_focus(1)

    def action_allow_once(self) -> None:
        self.dismiss(ApprovalChoice.ALLOW_ONCE)

    def action_allow_always(self) -> None:
        self.dismiss(ApprovalChoice.ALLOW_ALWAYS)

    def action_deny(self) -> None:
        self.dismiss(ApprovalChoice.DENY)

    def action_cancel(self) -> None:
        self.dismiss(None)
