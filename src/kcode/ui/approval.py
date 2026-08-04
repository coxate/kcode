from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from kcode.tools.base import ApprovalRequest


class ApprovalScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "deny", "Deny", show=False)]
    CSS = """
    ApprovalScreen { align: center middle; background: $background 60%; }
    #approval-dialog { width: 76; height: auto; max-height: 80%; padding: 1 2; border: round $warning; background: $surface; }
    #approval-summary { height: auto; margin: 1 0; }
    #approval-actions { height: 3; align-horizontal: right; }
    #approval-actions Button { margin-left: 1; }
    """

    def __init__(self, request: ApprovalRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Label(f"工具授权：{self.request.tool_name}")
            yield Static(self.request.summary, id="approval-summary", markup=False)
            yield Static(self.request.reason, markup=False)
            with Horizontal(id="approval-actions"):
                yield Button("拒绝", id="deny", variant="error")
                yield Button("仅允许这一次", id="allow", variant="success")

    def on_mount(self) -> None:
        self.query_one("#deny", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "allow")

    def action_deny(self) -> None:
        self.dismiss(False)
