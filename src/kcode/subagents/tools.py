from __future__ import annotations

from typing import cast

from pydantic import Field, field_validator

from kcode.subagents.service import SubAgentService
from kcode.tools.base import (
    ToolArguments,
    ToolContext,
    ToolEffect,
    ToolResult,
    ToolSpec,
)
from kcode.tools.registry import ToolRegistry


class AgentArgs(ToolArguments):
    prompt: str = Field(min_length=1, max_length=32 * 1024)
    description: str = Field(min_length=1, max_length=200)
    subagent_type: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$",
        max_length=64,
    )
    run_in_background: bool = False
    name: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("description", "name")
    @classmethod
    def single_line(cls, value: str | None) -> str | None:
        if value is not None and (value != value.strip() or "\n" in value or "\r" in value):
            raise ValueError("value must be a trimmed single line")
        return value


class TaskIdArgs(ToolArguments):
    task_id: str = Field(pattern=r"^task-[a-f0-9]{12}$")


class TaskSendMessageArgs(TaskIdArgs):
    message: str = Field(min_length=1, max_length=32 * 1024)


class EmptyArgs(ToolArguments):
    pass


class AgentTool:
    spec = ToolSpec(
        "agent",
        "Delegate an independent task. Set subagent_type for a defined role; omit it to fork "
        "the current context. Forks always run in the background.",
        AgentArgs,
        ToolEffect.READ_ONLY,
        always_visible=True,
        self_managed_timeout=True,
    )

    def __init__(self, service: SubAgentService) -> None:
        self.service = service

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        args = cast(AgentArgs, arguments)
        return await self.service.invoke(
            prompt=args.prompt,
            description=args.description,
            subagent_type=args.subagent_type,
            run_in_background=args.run_in_background,
            name=args.name,
        )


class TaskListTool:
    spec = ToolSpec(
        "task_list",
        "List SubAgent tasks retained in the current Kcode process.",
        EmptyArgs,
        always_visible=True,
    )

    def __init__(self, service: SubAgentService) -> None:
        self.service = service

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return self.service.list_tasks()


class TaskGetTool:
    spec = ToolSpec(
        "task_get",
        "Get the current status and retained result of one SubAgent task.",
        TaskIdArgs,
        always_visible=True,
    )

    def __init__(self, service: SubAgentService) -> None:
        self.service = service

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return self.service.get_task(cast(TaskIdArgs, arguments).task_id)


class TaskStopTool:
    spec = ToolSpec(
        "task_stop",
        "Request cancellation of a running SubAgent task.",
        TaskIdArgs,
        always_visible=True,
    )

    def __init__(self, service: SubAgentService) -> None:
        self.service = service

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return self.service.stop_task(cast(TaskIdArgs, arguments).task_id)


class TaskSendMessageTool:
    spec = ToolSpec(
        "task_send_message",
        "Send another task to a retained completed SubAgent using its existing context.",
        TaskSendMessageArgs,
        always_visible=True,
    )

    def __init__(self, service: SubAgentService) -> None:
        self.service = service

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        args = cast(TaskSendMessageArgs, arguments)
        return await self.service.send_message(args.task_id, args.message)


def register_subagent_tools(registry: ToolRegistry, service: SubAgentService) -> None:
    for tool in (
        AgentTool(service),
        TaskListTool(service),
        TaskGetTool(service),
        TaskStopTool(service),
        TaskSendMessageTool(service),
    ):
        registry.register(tool)
