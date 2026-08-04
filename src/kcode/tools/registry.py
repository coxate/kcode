from __future__ import annotations

from kcode.tools.base import Tool, ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        name = tool.spec.name.strip()
        if not name:
            raise ValueError("Tool name cannot be empty.")
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._tools[name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                tool.spec.name,
                tool.spec.description,
                tool.spec.arguments_model.model_json_schema(),
            )
            for tool in self._tools.values()
        )

    def __len__(self) -> int:
        return len(self._tools)


def create_default_registry() -> ToolRegistry:
    from kcode.tools.command import RunCommandTool
    from kcode.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool
    from kcode.tools.search import FindFilesTool, SearchCodeTool

    registry = ToolRegistry()
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        RunCommandTool(),
        FindFilesTool(),
        SearchCodeTool(),
    ):
        registry.register(tool)
    return registry
