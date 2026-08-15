from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from kcode.hooks.catalog import HookTrustRequest


class HookTrustScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "deny", "Deny", show=False),
        Binding("1", "trust", "Trust", show=False),
        Binding("2", "deny", "Deny", show=False),
    ]
    CSS = """
    HookTrustScreen { align: center middle; background: $background 60%; }
    #hook-trust-dialog {
        width: 86; height: auto; max-height: 85%; padding: 1 2;
        border: round $error; background: $surface;
    }
    #hook-trust-summary { height: auto; margin: 1 0; }
    #hook-trust-actions { height: auto; }
    #hook-trust-actions Button { width: 1fr; margin-top: 1; }
    """

    def __init__(self, request: HookTrustRequest) -> None:
        super().__init__()
        self.request = request

    def compose(self) -> ComposeResult:
        ids = ", ".join(self.request.hook_ids) or "（未识别到合法 ID）"
        with Vertical(id="hook-trust-dialog"):
            yield Label("项目请求加载自动化 Hook")
            yield Static(
                f"项目：{self.request.project_root}\n配置：{self.request.config_path}\nHooks：{ids}",
                id="hook-trust-summary",
                markup=False,
            )
            yield Static(
                "信任后 Hook 可以运行本地命令、读取开发环境和文件，并向网络发送请求。"
                "请只信任你已审查的项目；内容变化后会重新确认。",
                markup=False,
            )
            with Vertical(id="hook-trust-actions"):
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
