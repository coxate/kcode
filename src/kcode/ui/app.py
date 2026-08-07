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
from kcode.config import AgentConfig
from kcode.conversation import Conversation
from kcode.events import (
    AgentPhase,
    AgentProgress,
    AgentStopped,
    AgentStopReason,
    TextDelta,
    ThinkingDelta,
    TokenUsage,
    TokenUsageUpdated,
    ToolCallReady,
    ToolFinished,
    ToolStarted,
    TurnNotice,
)
from kcode.orchestration import AgentRunner
from kcode.permissions import (
    ApprovalChoice,
    LocalPermissionStore,
    PermissionEngine,
    PermissionMode,
    PermissionSettings,
    empty_permission_settings,
)
from kcode.providers.base import ChatProvider
from kcode.session import AgentSession
from kcode.tools.base import ApprovalRequest, ToolContext
from kcode.tools.executor import ToolExecutor
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
    #permission-status { width: 1fr; padding-left: 1; }
    #agent-status { width: auto; padding-right: 2; }
    #model-status { width: auto; padding-right: 1; }
    """
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Cancel / Exit", show=False, priority=True),
        Binding("shift+tab", "cycle_permissions", "Permissions", show=False, priority=True),
    ]

    def __init__(
        self,
        provider: ChatProvider,
        conversation: Conversation | None = None,
        *,
        warnings: tuple[str, ...] = (),
        cwd: Path | None = None,
        registry: ToolRegistry | None = None,
        context: ToolContext | None = None,
        agent_config: AgentConfig | None = None,
        session: AgentSession | None = None,
        permission_settings: PermissionSettings | None = None,
        permission_store: LocalPermissionStore | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.conversation = conversation or Conversation()
        self.startup_warnings = warnings
        self.cwd = (cwd or Path.cwd()).resolve()
        self.registry = registry or create_default_registry()
        self.context = context or ToolContext(self.cwd)
        self.agent_config = agent_config or AgentConfig()
        if permission_settings is None:
            permission_settings = empty_permission_settings(self.cwd)
        self.permission_settings = permission_settings
        self.permission_store = permission_store or LocalPermissionStore(
            permission_settings.layers[0].path
        )
        self.permission_engine = PermissionEngine(permission_settings)
        self.session = session or AgentSession(
            permission_settings.initial_mode,
            initial_mode=permission_settings.initial_mode,
        )
        self.runner = AgentRunner(
            provider,
            self.conversation,
            self.registry,
            ToolExecutor(self.registry, self.permission_engine, self.permission_store),
            self.context,
            self._request_approval,
            self.agent_config,
        )
        self.generating = False
        self._generation_worker: Worker[None] | None = None
        self._active_response: AssistantResponse | None = None
        self._tool_widgets: dict[str, ToolCallWidget] = {}
        self._iteration = 0
        self._usage = TokenUsage()

    def compose(self) -> ComposeResult:
        yield Static(
            CAT_BANNER.format(version=__version__, cwd=self.cwd),
            id="banner",
            markup=False,
        )
        yield Label("Ready. Ask me anything.", id="ready")
        yield VerticalScroll(id="chat")
        with Horizontal(id="prompt-area"):
            yield Static("❯", id="prompt-marker")
            yield Input(placeholder="Send a message...", id="prompt")
        with Horizontal(id="status"):
            yield Static(self._permission_status_text(), id="permission-status")
            yield Static(self._agent_status_text(), id="agent-status")
            yield Static(f"Model: {self.provider.model_name}", id="model-status")

    async def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        for warning in self.startup_warnings:
            await self._append_notice(warning, "system")

    def _agent_status_text(self, phase: str | None = None) -> str:
        iteration = (
            f" · {self._iteration}/{self.agent_config.max_iterations}" if self._iteration else ""
        )
        total = self._usage.total_tokens
        tokens = f" · Token {total}" if total is not None else " · Token ?"
        suffix = f" · {phase}" if phase else ""
        return f"Agent{iteration}{tokens}{suffix}"

    def _permission_status_text(self) -> str:
        return f"Permissions: {self.session.permission_mode.value}"

    def _set_permission_status(self) -> None:
        self.query_one("#permission-status", Static).update(self._permission_status_text())

    def _set_agent_status(self, phase: str | None = None) -> None:
        self.query_one("#agent-status", Static).update(self._agent_status_text(phase))

    async def _request_approval(self, request: ApprovalRequest) -> ApprovalChoice:
        self._set_agent_status("等待授权")
        try:
            choice = await self.push_screen_wait(ApprovalScreen(request))
            if choice is None:
                self.runner.cancel()
                return ApprovalChoice.DENY
            return choice
        finally:
            self._set_agent_status()

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
        self._iteration = 0
        self._usage = TokenUsage()
        self._set_agent_status()
        await self.query_one("#chat", VerticalScroll).mount(ChatMessageWidget("user", text))
        response = AssistantResponse()
        await self.query_one("#chat", VerticalScroll).mount(response)
        self._active_response = response
        self._set_generating(True)
        self._generation_worker = self.generate_response(text, response)

    async def _run_command(self, kind: CommandKind, raw: str) -> None:
        if kind == CommandKind.HELP:
            await self._append_notice(
                "命令：`/plan`、`/do`、`/compact`、`/help`、`/clear`、`/exit`；"
                "Shift+Tab 切换权限模式。"
            )
        elif kind == CommandKind.CLEAR:
            self.conversation.clear()
            await self.runner.clear_context()
            self.session.clear()
            self._iteration = 0
            self._usage = TokenUsage()
            await self.query_one("#chat", VerticalScroll).remove_children()
            self._set_permission_status()
            self._set_agent_status()
        elif kind == CommandKind.PLAN:
            self.session.set_mode(PermissionMode.PLAN)
            self._iteration = 0
            self._set_permission_status()
            self._set_agent_status()
            await self._append_notice("已进入 Plan Mode：只允许读取、查找、搜索和白名单只读命令。")
        elif kind == CommandKind.DO:
            has_plan = self.session.approve_plan()
            self._iteration = 0
            self._set_permission_status()
            self._set_agent_status()
            suffix = "，下一条请求将使用最新计划一次。" if has_plan else "。"
            await self._append_notice("已进入 Do Mode" + suffix)
        elif kind == CommandKind.COMPACT:
            self._set_generating(True)
            self._set_agent_status("压缩上下文")
            try:
                snapshot = await self.runner.compact_context(self.session)
                result = snapshot.compaction_result
                if result is None or not result.success:
                    detail = result.failure_reason if result is not None else "没有可压缩历史"
                    await self._append_notice(f"上下文压缩失败：{detail}", "error")
                else:
                    await self._append_notice(
                        "上下文压缩完成："
                        f"约 {result.before_tokens} → {result.after_tokens or '?'} Token；"
                        f"置信度 {snapshot.budget.confidence}；"
                        f"history_incomplete={str(result.history_incomplete).lower()}；"
                        f"Artifact {snapshot.offloaded_count} 个。",
                        "system",
                    )
            except Exception as exc:
                await self._append_notice(
                    f"上下文压缩失败：{exc.__class__.__name__}。",
                    "error",
                )
            finally:
                self._set_agent_status()
                self._set_generating(False)
        elif kind == CommandKind.EXIT:
            self.exit()
        else:
            await self._append_notice(f"未知命令：{raw}。输入 `/help` 查看帮助。", "error")

    def _set_generating(self, value: bool) -> None:
        self.generating = value
        # Markdown 流式更新会替换内部段落。若此时开始文本选择，Textual
        # 可能命中一个刚被移除的段落并在选区处理中崩溃。生成结束后恢复选择。
        self.ALLOW_SELECT = not value
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = value
        if not value:
            prompt.focus()

    @work(exclusive=True, group="generation")
    async def generate_response(self, user_text: str, response: AssistantResponse) -> None:
        answer = ""
        thinking = ""
        last_refresh = 0.0
        current_response = response
        try:
            async for event in self.runner.run(user_text, self.session):
                if isinstance(event, TextDelta):
                    answer += event.text
                elif isinstance(event, ThinkingDelta):
                    thinking += event.text
                elif isinstance(event, AgentProgress):
                    self._iteration = event.iteration
                    phase_labels = {
                        AgentPhase.MODEL: "模型生成",
                        AgentPhase.TOOLS: f"工具批次 {event.batch}",
                        AgentPhase.APPROVAL: "等待授权",
                        AgentPhase.COMPLETE: "完成",
                    }
                    self._set_agent_status(phase_labels[event.phase])
                    if event.phase == AgentPhase.MODEL:
                        if event.iteration == 1:
                            current_response.set_iteration(1)
                        else:
                            current_response.finish_thinking()
                            current_response = AssistantResponse(event.iteration)
                            await self.query_one("#chat", VerticalScroll).mount(current_response)
                            self._active_response = current_response
                            answer = ""
                            thinking = ""
                elif isinstance(event, TokenUsageUpdated):
                    self._usage = event.cumulative
                    self._set_agent_status()
                elif isinstance(event, ToolCallReady):
                    # 结构化工具调用出现后清除可能泄漏的临时协议文本。
                    answer = ""
                    current_response.update_answer("")
                    widget = ToolCallWidget(event.call)
                    self._tool_widgets[event.call.id] = widget
                    await self.query_one("#chat", VerticalScroll).mount(widget)
                elif isinstance(event, ToolStarted):
                    self._tool_widgets[event.call.id].set_running()
                elif isinstance(event, ToolFinished):
                    self._tool_widgets[event.call.id].set_result(event.result)
                elif isinstance(event, TurnNotice):
                    answer += f"\n\n**{event.message}**"
                elif isinstance(event, AgentStopped):
                    if event.reason != AgentStopReason.COMPLETED:
                        labels = {
                            AgentStopReason.ITERATION_LIMIT: "已达到迭代安全上限",
                            AgentStopReason.CANCELLED: "用户已取消当前任务",
                            AgentStopReason.UNKNOWN_TOOL_LIMIT: "连续请求未知工具",
                            AgentStopReason.STREAM_ERROR: "模型流出错",
                            AgentStopReason.INVALID_RESPONSE: "模型响应无效",
                        }
                        label = labels.get(event.reason, event.reason.value)
                        detail = event.detail.rstrip("。")
                        message = detail or label
                        if detail and event.reason in {
                            AgentStopReason.STREAM_ERROR,
                            AgentStopReason.INVALID_RESPONSE,
                        }:
                            message = f"{label}（{detail}）"
                        answer += f"\n\n*已停止：{message}。*"
                    self._set_agent_status(
                        "完成" if event.reason == AgentStopReason.COMPLETED else "已停止"
                    )
                now = time.monotonic()
                if now - last_refresh >= 1 / 30:
                    current_response.update_answer(answer)
                    current_response.update_thinking(thinking)
                    current_response.scroll_visible(animate=False)
                    last_refresh = now
                    await asyncio.sleep(0)
            current_response.update_answer(answer)
            current_response.update_thinking(thinking)
            current_response.finish_thinking()
        except (WorkerCancelled, asyncio.CancelledError):
            current_response.update_answer(answer + "\n\n*Cancelled.*")
            current_response.finish_thinking()
            raise
        except Exception:
            current_response.update_answer(
                answer + "\n\n**invalid_response:** Unexpected agent response."
            )
            current_response.finish_thinking()
        finally:
            self._active_response = None
            self._generation_worker = None
            self._set_generating(False)

    async def action_interrupt(self) -> None:
        if self.generating and self._generation_worker is not None:
            self.runner.cancel()
            if isinstance(self.screen, ApprovalScreen):
                self.screen.dismiss(None)
        else:
            self.exit()

    def action_cycle_permissions(self) -> None:
        self.session.cycle_mode()
        self._set_permission_status()
        self._set_agent_status()
