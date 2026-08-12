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
from textual.widgets import Input, Label, OptionList, Static
from textual.worker import Worker, WorkerCancelled

from kcode import __version__
from kcode.commands import (
    CommandDispatcher,
    CommandRegistry,
    MemoryInventory,
    SessionInfo,
    StatusSnapshot,
    create_builtin_registry,
    register_skill_commands,
)
from kcode.config import AgentConfig, ProviderConfig, SubAgentConfig, TeamConfig
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
from kcode.hooks import (
    HookCatalogBuilder,
    HookContext,
    HookEngine,
    HookEvent,
    HookTrustStore,
)
from kcode.mcp import McpManager
from kcode.mcp.trust import McpTrustRequest
from kcode.memory.models import MemoryDecision, MemoryScope, MemoryStatus
from kcode.memory.runtime import MemoryCoordinator
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
from kcode.skills import (
    LoadSkillTool,
    SkillCatalogBuilder,
    SkillExecutor,
    SkillMode,
    SkillRuntime,
    SkillTrustStore,
)
from kcode.skills.executor import SkillInvocation
from kcode.subagents import (
    AgentCatalog,
    AgentCatalogBuilder,
    AgentTrustStore,
    ProviderPool,
    SubAgentFactory,
    SubAgentService,
    TaskManager,
    register_subagent_tools,
)
from kcode.subagents.approval import ApprovalBroker
from kcode.teams import TeamCaller, TeamError
from kcode.teams.manager import TeamManager
from kcode.teams.tools import register_team_tools
from kcode.tools.base import ApprovalRequest, ToolContext
from kcode.tools.executor import ToolExecutor
from kcode.tools.registry import ToolRegistry, create_default_registry
from kcode.ui.agent_trust import AgentTrustScreen
from kcode.ui.approval import ApprovalScreen
from kcode.ui.command_menu import CommandMenu
from kcode.ui.hook_trust import HookTrustScreen
from kcode.ui.mcp_trust import McpTrustScreen
from kcode.ui.memory import (
    MemoryDeleteScreen,
    MemoryEditScreen,
    MemoryReviewScreen,
    MemoryScreen,
)
from kcode.ui.resume import ResumeScreen
from kcode.ui.skill_trust import SkillTrustScreen
from kcode.ui.widgets import AssistantResponse, ChatMessageWidget, ToolCallWidget
from kcode.worktrees import WorktreeError, WorktreeManager, WorktreeStatus

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
    #command-menu {
        display: none;
        height: auto;
        max-height: 6;
        margin: 0 1;
        border: round $accent;
        background: $surface;
        scrollbar-size: 1 1;
    }
    #status { height: 1; dock: bottom; background: $primary-darken-2; color: $text; }
    #permission-status { width: 1fr; padding-left: 1; }
    #agent-status { width: auto; padding-right: 2; }
    #model-status { width: auto; padding-right: 1; }
    """
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Cancel / Exit", show=False, priority=True),
        Binding("shift+tab", "cycle_permissions", "Permissions", show=False, priority=True),
        Binding("ctrl+m", "memory", "Memory", show=False, priority=True),
        Binding("up", "command_menu_up", "Previous command", show=False, priority=True),
        Binding("down", "command_menu_down", "Next command", show=False, priority=True),
        Binding("tab", "command_menu_complete", "Complete command", show=False, priority=True),
        Binding("escape", "escape", "Close / Background", show=False, priority=True),
    ]

    def __init__(
        self,
        provider: ChatProvider,
        conversation: Conversation | None = None,
        *,
        warnings: tuple[str, ...] = (),
        cwd: Path | None = None,
        registry: ToolRegistry | None = None,
        command_registry: CommandRegistry | None = None,
        context: ToolContext | None = None,
        agent_config: AgentConfig | None = None,
        session: AgentSession | None = None,
        permission_settings: PermissionSettings | None = None,
        permission_store: LocalPermissionStore | None = None,
        mcp_manager: McpManager | None = None,
        prompt_builder: SystemPromptBuilder | None = None,
        coordinator: SessionCoordinator | None = None,
        memory_coordinator: MemoryCoordinator | None = None,
        skill_builder: SkillCatalogBuilder | None = None,
        skill_trust_store: SkillTrustStore | None = None,
        hook_builder: HookCatalogBuilder | None = None,
        hook_trust_store: HookTrustStore | None = None,
        hook_engine: HookEngine | None = None,
        provider_configs: dict[str, ProviderConfig] | None = None,
        subagent_config: SubAgentConfig | None = None,
        agent_builder: AgentCatalogBuilder | None = None,
        agent_trust_store: AgentTrustStore | None = None,
        worktree_manager: WorktreeManager | None = None,
        team_config: TeamConfig | None = None,
        team_manager: TeamManager | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.coordinator = coordinator
        self.memory_coordinator = memory_coordinator
        self.conversation = (
            coordinator.current.conversation
            if coordinator is not None
            else (conversation or Conversation())
        )
        self.startup_warnings = warnings
        self.cwd = (cwd or Path.cwd()).resolve()
        self.registry = registry or create_default_registry()
        self.command_registry = command_registry or create_builtin_registry(freeze=False)
        self.command_dispatcher = CommandDispatcher(self.command_registry)
        self.context = context or ToolContext(self.cwd)
        self.agent_config = agent_config or AgentConfig()
        if permission_settings is None:
            permission_settings = empty_permission_settings(self.cwd)
        self.permission_settings = permission_settings
        self.permission_store = permission_store or LocalPermissionStore(
            permission_settings.layers[0].path
        )
        self.mcp_manager = mcp_manager
        self.skill_builder = skill_builder or SkillCatalogBuilder(self.cwd)
        self.skill_trust_store = skill_trust_store or SkillTrustStore()
        self.hook_builder = hook_builder or HookCatalogBuilder(self.cwd)
        self.hook_trust_store = hook_trust_store or HookTrustStore()
        self.hook_engine = hook_engine or HookEngine()
        self.agent_builder = agent_builder or AgentCatalogBuilder(self.cwd)
        self.agent_trust_store = agent_trust_store or AgentTrustStore()
        self.subagent_config = subagent_config or SubAgentConfig()
        self.worktree_manager = worktree_manager or WorktreeManager(self.cwd)
        self.team_config = team_config or TeamConfig()
        self.skill_runtime = SkillRuntime()
        if self.registry.get("load_skill") is None:
            self.registry.register(LoadSkillTool(self.skill_runtime))
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
        if memory_coordinator is not None:
            self.runner.bind_memory(memory_coordinator)
        self.runner.bind_skills(self.skill_runtime)
        self.runner.bind_hooks(
            self.hook_engine,
            coordinator.current if coordinator is not None else None,
        )
        configs = provider_configs or {}
        provider_config = getattr(provider, "config", None)
        if isinstance(provider_config, ProviderConfig):
            configs = {**configs, provider_config.name: provider_config}
        self.provider_pool = ProviderPool(provider, configs)
        self.subagent_factory = SubAgentFactory(self.provider_pool)
        self.approval_broker = ApprovalBroker(self._request_approval)
        self._subagent_notices: list[str] = []
        self.task_manager = TaskManager(
            self.subagent_config,
            self.approval_broker,
            sensitive_values=self.context.sensitive_values,
            usage_callback=self._record_subagent_usage,
            notice_callback=self._subagent_notices.append,
        )
        self.subagent_service = SubAgentService(
            AgentCatalog(),
            self.subagent_factory,
            self.task_manager,
            self.runner,
            self.subagent_config,
            self.worktree_manager,
        )
        register_subagent_tools(self.registry, self.subagent_service)
        self.team_manager = team_manager or TeamManager(
            self.team_config,
            self.subagent_config,
            AgentCatalog(),
            self.subagent_factory,
            self.task_manager,
            self.runner,
            self.worktree_manager,
            sensitive_values=self.context.sensitive_values,
        )
        register_team_tools(self.registry, self.team_manager)
        self.runner.bind_team_messages(self.team_manager.lead_message_source())
        self.runner.bind_task_notifications(self.task_manager)
        self.hook_engine.bind_agent_launcher(self.subagent_service)
        self.skill_executor = SkillExecutor(self.skill_runtime, self.subagent_factory)
        self.hook_engine.update_sensitive_values(self.context.sensitive_values)
        self.generating = False
        self._generation_worker: Worker[None] | None = None
        self._active_response: AssistantResponse | None = None
        self._tool_widgets: dict[str, ToolCallWidget] = {}
        self._iteration = 0
        self._usage = TokenUsage()
        self._main_request_usage = TokenUsage()
        self._subagent_request_usage = TokenUsage(0, 0, 0, 0, 0)
        self._session_usage = TokenUsage(0, 0, 0, 0, 0)
        self._coordinator_closed = False
        self._memory_closed = False
        self._startup_complete = False
        self._hook_session_ended = False
        self._hook_shutdown = False
        self._hook_closed = False

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
                disabled=True,
            )
        yield CommandMenu(self.command_registry)
        with Horizontal(id="status"):
            yield Static(self._permission_status_text(), id="permission-status")
            yield Static(self._agent_status_text(), id="agent-status")
            yield Static(f"Model: {self.provider.model_name}", id="model-status")

    async def on_mount(self) -> None:
        for warning in self.startup_warnings:
            await self._append_notice(warning, "system")
        self.query_one("#ready", Label).update("正在检查 Skills、Hooks、Agents 与 MCP Server…")
        self.initialize_mcp()
        if self.memory_coordinator is not None:
            self.monitor_memory()
        self.monitor_hook_warnings()
        self.monitor_subagents()
        self.monitor_subagent_approvals()

    async def on_unmount(self) -> None:
        for warning in await self.team_manager.close():
            print(f"KCode warning: {warning}", file=sys.stderr)
        await self.task_manager.close()
        await self._close_hooks("exit", display=False)
        if self.coordinator is not None and not self._coordinator_closed:
            warnings = await asyncio.shield(self.coordinator.close())
            for warning in warnings:
                print(f"KCode warning: {warning}", file=sys.stderr)
            self._coordinator_closed = True
        if self.memory_coordinator is not None and not self._memory_closed:
            warnings = await asyncio.shield(self.memory_coordinator.close())
            for warning in warnings:
                print(f"KCode warning: {warning}", file=sys.stderr)
            self._memory_closed = True
        if self.mcp_manager is not None:
            await asyncio.shield(self.mcp_manager.close())

    async def _request_hook_trust(self, request) -> bool:
        return await self.push_screen_wait(HookTrustScreen(request))

    def _hook_context(
        self,
        event: HookEvent,
        *,
        message: str = "",
        error: str = "",
        command: str = "",
    ) -> HookContext:
        session_id = (
            self.coordinator.current.session_id if self.coordinator is not None else "in-memory"
        )
        return HookContext(
            event,
            session_id,
            self.cwd,
            self.session.permission_mode,
            message=message,
            error=error,
            command=command,
        )

    async def _dispatch_app_hook(
        self,
        event: HookEvent,
        *,
        message: str = "",
        error: str = "",
        command: str = "",
        display: bool = True,
    ) -> None:
        result = await self.hook_engine.run_hooks(
            self._hook_context(event, message=message, error=error, command=command),
            self.runner.hook_session,
        )
        for warning in result.warnings:
            if display:
                await self._append_notice(warning.render(), "system")
            else:
                print(f"KCode warning: {warning.render()}", file=sys.stderr)

    async def _end_hook_session(self, reason: str, *, display: bool = True) -> None:
        if self._hook_session_ended:
            return
        await self._dispatch_app_hook(
            HookEvent.SESSION_END,
            message=reason,
            display=display,
        )
        self._hook_session_ended = True

    async def _start_hook_session(self, reason: str) -> None:
        self._hook_session_ended = False
        await self._dispatch_app_hook(HookEvent.SESSION_START, message=reason)

    async def _close_hooks(self, reason: str, *, display: bool) -> None:
        if self._hook_closed:
            return
        await self._end_hook_session(reason, display=display)
        if not self._hook_shutdown:
            await self._dispatch_app_hook(
                HookEvent.SHUTDOWN,
                message=reason,
                display=display,
            )
            self._hook_shutdown = True
        for warning in await self.hook_engine.close():
            if display:
                await self._append_notice(warning.render(), "system")
            else:
                print(f"KCode warning: {warning.render()}", file=sys.stderr)
        self._hook_closed = True

    @work(exclusive=True, group="hook-warning-monitor")
    async def monitor_hook_warnings(self) -> None:
        while not self._hook_closed:
            await asyncio.sleep(0.1)
            for warning in self.hook_engine.runtime.drain_warnings():
                await self._append_notice(warning.render(), "system")

    async def _request_mcp_trust(self, request: McpTrustRequest) -> bool:
        return await self.push_screen_wait(McpTrustScreen(request))

    async def _request_skill_trust(self, request) -> bool:
        return await self.push_screen_wait(SkillTrustScreen(request))

    async def _request_agent_trust(self, request) -> bool:
        return await self.push_screen_wait(AgentTrustScreen(request))

    async def _connect_mcp_tools(self) -> str:
        if self.mcp_manager is None:
            return "Ready. Ask me anything."
        try:
            await self.mcp_manager.prepare(self._request_mcp_trust)
            summary = await self.mcp_manager.connect_all()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._append_notice(
                "MCP initialization failed; Skills, Hooks, and Agents remain available: "
                f"{exc.__class__.__name__}.",
                "error",
            )
            return "Ready. MCP initialization failed; Skills/Hooks/Agents are available."
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
        self.task_manager.sensitive_values = self.context.sensitive_values
        if self.coordinator is not None:
            self.coordinator.update_sensitive_values(self.context.sensitive_values)
        if self.memory_coordinator is not None:
            self.memory_coordinator.update_sensitive_values(self.context.sensitive_values)
        for warning in summary.warnings:
            await self._append_notice(warning, "system")
        return summary.message

    @work(exclusive=True, group="mcp-startup")
    async def initialize_mcp(self) -> None:
        prompt = self.query_one("#prompt", Input)
        project_trusted = False
        project_hooks_trusted = False
        project_agents_trusted = False
        try:
            request, trust_warnings = self.skill_builder.trust_request()
            for warning in trust_warnings:
                await self._append_notice(warning, "system")
            if request is not None:
                project_trusted = self.skill_trust_store.is_trusted(request)
                if not project_trusted:
                    project_trusted = await self._request_skill_trust(request)
                    if project_trusted:
                        try:
                            await asyncio.to_thread(self.skill_trust_store.trust, request)
                        except (OSError, ValueError):
                            project_trusted = False
                            await self._append_notice(
                                "无法安全保存项目 Skill 信任；已跳过项目 Skills。",
                                "error",
                            )
            hook_request, hook_trust_warnings = self.hook_builder.trust_request()
            for warning in hook_trust_warnings:
                await self._append_notice(warning.render(), "system")
            if hook_request is not None:
                project_hooks_trusted = self.hook_trust_store.is_trusted(hook_request)
                if not project_hooks_trusted:
                    project_hooks_trusted = await self._request_hook_trust(hook_request)
                    if project_hooks_trusted:
                        try:
                            await asyncio.to_thread(
                                self.hook_trust_store.trust,
                                hook_request,
                            )
                        except (OSError, ValueError):
                            project_hooks_trusted = False
                            await self._append_notice(
                                "无法安全保存项目 Hook 信任；已跳过项目 Hooks。",
                                "error",
                            )
            if self.subagent_config.enabled:
                agent_request, agent_trust_warnings = self.agent_builder.trust_request()
            else:
                agent_request, agent_trust_warnings = None, ()
            for warning in agent_trust_warnings:
                await self._append_notice(warning, "system")
            if agent_request is not None:
                project_agents_trusted = self.agent_trust_store.is_trusted(agent_request)
                if not project_agents_trusted:
                    project_agents_trusted = await self._request_agent_trust(agent_request)
                    if project_agents_trusted:
                        try:
                            await asyncio.to_thread(
                                self.agent_trust_store.trust,
                                agent_request,
                            )
                        except (OSError, ValueError):
                            project_agents_trusted = False
                            await self._append_notice(
                                "无法安全保存项目 Agent 信任；已跳过项目 Agents。",
                                "error",
                            )
            ready_message = await self._connect_mcp_tools()
            catalog = self.skill_builder.build(
                project_trusted=project_trusted,
                tool_names=self.registry.names(),
                command_names=self.command_registry.registered_names(),
            )
            self.skill_runtime.set_catalog(catalog)
            self.runner.update_available_skills(catalog.available_prompt())
            agent_catalog = (
                self.agent_builder.build(
                    project_trusted=project_agents_trusted,
                    tool_names=self.registry.names(),
                    provider_names=self.provider_pool.names,
                )
                if self.subagent_config.enabled
                else AgentCatalog()
            )
            self.subagent_service.set_catalog(agent_catalog)
            self.team_manager.set_catalog(agent_catalog)
            self.runner.update_available_agents(agent_catalog.available_prompt())
            hook_catalog = self.hook_builder.build(project_trusted=project_hooks_trusted)
            self.hook_engine.set_catalog(hook_catalog)
            register_skill_commands(self.command_registry, catalog.summaries())
            self.command_registry.freeze()
            for warning in catalog.warnings:
                await self._append_notice(warning, "system")
            for warning in hook_catalog.warnings:
                await self._append_notice(warning.render(), "system")
            for warning in agent_catalog.warnings:
                await self._append_notice(warning, "system")
            await self._dispatch_app_hook(HookEvent.STARTUP, message="ready")
            await self._start_hook_session("initial")
            self.query_one("#ready", Label).update(ready_message)
            self._startup_complete = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._append_notice(
                f"KCode could not finish startup; built-in tools remain available: "
                f"{exc.__class__.__name__}.",
                "error",
            )
            if not self.command_registry.frozen:
                self.command_registry.freeze()
            self.query_one("#ready", Label).update(
                "Ready. Skill/Hook/Agent/MCP initialization failed."
            )
        finally:
            prompt.disabled = False
            prompt.focus()
            if self.memory_coordinator is not None and self.memory_coordinator.pending():
                self.review_memories()

    def _agent_status_text(self, phase: str | None = None) -> str:
        iteration = (
            f" · {self._iteration}/{self.agent_config.max_iterations}" if self._iteration else ""
        )
        total = self._usage.total_tokens
        tokens = f" · Token {total}" if total is not None else " · Token ?"
        suffix = f" · {phase}" if phase else ""
        tasks = ""
        if self.task_manager.running_count or self.task_manager.waiting_approval_count:
            tasks = (
                f" · 子任务 {self.task_manager.running_count}"
                f"/授权 {self.task_manager.waiting_approval_count}"
            )
        return f"Agent{iteration}{tokens}{tasks}{suffix}"

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
                if request.task_id is None:
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
        menu = self.query_one("#command-menu", CommandMenu)
        if menu.display:
            selected = menu.selected_name()
            if selected is not None:
                text = f"/{selected}"
            menu.close()
        event.input.value = ""
        if await self.command_dispatcher.dispatch(text, self):
            return
        await self._submit_user_text(text)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "prompt" and not self.generating:
            self.query_one("#command-menu", CommandMenu).update_query(event.value)

    def _command_menu_active(self) -> bool:
        if len(self.screen_stack) != 1:
            return False
        prompt = self.query_one("#prompt", Input)
        return self.focused is prompt and self.query_one("#command-menu").display

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action.startswith("command_menu_"):
            return self._command_menu_active()
        return super().check_action(action, parameters)

    def action_command_menu_up(self) -> None:
        self.query_one("#command-menu", OptionList).action_cursor_up()

    def action_command_menu_down(self) -> None:
        self.query_one("#command-menu", OptionList).action_cursor_down()

    def action_command_menu_complete(self) -> None:
        menu = self.query_one("#command-menu", CommandMenu)
        selected = menu.selected_name()
        if selected is not None:
            prompt = self.query_one("#prompt", Input)
            prompt.value = f"/{selected} "
            prompt.cursor_position = len(prompt.value)
        menu.close()

    def action_command_menu_close(self) -> None:
        self.query_one("#command-menu", CommandMenu).close()

    async def action_escape(self) -> None:
        if len(self.screen_stack) > 1:
            self.screen.dismiss(None)
            return
        menu = self.query_one("#command-menu", CommandMenu)
        if menu.display:
            menu.close()
            return
        if self.task_manager.detach_foreground():
            await self._append_notice(
                "前台 SubAgent 已转到后台；完成后会通知主 Agent。",
                "system",
            )
            self._set_agent_status()

    async def _submit_user_text(self, text: str, display_text: str | None = None) -> None:
        self._iteration = 0
        self._usage = TokenUsage()
        self._main_request_usage = TokenUsage()
        self._subagent_request_usage = TokenUsage(0, 0, 0, 0, 0)
        self._set_agent_status()
        await self.query_one("#chat", VerticalScroll).mount(
            ChatMessageWidget("user", display_text or text)
        )
        response = AssistantResponse()
        await self.query_one("#chat", VerticalScroll).mount(response)
        self._active_response = response
        self._set_generating(True)
        self._generation_worker = self.generate_response(text, response)

    async def command_notice(self, text: str, style: str = "system") -> None:
        await self._append_notice(text, style)

    async def command_submit_user(
        self,
        text: str,
        display_text: str | None = None,
    ) -> None:
        await self._submit_user_text(text, display_text)

    def command_skills(self):
        return self.skill_runtime.catalog.summaries()

    def command_hooks(self):
        return self.hook_engine.summaries()

    async def command_hook_execute(self, name: str, args: str, command_type) -> None:
        await self._dispatch_app_hook(
            HookEvent.COMMAND_EXECUTE,
            command=name,
            message=args,
        )

    async def command_hook_error(self, name: str, error_type: str) -> None:
        await self._dispatch_app_hook(
            HookEvent.ERROR,
            command=name,
            message="command",
            error=error_type,
        )

    async def command_execute_skill(self, name: str, args: str) -> None:
        try:
            invocation = self.skill_executor.prepare(name, args)
        except ValueError as exc:
            await self._append_notice(str(exc), "error")
            return
        for warning in invocation.warnings:
            await self._append_notice(warning, "system")
        if invocation.mode is SkillMode.INLINE:
            await self._submit_user_text(invocation.prompt, invocation.display_text)
            return
        await self._submit_fork(invocation)

    async def _submit_fork(self, invocation: SkillInvocation) -> None:
        self._iteration = 0
        self._usage = TokenUsage()
        self._main_request_usage = TokenUsage()
        self._subagent_request_usage = TokenUsage(0, 0, 0, 0, 0)
        self._set_agent_status()
        await self.query_one("#chat", VerticalScroll).mount(
            ChatMessageWidget("user", invocation.display_text)
        )
        response = AssistantResponse()
        await self.query_one("#chat", VerticalScroll).mount(response)
        self._active_response = response
        self._set_generating(True)
        events = self.skill_executor.run_fork(invocation, self.runner, self.session)
        self._generation_worker = self.generate_response("", response, event_source=events)

    def command_enter_plan(self) -> None:
        self.session.set_mode(PermissionMode.PLAN)
        self._iteration = 0
        self._set_permission_status()
        self._set_agent_status()

    def command_enter_do(self) -> bool:
        has_plan = self.session.approve_plan()
        self._iteration = 0
        self._set_permission_status()
        self._set_agent_status()
        return has_plan

    async def command_compact(self, focus: str | None) -> None:
        self._set_generating(True)
        self._set_agent_status("压缩上下文")
        try:
            snapshot = await self.runner.compact_context(self.session, focus=focus)
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
                    f"Artifact {snapshot.offloaded_count} 个。"
                )
        finally:
            self._set_agent_status()
            self._set_generating(False)

    async def command_clear(self) -> None:
        clear_warnings: tuple[str, ...] = ()
        skill_warnings: tuple[str, ...] = ()
        await self._end_hook_session("clear")
        if self.coordinator is None:
            self.conversation.clear()
            await self.runner.clear_context()
            self.skill_runtime.restore(())
        else:
            runtime, clear_warnings = await self.coordinator.clear()
            skill_warnings = self.runner.bind_session(runtime)
            self.conversation = runtime.conversation
        self.session.clear()
        self._iteration = 0
        self._usage = TokenUsage()
        self._main_request_usage = TokenUsage()
        self._subagent_request_usage = TokenUsage(0, 0, 0, 0, 0)
        self._session_usage = TokenUsage(0, 0, 0, 0, 0)
        await self._start_hook_session("clear")
        await self.query_one("#chat", VerticalScroll).remove_children()
        await self._append_notice("当前会话已清空。")
        for warning in clear_warnings:
            await self._append_notice(warning, "error")
        for warning in skill_warnings:
            await self._append_notice(warning, "system")
        self._set_permission_status()
        self._set_agent_status()

    def command_resume(self) -> None:
        self._resume_session()

    async def command_exit(self) -> None:
        for warning in await self.team_manager.close():
            await self._append_notice(warning, "error")
        await self.task_manager.close()
        await self._close_hooks("exit", display=True)
        if self.coordinator is not None and not self._coordinator_closed:
            for warning in await self.coordinator.close():
                await self._append_notice(warning, "error")
            self._coordinator_closed = True
        if self.memory_coordinator is not None and not self._memory_closed:
            for warning in await self.memory_coordinator.close():
                await self._append_notice(warning, "error")
            self._memory_closed = True
        self.exit()

    async def command_clear_mcp_trust(self) -> None:
        if self.mcp_manager is None:
            await self._append_notice("当前项目没有配置 MCP Server。")
            return
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
            await self._append_notice(message)
        except OSError:
            await self._append_notice("无法安全清除 MCP 信任，请检查 ~/.kcode 目录权限。", "error")

    def command_status(self) -> StatusSnapshot:
        memory_count = (
            len(self.memory_coordinator.records()) if self.memory_coordinator is not None else None
        )
        return StatusSnapshot(
            mode=self.session.permission_mode.value,
            input_tokens=self._session_usage.input_tokens,
            output_tokens=self._session_usage.output_tokens,
            tool_count=len(self.registry),
            memory_count=memory_count,
            model=self.provider.model_name,
            cwd=str(self.cwd),
        )

    def command_memories(self) -> MemoryInventory:
        if self.memory_coordinator is None:
            return MemoryInventory(False)
        records = self.memory_coordinator.records()
        return MemoryInventory(
            True,
            tuple(sorted(record.id for record in records if record.scope is MemoryScope.USER)),
            tuple(sorted(record.id for record in records if record.scope is MemoryScope.PROJECT)),
        )

    def command_session(self) -> SessionInfo:
        if self.coordinator is None:
            return SessionInfo(False)
        runtime = self.coordinator.current
        return SessionInfo(True, runtime.session_id, str(runtime.journal.path))

    async def command_worktree_create(self, name: str) -> None:
        try:
            record, warnings = await self.worktree_manager.create_manual(name)
        except WorktreeError as exc:
            await self._append_notice(str(exc), "error")
            return
        await self._append_notice(
            "\n".join(
                (
                    "Worktree 已创建。",
                    f"名称：{record.name}",
                    f"路径：{record.path}",
                    f"分支：{record.branch}",
                    f"基线：{record.base_commit}",
                    *(f"警告：{warning}" for warning in warnings),
                )
            )
        )

    @staticmethod
    def _worktree_status_text(status: WorktreeStatus) -> str:
        def known(value: object | None) -> str:
            if value is None:
                return "unknown"
            if isinstance(value, bool):
                return str(value).lower()
            return str(value)

        return "\n".join(
            (
                f"名称：{status.path.name}",
                f"路径：{status.path}",
                f"分支：{known(status.branch)}",
                f"基线：{known(status.record.base_commit if status.record else None)}",
                f"HEAD：{known(status.head_commit)}",
                f"dirty：{known(status.dirty)}",
                f"新 commit：{known(status.head_changed)}",
                f"托管：{known(status.managed)}",
                f"可安全删除：{known(status.removable)}",
                *(f"警告：{warning}" for warning in status.warnings),
            )
        )

    async def command_worktree_list(self) -> None:
        try:
            statuses = await self.worktree_manager.list()
        except WorktreeError as exc:
            await self._append_notice(str(exc), "error")
            return
        if not statuses:
            await self._append_notice("当前仓库没有 Kcode Worktree。")
            return
        await self._append_notice(
            "\n\n".join(self._worktree_status_text(item) for item in statuses)
        )

    async def command_worktree_status(self, name: str) -> None:
        try:
            status = await self.worktree_manager.status(name)
        except WorktreeError as exc:
            await self._append_notice(str(exc), "error")
            return
        await self._append_notice(self._worktree_status_text(status))

    async def command_worktree_remove(self, name: str) -> None:
        try:
            report = await self.worktree_manager.remove_manual(name)
        except WorktreeError as exc:
            await self._append_notice(str(exc), "error")
            return
        await self._append_notice(report.render(), "system" if not report.kept else "error")

    async def command_team_status(self) -> None:
        try:
            result = await self.team_manager.status(TeamCaller.lead())
        except TeamError as exc:
            await self._append_notice(str(exc), "error")
            return
        data = result.data
        lines = [
            f"Team：{data['name']}",
            f"目标：{data['goal']}",
            f"任务：{data['tasks']}",
        ]
        for member in data["members"]:
            lines.append(
                f"成员：{member['name']} · {member['status']} · {member['isolation']} · "
                f"Token {member['tokens']} · Worktree {member['worktree']}"
            )
        await self._append_notice("\n".join(lines))

    async def command_team_stop(self, name: str) -> None:
        try:
            result = await self.team_manager.stop(TeamCaller.lead(), name)
        except TeamError as exc:
            await self._append_notice(str(exc), "error")
            return
        await self._append_notice(
            f"成员 {name}：{result.data['status']}\nWorktree：{result.data.get('worktree')}"
        )

    async def command_team_delete(self) -> None:
        try:
            result = await self.team_manager.delete(TeamCaller.lead())
        except TeamError as exc:
            await self._append_notice(str(exc), "error")
            return
        await self._append_notice(f"Team 已删除。保留报告：{result.data['worktrees']}")

    def _set_generating(self, value: bool) -> None:
        self.generating = value
        # Markdown 流式更新会替换内部段落。若此时开始文本选择，Textual
        # 可能命中一个刚被移除的段落并在选区处理中崩溃。生成结束后恢复选择。
        self.ALLOW_SELECT = not value
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = value
        if not value:
            prompt.focus()

    def _record_subagent_usage(self, usage: TokenUsage) -> None:
        """Count child usage in the session UI, not in the main context anchor."""
        self._subagent_request_usage = self._subagent_request_usage.plus(usage)
        self._usage = self._main_request_usage.plus(self._subagent_request_usage)
        self._session_usage = self._session_usage.plus(usage)
        if self.is_mounted:
            self._set_agent_status()

    @work(exclusive=True, group="subagent-monitor")
    async def monitor_subagents(self) -> None:
        while not self.task_manager.closed:
            await asyncio.sleep(0.1)
            notices = tuple(self._subagent_notices)
            self._subagent_notices.clear()
            for notice in notices:
                await self._append_notice(notice, "system")
            if notices:
                self._set_agent_status()

    @work(exclusive=True, group="subagent-approval-monitor")
    async def monitor_subagent_approvals(self) -> None:
        while not self.task_manager.closed:
            ticket = await self.approval_broker.next_ticket()
            while (
                self.generating
                and not self.task_manager.closed
                and self.approval_broker.is_pending(ticket.id)
            ):
                await asyncio.sleep(0.1)
            if not self.approval_broker.is_pending(ticket.id):
                continue
            if self.task_manager.closed:
                self.approval_broker.resolve(ticket.id, ApprovalChoice.DENY)
                return
            choice = await self.push_screen_wait(ApprovalScreen(ticket.request))
            self.approval_broker.resolve(ticket.id, choice or ApprovalChoice.DENY)
            self._set_agent_status()

    @work(exclusive=True, group="memory-monitor")
    async def monitor_memory(self) -> None:
        assert self.memory_coordinator is not None
        while True:
            await self.memory_coordinator.next_proposal()
            if not self.generating:
                self.review_memories()

    @work(exclusive=True, group="memory-review")
    async def review_memories(self) -> None:
        if self.memory_coordinator is None or self.generating:
            return
        while self.memory_coordinator.pending() and not self.generating:
            proposal = self.memory_coordinator.pending()[0]
            records = {record.id: record for record in self.memory_coordinator.records()}
            targets = tuple(
                record for target in proposal.target_ids if (record := records.get(target))
            )
            decision = await self.push_screen_wait(MemoryReviewScreen(proposal, targets))
            if decision is None:
                return
            await self._apply_memory_decision(decision)

    async def _apply_memory_decision(self, decision: MemoryDecision) -> None:
        assert self.memory_coordinator is not None
        result = await self.memory_coordinator.apply(decision)
        if result.changed:
            self.runner.update_long_term_memory(result.prompt.content)
        for warning in result.warnings:
            await self._append_notice(warning, "error")

    def action_memory(self) -> None:
        self.open_memory()

    @work(exclusive=True, group="memory-panel")
    async def open_memory(self) -> None:
        if self.memory_coordinator is None:
            await self._append_notice(
                "长期记忆未启用；请在 ~/.kcode/config.yaml 设置 memory.enabled: true。",
                "system",
            )
            return
        if self.generating:
            await self._append_notice("Agent 正在工作，请在本轮完成后打开长期记忆。", "system")
            return
        while True:
            action = await self.push_screen_wait(
                MemoryScreen(
                    self.memory_coordinator.records(),
                    self.memory_coordinator.pending(),
                    self.memory_coordinator.warnings,
                )
            )
            if action is None:
                return
            if action.kind == "review" and action.item_id is not None:
                proposal = next(
                    item for item in self.memory_coordinator.pending() if item.id == action.item_id
                )
                records = {record.id: record for record in self.memory_coordinator.records()}
                targets = tuple(
                    record for target in proposal.target_ids if (record := records.get(target))
                )
                decision = await self.push_screen_wait(MemoryReviewScreen(proposal, targets))
                if decision is not None:
                    await self._apply_memory_decision(decision)
            elif action.kind == "toggle" and action.scope is not None and action.item_id:
                record = next(
                    item for item in self.memory_coordinator.records() if item.id == action.item_id
                )
                status = (
                    MemoryStatus.INACTIVE
                    if record.status == MemoryStatus.ACTIVE
                    else MemoryStatus.ACTIVE
                )
                result = await self.memory_coordinator.set_status(
                    action.scope,
                    action.item_id,
                    status,
                )
                if result.changed:
                    self.runner.update_long_term_memory(result.prompt.content)
                for warning in result.warnings:
                    await self._append_notice(warning, "error")
            elif action.kind == "delete" and action.scope is not None and action.item_id:
                record = next(
                    item for item in self.memory_coordinator.records() if item.id == action.item_id
                )
                if await self.push_screen_wait(MemoryDeleteScreen(record)):
                    result = await self.memory_coordinator.delete(action.scope, action.item_id)
                    if result.changed:
                        self.runner.update_long_term_memory(result.prompt.content)
                    for warning in result.warnings:
                        await self._append_notice(warning, "error")
            elif action.kind == "edit" and action.scope is not None and action.item_id:
                record = next(
                    item for item in self.memory_coordinator.records() if item.id == action.item_id
                )
                values = await self.push_screen_wait(MemoryEditScreen(record))
                if values is not None:
                    title, summary, application, body = values
                    result = await self.memory_coordinator.edit_record(
                        action.scope,
                        action.item_id,
                        title=title,
                        summary=summary,
                        application=application,
                        body=body,
                    )
                    if result.changed:
                        self.runner.update_long_term_memory(result.prompt.content)
                    for warning in result.warnings:
                        await self._append_notice(warning, "error")

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
            await self._end_hook_session("resume")
            result = await self.coordinator.resume(
                selected,
                tools=self.runner.tool_definitions(self.session.permission_mode),
            )
            skill_warnings = self.runner.bind_session(result.runtime)
            self.conversation = result.runtime.conversation
            await self._start_hook_session("resume")
            await self._redraw_resumed_session(result)
            for warning in skill_warnings:
                await self._append_notice(warning, "system")
        except Exception as exc:
            if self._hook_session_ended:
                await self._start_hook_session("resume-failed")
            await self._append_notice(f"恢复会话失败：{exc}", "error")
        finally:
            self._set_agent_status()
            self._set_generating(False)
            if self.memory_coordinator is not None and self.memory_coordinator.pending():
                self.review_memories()

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
        self._main_request_usage = TokenUsage()
        self._subagent_request_usage = TokenUsage(0, 0, 0, 0, 0)
        self._session_usage = TokenUsage(0, 0, 0, 0, 0)
        self._set_agent_status()

    @work(exclusive=True, group="generation")
    async def generate_response(
        self,
        user_text: str,
        response: AssistantResponse,
        *,
        event_source=None,
    ) -> None:
        answer = ""
        thinking = ""
        last_refresh = 0.0
        current_response = response
        try:
            events = event_source or self.runner.run(user_text, self.session)
            async for event in events:
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
                    self._main_request_usage = event.cumulative
                    self._usage = event.cumulative.plus(self._subagent_request_usage)
                    self._session_usage = self._session_usage.plus(event.request)
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
            if self.memory_coordinator is not None and self.memory_coordinator.pending():
                self.review_memories()

    async def action_interrupt(self) -> None:
        if self.generating and self._generation_worker is not None:
            if self.skill_executor.active_runner is not None:
                self.skill_executor.cancel()
            else:
                self.runner.cancel()
            if isinstance(self.screen, ApprovalScreen):
                self.screen.dismiss(None)
        else:
            self.exit()

    def action_cycle_permissions(self) -> None:
        self.session.cycle_mode()
        self._set_permission_status()
        self._set_agent_status()
