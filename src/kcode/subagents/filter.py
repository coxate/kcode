from __future__ import annotations

from kcode.subagents.models import AgentMeta
from kcode.tools.base import ToolArguments, ToolContext, ToolResult
from kcode.tools.registry import ToolRegistry

CONTROL_TOOL_NAMES = {
    "agent",
    "task_list",
    "task_get",
    "task_stop",
    "task_send_message",
}
BACKGROUND_BASE_TOOLS = {
    "read_file",
    "write_file",
    "edit_file",
    "run_command",
    "find_files",
    "search_code",
    "load_skill",
}


class DeniedControlTool:
    def __init__(self, tool) -> None:
        self.spec = tool.spec

    async def execute(self, arguments: ToolArguments, context: ToolContext) -> ToolResult:
        return ToolResult.failure(
            "nested_subagent_disabled",
            "A SubAgent cannot create or control another SubAgent task.",
            status="denied",
            details={"tool": self.spec.name},
        )


def defined_registry(parent: ToolRegistry, meta: AgentMeta, *, background: bool) -> ToolRegistry:
    allowed = parent.names() - CONTROL_TOOL_NAMES
    if meta.tools:
        allowed &= set(meta.tools) | {"load_skill"}
    allowed -= set(meta.disallowed_tools)
    if background:
        explicit_mcp = {name for name in meta.tools if name.startswith("mcp__")}
        allowed &= BACKGROUND_BASE_TOOLS | explicit_mcp
    registry = ToolRegistry()
    for tool in parent.tools():
        name = tool.spec.name
        if name == "load_skill" or name not in allowed:
            continue
        registry.register(tool)
    return registry


def fork_registry(parent: ToolRegistry) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in parent.tools():
        if tool.spec.name == "load_skill":
            continue
        registry.register(DeniedControlTool(tool) if tool.spec.name in CONTROL_TOOL_NAMES else tool)
    return registry


def skill_fork_registry(parent: ToolRegistry, allowed_tools: tuple[str, ...]) -> ToolRegistry:
    allowed = set(allowed_tools) if allowed_tools else parent.names()
    registry = ToolRegistry()
    for tool in parent.tools():
        name = tool.spec.name
        if name in CONTROL_TOOL_NAMES or name == "load_skill":
            continue
        if name in allowed or tool.spec.always_visible:
            registry.register(tool)
    return registry
