from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import replace
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Input, Label, Static
from textual.worker import Worker, WorkerCancelled

from kcode import __version__
from kcode.config import AgentConfig
from kcode.conversation import AssistantMessage, Conversation, ToolResultMessage, UserMessage
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
from kcode.history.runtime import ResumeResult, SessionCoordinator
from kcode.mcp import McpManager
from kcode.mcp.trust import McpTrustRequest
from kcode.orchestration import AgentRunner
from kcode.permissions import (
    ApprovalChoice,
    LocalPermissionStore,
    PermissionEngine,
    PermissionMode,
    PermissionSettings,
    empty_permission_settings,
)
from kcode.prompting import SystemPromptBuilder
from kcode.providers.base import ChatProvider
from kcode.session import AgentSession
from kcode.tools.base import ApprovalRequest, ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import ToolRegistry, create_default_registry
from kcode.ui.approval import ApprovalScreen
from kcode.ui.commands import CommandKind, parse_command
from kcode.ui.mcp_trust import McpTrustScreen
from kcode.ui.resume import ResumeScreen
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
        mcp_manager: McpManager | None = None,
        prompt_builder: SystemPromptBuilder | None = None,
        coordinator: SessionCoordinator | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.coordinator = coordinator
        self.conversation = (
            coordinator.current.conversation
            if coordinator is not None
            else (conversation or Conversation())
        )
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
        self.mcp_manager = mcp_manager
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
            prompt_builder=prompt_builder,
            context_manager=(
                coordinator.current.context_manager if coordinator is not None else None
            ),
        )
        if coordinator is not None:
            self.runner.bind_session(coordinator.current)
        self.generating = False
        self._generation_worker: Worker[None] | None = None
        self._active_response: AssistantResponse | None = None
        self._tool_widgets: dict[str, ToolCallWidget] = {}
        self._iteration = 0
        self._usage = TokenUsage()
        self._coordinator_closed = False

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
            yield Input(
                placeholder="Send a message...",
                id="prompt",
                disabled=self.mcp_manager is not None,
            )
        with Horizontal(id="status"):
            yield Static(self._permission_status_text(), id="permission-status")
            yield Static(self._agent_status_text(), id="agent-status")
            yield Static(f"Model: {self.provider.model_name}", id="model-status")

    async def on_mount(self) -> None:
        for warning in self.startup_warnings:
            await self._append_notice(warning, "system")
        if self.mcp_manager is not None:
            self.query_one("#ready", Label).update("正在检查 MCP Server…")
            self.initialize_mcp()
        else:
            self.query_one("#prompt", Input).focus()

    async def on_unmount(self) -> None:
        if self.coordinator is not None and not self._coordinator_closed:
            warnings = await asyncio.shield(self.coordinator.close())
            for warning in warnings:
                print(f"KCode warning: {warning}", file=sys.stderr)
            self._coordinator_closed = True
        if self.mcp_manager is not None:
            await asyncio.shield(self.mcp_manager.close())

    async def _request_mcp_trust(self, request: McpTrustRequest) -> bool:
        return await self.push_screen_wait(McpTrustScreen(request))

    @work(exclusive=True, group="mcp-startup")
    async def initialize_mcp(self) -> None:
        assert self.mcp_manager is not None
        prompt = self.query_one("#prompt", Input)
        try:
            await self.mcp_manager.prepare(self._request_mcp_trust)
            summary = await self.mcp_manager.connect_all()
            for tool in summary.tools:
                try:
                    self.registry.register(tool)
                except ValueError as exc:
                    await self._append_notice(f"KCode ignored an MCP tool: {exc}", "system")
            self.context = replace(
                self.context,
                sensitive_values=tuple(
                    dict.fromkeys((*self.context.sensitive_values, *summary.sensitive_values))
                ),
            )
            self.runner.update_tool_context(self.context)
            if self.coordinator is not None:
                self.coordinator.update_sensitive_values(self.context.sensitive_values)
            for warning in summary.warnings:
                await self._append_notice(warning, "system")
            self.query_one("#ready", Label).update(summary.message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._append_notice(
                f"KCode could not initialize MCP; built-in tools remain available: "
                f"{exc.__class__.__name__}.",
                "error",
            )
            self.query_one("#ready", Label).update("Ready. MCP initialization failed.")
        finally:
            prompt.disabled = False
            prompt.focus()

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
                "命令：`/plan`、`/do`、`/compact`、`/help`、`/clear`、`/exit`、"
                "`/resume`、`/mcp trust clear`；Shift+Tab 切换权限模式。"
            )
        elif kind == CommandKind.CLEAR:
            clear_warnings: tuple[str, ...] = ()
            if self.coordinator is None:
                self.conversation.clear()
                await self.runner.clear_context()
            else:
                runtime, clear_warnings = await self.coordinator.clear()
                self.runner.bind_session(runtime)
                self.conversation = runtime.conversation
            self.session.clear()
            self._iteration = 0
            self._usage = TokenUsage()
            await self.query_one("#chat", VerticalScroll).remove_children()
            for warning in clear_warnings:
                await self._append_notice(warning, "error")
            self._set_permission_status()
            self._set_agent_status()
        elif kind == CommandKind.RESUME:
            self._resume_session()
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
            if self.coordinator is not None and not self._coordinator_closed:
                for warning in await self.coordinator.close():
                    await self._append_notice(warning, "error")
                self._coordinator_closed = True
            self.exit()
        elif kind == CommandKind.MCP_TRUST_CLEAR:
            if self.mcp_manager is None:
                await self._append_notice("当前项目没有配置 MCP Server。", "system")
            else:
                try:
                    removed = await asyncio.to_thread(
                        self.mcp_manager.trust_store.clear_project,
                        self.cwd,
                    )
                    message = (
                        "已清除当前项目的 MCP 信任；重启 KCode 后将重新确认。"
                        if removed
                        else "当前项目没有已保存的 MCP 信任。"
                    )
                    await self._append_notice(message, "system")
                except OSError:
                    await self._append_notice(
                        "无法安全清除 MCP 信任，请检查 ~/.kcode 目录权限。",
                        "error",
                    )
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

    @work(exclusive=True, group="resume")
    async def _resume_session(self) -> None:
        if self.coordinator is None:
            await self._append_notice("当前 App 没有启用会话持久化。", "error")
            return
        self._set_generating(True)
        self._set_agent_status("扫描会话")
        try:
            summaries = await asyncio.to_thread(self.coordinator.list_sessions)
            selected = await self.push_screen_wait(ResumeScreen(summaries))
            if selected is None:
                return
            self._set_agent_status("恢复会话")
            result = await self.coordinator.resume(
                selected,
                tools=self.runner.tool_definitions(self.session.permission_mode),
            )
            self.runner.bind_session(result.runtime)
            self.conversation = result.runtime.conversation
            await self._redraw_resumed_session(result)
        except Exception as exc:
            await self._append_notice(f"恢复会话失败：{exc}", "error")
        finally:
            self._set_agent_status()
            self._set_generating(False)

    async def _redraw_resumed_session(self, result: ResumeResult) -> None:
        chat = self.query_one("#chat", VerticalScroll)
        await chat.remove_children()
        self._tool_widgets.clear()
        for message in result.runtime.conversation.messages_snapshot():
            if isinstance(message, UserMessage):
                await chat.mount(ChatMessageWidget("user", message.content))
            elif isinstance(message, AssistantMessage):
                if message.tool_calls:
                    names = ", ".join(call.name for call in message.tool_calls)
                    await chat.mount(ChatMessageWidget("system", f"历史工具调用：{names}"))
                elif message.content.strip():
                    await chat.mount(ChatMessageWidget("assistant", message.content))
            elif isinstance(message, ToolResultMessage):
                await chat.mount(
                    ChatMessageWidget(
                        "system",
                        f"历史工具结果：{message.tool_name} · {message.result.status}",
                    )
                )
        for warning in result.warnings:
            await self._append_notice(warning, "system")
        self._iteration = 0
        self._usage = TokenUsage()
        self._set_agent_status()

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
