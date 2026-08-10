from __future__ import annotations

from pydantic import Field

from kcode.skills.runtime import SkillRuntime
from kcode.tools.base import ToolArguments, ToolContext, ToolEffect, ToolResult, ToolSpec


class LoadSkillArgs(ToolArguments):
    name: str = Field(min_length=1, max_length=32)


class LoadSkillTool:
    def __init__(self, runtime: SkillRuntime) -> None:
        self.runtime = runtime

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            "load_skill",
            "Load a listed Skill workflow into the active conversation context.",
            LoadSkillArgs,
            ToolEffect.READ_ONLY,
            always_visible=True,
        )

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        del context
        assert isinstance(arguments, LoadSkillArgs)
        result = await self.runtime.activate(arguments.name)
        if not result.ok:
            return ToolResult.failure(
                result.error_code or "skill_activation_failed",
                result.error_message or "Skill activation failed.",
                details={"name": result.name, "active": list(result.active_names)},
            )
        return ToolResult.success(
            {"name": result.name, "active": list(result.active_names)},
            warnings=result.warnings,
        )
