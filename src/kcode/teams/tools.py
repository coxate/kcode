from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from kcode.teams.models import TeamCaller, TeamError, TeamTaskStatus
from kcode.teams.rendering import MAX_TEAM_BYTES, truncate
from kcode.tools.base import ToolArguments, ToolContext, ToolEffect, ToolResult, ToolSpec
from kcode.tools.registry import ToolRegistry


class EmptyArgs(ToolArguments):
    pass


class TeamCreateArgs(ToolArguments):
    name: str = Field(min_length=1, max_length=64)
    goal: str = Field(min_length=1, max_length=32 * 1024)


class TeamSpawnArgs(ToolArguments):
    name: str = Field(min_length=1, max_length=64)
    prompt: str = Field(min_length=1, max_length=32 * 1024)
    subagent_type: str = Field(default="general-purpose", min_length=1, max_length=64)
    isolation: Literal["shared", "worktree"] = "worktree"


class TeamMemberArgs(ToolArguments):
    name: str = Field(min_length=1, max_length=64)


class TeamMessageArgs(ToolArguments):
    to: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=32 * 1024)


class TeamTaskCreateArgs(ToolArguments):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=32 * 1024)
    assignee: str | None = Field(default=None, max_length=64)
    blocked_by: tuple[str, ...] = ()


class TeamTaskListArgs(ToolArguments):
    status: TeamTaskStatus | None = None


class TeamTaskUpdateArgs(ToolArguments):
    task_id: str = Field(pattern=r"^team-task-[a-f0-9]{12}$")
    status: TeamTaskStatus | None = None
    assignee: str | None = Field(default=None, max_length=64)
    add_blocked_by: tuple[str, ...] = ()
    remove_blocked_by: tuple[str, ...] = ()


class TeamTool:
    def __init__(self, manager, caller: TeamCaller, spec: ToolSpec, method: str) -> None:
        self.manager = manager
        self.caller = caller
        self.spec = spec
        self.method = method

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        values = arguments.model_dump(exclude_none=True)
        try:
            result = await getattr(self.manager, self.method)(self.caller, **values)
        except TeamError as exc:
            return ToolResult.failure(exc.code, str(exc))
        encoded = json.dumps(result.data, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_TEAM_BYTES:
            content, _ = truncate(encoded, MAX_TEAM_BYTES - 256)
            return ToolResult.success(
                {"truncated": True, "content": content},
                warnings=result.warnings,
                truncated=True,
            )
        return ToolResult.success(result.data, warnings=result.warnings)


def _spec(name: str, description: str, model, *, read_only: bool = False) -> ToolSpec:
    return ToolSpec(
        name,
        description,
        model,
        ToolEffect.READ_ONLY if read_only else ToolEffect.SIDE_EFFECT,
        always_visible=True,
        self_managed_timeout=True,
    )


SPECS = (
    (_spec("team_create", "Create the single in-process Agent Team.", TeamCreateArgs), "create"),
    (_spec("team_spawn", "Spawn a named Team member.", TeamSpawnArgs), "spawn"),
    (_spec("team_status", "Inspect the active Team.", EmptyArgs, read_only=True), "status"),
    (_spec("team_stop", "Stop one Team member safely.", TeamMemberArgs), "stop"),
    (_spec("team_delete", "Delete inactive Team coordination state.", EmptyArgs), "delete"),
    (_spec("team_send_message", "Send a Team message.", TeamMessageArgs), "send_message"),
    (_spec("team_task_create", "Create a shared Team task.", TeamTaskCreateArgs), "task_create"),
    (
        _spec("team_task_list", "List shared Team tasks.", TeamTaskListArgs, read_only=True),
        "task_list",
    ),
    (_spec("team_task_update", "Update a shared Team task.", TeamTaskUpdateArgs), "task_update"),
)


def lead_tools(manager) -> tuple[TeamTool, ...]:
    return tuple(TeamTool(manager, TeamCaller.lead(), spec, method) for spec, method in SPECS)


def member_tools(manager, caller: TeamCaller) -> tuple[TeamTool, ...]:
    allowed = {"team_send_message", "team_task_create", "team_task_list", "team_task_update"}
    return tuple(
        TeamTool(manager, caller, spec, method) for spec, method in SPECS if spec.name in allowed
    )


def register_team_tools(registry: ToolRegistry, manager) -> None:
    for tool in lead_tools(manager):
        registry.register(tool)
