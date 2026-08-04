from __future__ import annotations

import asyncio
import time
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Input, Label, Static
from textual.worker import Worker, WorkerCancelled

from kcode import __version__
from kcode.conversation import Conversation
from kcode.errors import ProviderError
from kcode.events import (
    StreamCompleted,
    TextDelta,
    ThinkingDelta,
    ToolCallReady,
    ToolFinished,
    ToolStarted,
    TurnNotice,
)
from kcode.orchestration import TurnRunner
from kcode.providers.base import ChatProvider
from kcode.tools.base import ApprovalRequest, ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.policy import ToolPolicy
from kcode.tools.registry import ToolRegistry, create_default_registry
from kcode.ui.approval import ApprovalScreen
from kcode.ui.commands import CommandKind, parse_command
from kcode.ui.widgets import AssistantResponse, ChatMessageWidget, ToolCallWidget

CAT_BANNER = r""" /\_/\
( o.o )   KCode v{version}
 > ^ <    {cwd}"""


class KCodeApp(App[None]):
    TITLE = "KCode"
    CSS = """
    Screen { layout: vertical; background: $surface; }
    #banner { height: 4; padding: 0 2; color: $accent; }
    #ready { height: 1; padding: 0 2; color: $text-muted; }
    #chat { height: 1fr; padding: 0 2; scrollbar-size: 1 1; }
    .message { height: auto; margin: 0 0 1 0; padding: 0 1; border-left: tall $primary; }
    .user { border-left: tall $secondary; }
    .system { border-left: tall $warning; color: $text-muted; }
    .error { border-left: tall $error; color: $error; }
    .tool { border-left: tall $warning; background: $boost; }
    .tool-success { color: $success; }
    .tool-error { color: $error; }
    .message-role { height: 1; text-style: bold; }
    .message-content, .tool-arguments, .tool-status { height: auto; }
    Collapsible { height: auto; padding: 0; }
    #prompt-area { height: 3; margin: 0 1; border: round $accent; align-vertical: middle; }
    #prompt-marker { width: 3; padding-left: 1; color: $accent; text-style: bold; }
    #prompt { width: 1fr; border: none; }
    #status { height: 1; dock: bottom; background: $primary-darken-2; color: $text; }
    #provider-status { width: 1fr; padding-left: 1; }
    #model-status { width: auto; padding-right: 1; }
    """
    BINDINGS = [Binding("ctrl+c", "interrupt", "Cancel / Exit", show=False)]

    def __init__(
        self,
        provider: ChatProvider,
        conversation: Conversation | None = None,
        *,
        warnings: tuple[str, ...] = (),
        cwd: Path | None = None,
        registry: ToolRegistry | None = None,
        context: ToolContext | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.conversation = conversation or Conversation()
        self.startup_warnings = warnings
        self.cwd = (cwd or Path.cwd()).resolve()
        self.registry = registry or create_default_registry()
        self.context = context or ToolContext(self.cwd)
        self.runner = TurnRunner(
            provider,
            self.conversation,
            self.registry,
            ToolExecutor(self.registry, ToolPolicy(self.cwd)),
            self.context,
            self._request_approval,
        )
        self.generating = False
        self._generation_worker: Worker[None] | None = None
        self._active_response: AssistantResponse | None = None
        self._tool_widgets: dict[str, ToolCallWidget] = {}

    def compose(self) -> ComposeResult:
        yield Static(CAT_BANNER.format(version=__version__, cwd=self.cwd), id="banner", markup=False)
        yield Label("Ready. Ask me anything.", id="ready")
        yield VerticalScroll(id="chat")
        with Horizontal(id="prompt-area"):
            yield Static("❯", id="prompt-marker")
            yield Input(placeholder="Send a message...", id="prompt")
        with Horizontal(id="status"):
            yield Static(f"Provider: {self.provider.display_name}", id="provider-status")
            yield Static(f"Model: {self.provider.model_name}", id="model-status")

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        for warning in self.startup_warnings:
            await self._append_notice(warning, "system")

    async def _request_approval(self, request: ApprovalRequest) -> bool:
        return await self.push_screen_wait(ApprovalScreen(request))

    async def _append_notice(self, text: str, style: str = "system") -> None:
        widget = ChatMessageWidget(style, text)
        await self.query_one("#chat", VerticalScroll).mount(widget)
        widget.scroll_visible(animate=False)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self.generating:
            return
        event.input.value = ""
        command = parse_command(text)
        if command is not None:
            await self._run_command(command.kind, command.raw)
            return
        await self.query_one("#chat", VerticalScroll).mount(ChatMessageWidget("user", text))
        response = AssistantResponse()
        await self.query_one("#chat", VerticalScroll).mount(response)
        self._active_response = response
        self._set_generating(True)
        self._generation_worker = self.generate_response(text, response)

    async def _run_command(self, kind: CommandKind, raw: str) -> None:
        if kind == CommandKind.HELP:
            await self._append_notice("Commands: `/help`, `/clear`, `/exit`")
        elif kind == CommandKind.CLEAR:
            self.conversation.clear()
            await self.query_one("#chat", VerticalScroll).remove_children()
        elif kind == CommandKind.EXIT:
            self.exit()
        else:
            await self._append_notice(f"Unknown command: {raw}. Try `/help`.", "error")

    def _set_generating(self, value: bool) -> None:
        self.generating = value
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = value
        if not value:
            prompt.focus()

    @work(exclusive=True, group="generation")
    async def generate_response(self, user_text: str, response: AssistantResponse) -> None:
        answer = ""
        thinking = ""
        last_refresh = 0.0
        try:
            async for event in self.runner.run(user_text):
                if isinstance(event, TextDelta):
                    answer += event.text
                elif isinstance(event, ThinkingDelta):
                    thinking += event.text
                elif isinstance(event, ToolCallReady):
                    # 首次请求只负责选择工具；检测到真实工具调用后，清除模型泄漏的临时协议文本。
                    answer = ""
                    response.update_answer("")
                    widget = ToolCallWidget(event.call)
                    self._tool_widgets[event.call.id] = widget
                    await self.query_one("#chat", VerticalScroll).mount(widget)
                elif isinstance(event, ToolStarted):
                    self._tool_widgets[event.call.id].set_running()
                elif isinstance(event, ToolFinished):
                    self._tool_widgets[event.call.id].set_result(event.result)
                elif isinstance(event, TurnNotice):
                    await self._append_notice(event.message, "error")
                now = time.monotonic()
                if now - last_refresh >= 1 / 30:
                    response.update_answer(answer)
                    response.update_thinking(thinking)
                    response.scroll_visible(animate=False)
                    last_refresh = now
                    await asyncio.sleep(0)
            response.update_answer(answer)
            response.update_thinking(thinking)
            response.finish_thinking()
        except (WorkerCancelled, asyncio.CancelledError):
            response.update_answer(answer + "\n\n*Cancelled.*")
            response.finish_thinking()
            raise
        except ProviderError as exc:
            response.update_answer(answer + f"\n\n**{exc.kind.value}:** {exc}")
            response.finish_thinking()
        except Exception:
            response.update_answer(answer + "\n\n**invalid_response:** Unexpected provider response.")
            response.finish_thinking()
        finally:
            self._active_response = None
            self._generation_worker = None
            self._set_generating(False)

    async def action_interrupt(self) -> None:
        if self.generating and self._generation_worker is not None:
            self._generation_worker.cancel()
        else:
            self.exit()
