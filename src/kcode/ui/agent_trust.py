from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from kcode.subagents.trust import AgentTrustRequest


class AgentTrustScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "deny", "Deny", show=False),
        Binding("1", "trust", "Trust", show=False),
        Binding("2", "deny", "Deny", show=False),
    ]
    CSS = """
    AgentTrustScreen { align: center middle; background: $background 60%; }
    #agent-trust-dialog {
        width: 82; height: auto; max-height: 85%; padding: 1 2;
        border: round $error; background: $surface;
    }
    #agent-trust-summary { height: auto; margin: 1 0; }
    #agent-trust-actions { height: auto; }
    #agent-trust-actions Button { width: 1fr; margin-top: 1; }
    """

    def __init__(self, request: AgentTrustRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        names = ", ".join(self.request.agent_names)
        with Vertical(id="agent-trust-dialog"):
            yield Label("项目请求加载 SubAgent 角色")
            yield Static(
                f"项目：{self.request.project_root}\nAgents：{names}",
                id="agent-trust-summary",
                markup=False,
            )
            yield Static(
                "信任后角色提示可进入模型，并在父权限上限内请求工具。内容变化后会重新确认。",
                markup=False,
            )
            with Vertical(id="agent-trust-actions"):
                yield Button("1. 信任当前内容", id="trust")
                yield Button("2. 拒绝", id="deny", variant="error")

    def on_mount(self) -> None:
        self.query_one("#deny", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "trust")

    def action_trust(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
