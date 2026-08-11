from __future__ import annotations

from kcode.config import SubAgentConfig
from kcode.subagents.catalog import AgentCatalog
from kcode.subagents.factory import SubAgentFactory
from kcode.subagents.manager import LaunchResult, TaskManager
from kcode.tools.base import ToolResult

FORK_BOILERPLATE = """<fork-subagent>
Work only on the delegated task. Use the inherited context as evidence, do not start or control
other SubAgents, and return a concise result with scope, findings, changes, and verification.
</fork-subagent>
"""


class SubAgentService:
    def __init__(
        self,
        catalog: AgentCatalog,
        factory: SubAgentFactory,
        manager: TaskManager,
        parent,
        config: SubAgentConfig,
    ) -> None:
        self.catalog = catalog
        self.factory = factory
        self.manager = manager
        self.parent = parent
        self.config = config

    def set_catalog(self, catalog: AgentCatalog) -> None:
        self.catalog = catalog

    @property
    def current_mode(self):
        snapshot = self.parent.delegation_snapshot
        if snapshot is None:
            raise RuntimeError("The parent Agent has no active delegation context.")
        return snapshot.mode

    async def invoke(
        self,
        *,
        prompt: str,
        description: str,
        subagent_type: str | None,
        run_in_background: bool,
        name: str | None,
    ) -> ToolResult:
        if not self.config.enabled:
            return ToolResult.failure("subagents_disabled", "SubAgents are disabled.")
        if subagent_type is None:
            if not self.config.background_enabled:
                return ToolResult.failure(
                    "background_disabled",
                    "Fork SubAgents require background tasks to be enabled.",
                )
            try:
                child = self.factory.fork(
                    self.parent,
                    self.current_mode,
                    self.parent.approve,
                )
                launched = await self.manager.launch(
                    child,
                    f"{FORK_BOILERPLATE}\n{prompt}",
                    name or description,
                    background=True,
                )
            except (RuntimeError, ValueError) as exc:
                return ToolResult.failure("subagent_launch_failed", str(exc))
            return self._launch_result(launched)

        definition, warnings = self.catalog.resolve(subagent_type)
        if definition is None:
            return ToolResult.failure(
                "unknown_subagent",
                f"Unknown or unavailable Agent: {subagent_type}",
            )
        background = run_in_background or definition.meta.background
        if background and not self.config.background_enabled:
            return ToolResult.failure(
                "background_disabled",
                "Background SubAgents are disabled.",
            )
        try:
            child = self.factory.defined(
                definition,
                self.parent,
                self.current_mode,
                self.parent.approve,
                background=background,
            )
            launched = await self.manager.launch(
                child,
                prompt,
                name or description,
                background=background,
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            return ToolResult.failure("subagent_launch_failed", str(exc))
        result = self._launch_result(launched)
        if warnings:
            return ToolResult(
                result.status,
                result.data,
                result.error,
                result.duration_ms,
                result.truncated,
                warnings,
            )
        return result

    async def launch_hook(
        self,
        *,
        prompt: str,
        subagent_type: str,
        name: str | None,
        mode,
    ) -> LaunchResult:
        if not self.config.enabled or not self.config.background_enabled:
            raise RuntimeError("Background SubAgents are disabled.")
        definition, _ = self.catalog.resolve(subagent_type)
        if definition is None:
            raise ValueError(f"Unknown or unavailable Agent: {subagent_type}")
        child = self.factory.defined(
            definition,
            self.parent,
            mode,
            self.parent.approve,
            background=True,
        )
        return await self.manager.launch(
            child,
            prompt,
            name or definition.meta.name,
            background=True,
        )

    @staticmethod
    def _launch_result(result: LaunchResult) -> ToolResult:
        return ToolResult.success(
            {
                "task_id": result.task_id,
                "status": result.status,
                "result": result.result,
            },
            warnings=result.warnings,
        )

    def list_tasks(self) -> ToolResult:
        return ToolResult.success({"tasks": list(self.manager.summaries())})

    def get_task(self, task_id: str) -> ToolResult:
        record = self.manager.get(task_id)
        if record is None:
            return ToolResult.failure("unknown_task", f"Unknown task: {task_id}")
        return ToolResult.success(
            {
                "task_id": record.id,
                "name": record.name,
                "status": record.status.value,
                "result": record.result,
                "error": record.error,
                "tokens": record.usage.total_tokens,
            }
        )

    def stop_task(self, task_id: str) -> ToolResult:
        if not self.manager.stop(task_id):
            return ToolResult.failure(
                "task_not_running",
                f"Task is missing or already stopped: {task_id}",
            )
        return ToolResult.success({"task_id": task_id, "status": "cancellation_requested"})

    async def send_message(self, task_id: str, message: str) -> ToolResult:
        try:
            launched = await self.manager.send_message(task_id, message)
        except (RuntimeError, ValueError) as exc:
            return ToolResult.failure("task_send_failed", str(exc))
        return self._launch_result(launched)
