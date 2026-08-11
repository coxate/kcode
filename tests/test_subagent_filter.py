from pathlib import Path

from kcode.subagents.filter import CONTROL_TOOL_NAMES, defined_registry, fork_registry
from kcode.subagents.models import AgentMeta
from kcode.tools.base import ToolArguments, ToolContext, ToolSpec
from kcode.tools.registry import create_default_registry


async def test_defined_filter_and_fork_denial() -> None:
    registry = create_default_registry()
    meta = AgentMeta(
        name="worker",
        description="worker",
        tools=("read_file", "write_file", "mcp__mail"),
        disallowed_tools=("write_file",),
    )
    defined = defined_registry(registry, meta, background=True)
    assert defined.names() == {"read_file"}

    class Control:
        spec = ToolSpec("agent", "delegate", ToolArguments)

        async def execute(self, arguments, context):
            raise AssertionError

    registry.register(Control())
    forked = fork_registry(registry)
    assert forked.names() == registry.names()
    tool = forked.get("agent")
    result = await tool.execute(tool.spec.arguments_model(), ToolContext(Path.cwd()))
    assert result.status == "denied"
    assert result.error.code == "nested_subagent_disabled"
    assert "agent" in CONTROL_TOOL_NAMES
