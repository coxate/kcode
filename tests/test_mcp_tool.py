import asyncio
from pathlib import Path

from mcp import types

from kcode.mcp.tool import McpTool, McpToolArguments, effect_from_annotations
from kcode.tools.base import ToolContext, ToolEffect
from kcode.tools.registry import ToolRegistry


class Handle:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return self.result


def tool(handle: Handle) -> McpTool:
    return McpTool(
        name="mcp__demo__echo",
        remote_name="echo",
        description="Echo",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}},
        effect=ToolEffect.READ_ONLY,
        handle=handle,
        server_name="demo",
    )


async def test_tool_preserves_schema_arguments_and_text_order(tmp_path: Path) -> None:
    handle = Handle(
        types.CallToolResult(
            content=[
                types.TextContent(type="text", text="one"),
                types.TextContent(type="text", text="two"),
            ]
        )
    )
    wrapped = tool(handle)
    result = await wrapped.execute(McpToolArguments(value="x"), ToolContext(tmp_path))
    assert wrapped.spec.parameters["properties"]["value"]["type"] == "string"
    assert handle.calls == [("echo", {"value": "x"})]
    assert result.data == {"content": "onetwo"}


async def test_tool_maps_remote_and_transport_errors(tmp_path: Path) -> None:
    remote = tool(
        Handle(
            types.CallToolResult(
                content=[types.TextContent(type="text", text="bad request")],
                isError=True,
            )
        )
    )
    remote_result = await remote.execute(McpToolArguments(), ToolContext(tmp_path))
    assert remote_result.error.code == "mcp_tool_error"

    failed = tool(Handle(error=RuntimeError("secret failure")))
    failed_result = await failed.execute(McpToolArguments(), ToolContext(tmp_path))
    assert failed_result.error.code == "mcp_transport_error"

    timed_out = tool(Handle(error=asyncio.TimeoutError()))
    timeout_result = await timed_out.execute(McpToolArguments(), ToolContext(tmp_path))
    assert timeout_result.status == "timeout"


async def test_non_text_warning_is_emitted_once(tmp_path: Path) -> None:
    handle = Handle(
        types.CallToolResult(
            content=[types.ImageContent(type="image", data="AA==", mimeType="image/png")]
        )
    )
    wrapped = tool(handle)
    first = await wrapped.execute(McpToolArguments(), ToolContext(tmp_path))
    second = await wrapped.execute(McpToolArguments(), ToolContext(tmp_path))
    assert len(first.warnings) == 1
    assert second.warnings == ()


def test_read_only_requires_explicit_true() -> None:
    base = {"name": "echo", "inputSchema": {"type": "object"}}
    assert effect_from_annotations(types.Tool(**base)) == ToolEffect.SIDE_EFFECT
    assert (
        effect_from_annotations(
            types.Tool(**base, annotations=types.ToolAnnotations(readOnlyHint=False))
        )
        == ToolEffect.SIDE_EFFECT
    )
    assert (
        effect_from_annotations(
            types.Tool(**base, annotations=types.ToolAnnotations(readOnlyHint=True))
        )
        == ToolEffect.READ_ONLY
    )


def test_empty_remote_schema_is_not_replaced_by_local_model() -> None:
    wrapped = McpTool(
        name="mcp__demo__empty",
        remote_name="empty",
        description="Empty schema",
        parameters={},
        effect=ToolEffect.READ_ONLY,
        handle=Handle(types.CallToolResult(content=[])),
        server_name="demo",
    )
    registry = ToolRegistry()
    registry.register(wrapped)
    assert registry.definitions()[0].parameters == {}
