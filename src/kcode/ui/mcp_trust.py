from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from kcode.mcp.trust import McpTrustRequest


class McpTrustScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "deny", "Deny", show=False),
        Binding("1", "trust", "Trust", show=False),
        Binding("2", "deny", "Deny", show=False),
    ]
    CSS = """
    McpTrustScreen { align: center middle; background: $background 60%; }
    #mcp-trust-dialog {
        width: 82;
        height: auto;
        max-height: 85%;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    #mcp-trust-summary { height: auto; margin: 1 0; }
    #mcp-trust-actions { height: auto; }
    #mcp-trust-actions Button { width: 1fr; margin-top: 1; }
    """

    def __init__(self, request: McpTrustRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        variables = ", ".join(self.request.environment_variables) or "无"
        summary = (
            f"项目：{self.request.project_root}\n"
            f"Server：{self.request.server_name} ({self.request.server_type})\n"
            f"启动目标：{self.request.target}\n"
            f"引用的环境变量名称：{variables}"
        )
        with Vertical(id="mcp-trust-dialog"):
            yield Label("项目请求启动 MCP Server")
            yield Static(summary, id="mcp-trust-summary", markup=False)
            yield Static(
                "信任后 KCode 才会读取上述变量并启动进程或建立网络连接。",
                markup=False,
            )
            with Vertical(id="mcp-trust-actions"):
                yield Button("1. 信任当前配置", id="trust")
                yield Button("2. 拒绝", id="deny", variant="error")

    def on_mount(self) -> None:
        self.query_one("#deny", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "trust")

    def action_trust(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
